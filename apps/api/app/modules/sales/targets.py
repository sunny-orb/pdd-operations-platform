"""渠道月GMV目标规范化与不可变发布；完成额只在既有数仓事实查询派生。"""

import hashlib
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.modules.archive_store import require_archive, write_archive

from .contracts import PublishResult, ReadWindow, SalesError, ShopDailyCandidate
from .normalization import canonical as raw_canonical
from .source_policy import APPROVED_START, FISCAL_END, SHOPS
from .storage import ShopDailyStore

TARGET_BASE = "XGzTbd9xJaASV3soYnycMmf0nY3"
TARGET_TABLE = "tblwEjKpt1k51M3j"
TARGET_URL = f"https://kocotree.feishu.cn/base/{TARGET_BASE}?table={TARGET_TABLE}"
TARGET_FIELDS = {
    "fldGdr3oOJ": ("平台", "text"),
    "fld5tezqp3": ("年份", "text"),
    "fldnBczHp9": ("月份", "text"),
    "fldZq41Hnj": ("gmv目标", "number"),
}
TARGET_REVISION = "m3-original-gmv-targets-v2"
BASIS = {"original_gmv": ("gmv目标", "原版 GMV 目标")}
MEMBERS = tuple(value[0] for value in SHOPS.values())


def canonical(value):
    def dates(item):
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, dict):
            return {key: dates(part) for key, part in item.items()}
        if isinstance(item, (tuple, list)):
            return [dates(part) for part in item]
        return item

    return raw_canonical(dates(value))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def target_window():
    return ReadWindow(APPROVED_START, FISCAL_END, tuple(SHOPS))


def _selection(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise SalesError("TARGET_LABEL_AMBIGUOUS")
    return value


def normalize_targets(rows, *, basis="original_gmv", source_metadata=None):
    """输入原版四字段/真实record_id；输出财年内唯一月目标（元），不分摊到店。"""
    if basis not in BASIS:
        raise SalesError("INVALID_TARGET_BASIS")
    raw, output, seen, months = [], [], set(), set()
    selected, label = BASIS[basis]
    with localcontext() as context:
        context.prec = 80
        for row in rows:
            original = {
                "record_id": row.get("record_id"),
                **{name: row.get(name) for name, _kind in TARGET_FIELDS.values()},
            }
            raw.append(original)
            key = original["record_id"]
            if not isinstance(key, str) or not key.strip() or key in seen:
                raise SalesError("TARGET_SOURCE_ID_INVALID")
            seen.add(key)
            if _selection(original["平台"]) != "拼多多":
                raise SalesError("TARGET_CHANNEL_NOT_APPROVED")
            year, month = _selection(original["年份"]), _selection(original["月份"])
            if not re.fullmatch(r"\d{4}年", year) or not re.fullmatch(r"(?:[1-9]|1[0-2])月", month):
                raise SalesError("TARGET_PERIOD_INVALID")
            start = date(int(year[:-1]), int(month[:-1]), 1)
            if start in months:
                raise SalesError("TARGET_MONTH_CONFLICT")
            months.add(start)
            if not APPROVED_START <= start <= FISCAL_END:
                continue
            try:
                value = original[selected]
                if isinstance(value, bool) or value is None:
                    raise ValueError()
                amount = Decimal(str(value))
                if (
                    not amount.is_finite()
                    or amount < 0
                    or amount != amount.quantize(Decimal(".01"))
                ):
                    raise ValueError()
                amount = amount.quantize(Decimal(".01"))
            except (ValueError, InvalidOperation) as exc:
                raise SalesError("TARGET_AMOUNT_INVALID") from exc
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            output.append(
                dict(
                    source_row_key=key,
                    source_target_key=key,
                    source_scope_label=f"拼多多 · {label}（固定六店渠道范围）",
                    source_period_label=f"{year}/{month}",
                    period_kind="month",
                    period_start=start,
                    period_end=end,
                    period_status="confirmed",
                    target_amount=amount,
                    scope_status="complete",
                    evidence_note=f"原版目标表；{selected}原值单位元；六店范围沿原型/SHOPS，不拆分渠道目标",
                )
            )
    if not output:
        raise SalesError("TARGETS_EMPTY")
    output.sort(key=lambda r: (r["period_start"], r["source_row_key"]))
    raw.sort(key=lambda r: r["record_id"])
    digest = fingerprint(dict(revision=TARGET_REVISION, basis=basis, rows=output, members=MEMBERS))
    return ShopDailyCandidate(target_window(), tuple(output), tuple(raw), digest, source_metadata)


def _producer():
    root = Path(__file__).resolve().parents[2]
    return fingerprint(
        {
            name: (root / name).read_text()
            for name in (
                "modules/sales/targets.py",
                "connectors/sales_targets.py",
            )
        }
    )


class SalesTargetStore(ShopDailyStore):
    """复用既有任务锁/租约/失败恢复；短事务只写目标、成员及发布支撑表。"""

    def __init__(self, engine, *, archive_root, basis="original_gmv"):
        if basis != "original_gmv":
            raise SalesError("TARGET_BASIS_NOT_APPROVED")
        self.engine, self.archive_root, self.basis = engine, Path(archive_root), basis
        self.code, self.approved = "sales_targets", target_window()
        self.loaded_producer = _producer()
        m = sa.MetaData()
        self.tables = {
            name: sa.Table(name, m, schema="public", autoload_with=engine, resolve_fks=False)
            for name in (
                "shops",
                "sales_targets",
                "sales_target_shops",
                "dataset_versions",
                "task_runs",
                "current_datasets",
            )
        }

    def run(self, receive):
        def checked(heartbeat):
            candidate = receive(heartbeat)
            source = candidate.source_metadata or {}
            if (
                source.get("stable_full_scope") is not True
                or source.get("base_token") != TARGET_BASE
                or source.get("table_id") != TARGET_TABLE
                or source.get("timezone") != "Asia/Shanghai"
                or source.get("rev") is None
            ):
                raise SalesError("TARGET_STABLE_SCOPE_UNPROVEN")
            replay = normalize_targets(candidate.raw_rows, basis=self.basis, source_metadata=source)
            if replay.rows != candidate.rows or replay.fingerprint != candidate.fingerprint:
                raise SalesError("TARGET_CANDIDATE_MISMATCH")
            if _producer() != self.loaded_producer:
                raise SalesError("PRODUCER_CODE_CHANGED")
            return candidate

        return super().run(checked)

    def _archive_target(self, version, candidate, heartbeat):
        self.archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.archive_root.chmod(0o700)
        folder = self.archive_root / version
        folder.mkdir(mode=0o700)

        def serialize(rows):
            lines = []
            for row in rows:
                heartbeat()
                lines.append(canonical(row))
            return ("\n".join(lines) + "\n").encode()

        raw = serialize(candidate.raw_rows)
        normalized = serialize(candidate.rows)
        interpretation = canonical(
            dict(
                revision=TARGET_REVISION,
                basis=self.basis,
                source=TARGET_URL,
                fields=TARGET_FIELDS,
                metadata=candidate.source_metadata,
                member_mapping=SHOPS,
                units="来源及规范均为人民币元",
                fiscal_start=APPROVED_START,
                fiscal_end=FISCAL_END,
                excluded_periods=sorted(
                    {f"{r['年份']}/{r['月份']}" for r in candidate.raw_rows}
                    - {r["source_period_label"] for r in candidate.rows}
                ),
                fingerprint=candidate.fingerprint,
                producer_revision=self.loaded_producer,
                input_sha256=hashlib.sha256(raw).hexdigest(),
                normalized_sha256=hashlib.sha256(normalized).hexdigest(),
            )
        ).encode()
        for name, content in (
            ("input.ndjson", raw),
            ("normalized.ndjson", normalized),
            ("interpretation.json", interpretation),
        ):
            heartbeat()
            write_archive(folder / name, content)
            if (folder / name).read_bytes() != content:
                raise SalesError("ARCHIVE_CHECKSUM_MISMATCH")
        return str(folder / "input.ndjson"), str(folder / "interpretation.json")

    def _validate_stored(self, c, version, candidate):
        heads, members = self.tables["sales_targets"], self.tables["sales_target_shops"]
        actual = (
            c.execute(sa.select(heads).where(heads.c.dataset_version_id == version))
            .mappings()
            .all()
        )
        wanted = {r["source_row_key"]: r for r in candidate.rows}
        if len(actual) != len(wanted) or any(
            row["source_row_key"] not in wanted
            or any(row[key] != value for key, value in wanted[row["source_row_key"]].items())
            for row in actual
        ):
            raise SalesError("TARGET_DATABASE_REPLAY_MISMATCH")
        stored_members = set(
            c.execute(
                sa.select(members.c.target_id, members.c.shop_id).where(
                    members.c.target_id.in_([r["id"] for r in actual])
                )
            ).all()
        )
        if stored_members != {(r["id"], member) for r in actual for member in MEMBERS}:
            raise SalesError("TARGET_MEMBERS_MISMATCH")

    def _publish(self, c, run_id, worker, candidate, heartbeat):
        if _producer() != self.loaded_producer:
            raise SalesError("PRODUCER_CODE_CHANGED")
        version = str(uuid4())
        versions, current, tasks = (
            self.tables[n] for n in ("dataset_versions", "current_datasets", "task_runs")
        )
        digest = fingerprint(dict(content=candidate.fingerprint, producer=self.loaded_producer))
        with c.begin():
            previous = c.execute(
                sa.select(versions.c.retained_input_ref, versions.c.interpretation_ref).where(
                    versions.c.dataset_code == "sales_targets",
                    versions.c.content_fingerprint == digest,
                    versions.c.state == "published",
                )
            ).first()
        if previous:
            archive, interpretation = (
                require_archive(previous.retained_input_ref),
                require_archive(previous.interpretation_ref),
            )
        else:
            archive, interpretation = self._archive_target(version, candidate, heartbeat)
        with c.begin():
            heartbeat(force=True)
            existing_members = set(
                c.execute(
                    sa.select(self.tables["shops"].c.id).where(
                        self.tables["shops"].c.id.in_(MEMBERS)
                    )
                ).scalars()
            )
            if existing_members != set(MEMBERS):
                raise SalesError("TARGET_SHOPS_NOT_CONNECTED")
            reused = c.execute(
                sa.select(versions.c.id).where(
                    versions.c.dataset_code == self.code,
                    versions.c.state == "published",
                    versions.c.content_fingerprint == digest,
                )
            ).scalar_one_or_none()
            if reused:
                version = reused
            else:
                c.execute(
                    versions.insert().values(
                        id=version,
                        dataset_code=self.code,
                        origin_kind="source",
                        state="draft",
                        source_system="feishu",
                        source_locator=TARGET_URL,
                        row_schema_revision=f"{TARGET_REVISION}:{self.basis}",
                        interpretation_ref=interpretation,
                        producer_revision=self.loaded_producer,
                        retained_input_ref=archive,
                        upstream_revision=str(candidate.source_metadata["rev"]),
                        received_at=sa.func.clock_timestamp(),
                    )
                )
                heads = []
                for row in candidate.rows:
                    heartbeat()
                    heads.append(
                        dict(
                            row,
                            id=str(uuid5(NAMESPACE_URL, f"{version}/{row['source_row_key']}")),
                            dataset_version_id=version,
                        )
                    )
                c.execute(self.tables["sales_targets"].insert(), heads)
                c.execute(
                    self.tables["sales_target_shops"].insert(),
                    [
                        dict(
                            target_id=head["id"],
                            shop_id=member,
                            evidence_note="渠道六店范围沿原型及已核SHOPS固定；非目标金额分摊",
                        )
                        for head in heads
                        for member in MEMBERS
                    ],
                )
            self._validate_stored(c, version, candidate)
            if not reused:
                c.execute(
                    versions.update()
                    .where(versions.c.id == version)
                    .values(
                        state="validated",
                        content_fingerprint=digest,
                        validation_evidence_ref=interpretation,
                    )
                )
                c.execute(
                    versions.update().where(versions.c.id == version).values(state="published")
                )
            heartbeat(force=True)
            c.execute(
                insert(current)
                .values(dataset_code=self.code, version_id=version)
                .on_conflict_do_update(
                    index_elements=[current.c.dataset_code],
                    set_={"version_id": version},
                )
            )
            finished = c.execute(
                tasks.update()
                .where(
                    tasks.c.id == run_id,
                    tasks.c.status == "running",
                    tasks.c.worker_ref == worker,
                    tasks.c.lease_expires_at > sa.func.clock_timestamp(),
                )
                .values(
                    status="succeeded",
                    result_version_id=version,
                    finished_at=sa.func.clock_timestamp(),
                    lease_expires_at=None,
                    evidence_ref=interpretation,
                )
            )
            if finished.rowcount != 1:
                raise SalesError("TASK_LEASE_LOST", 409)
        return PublishResult(run_id, version, bool(reused), "succeeded")


def run_sales_targets(engine, archive_root, basis="original_gmv"):
    """项目编排回调；已登记任务后才创建只读reader，只读取已批准原版目标源。"""

    def receive(heartbeat):
        from app.connectors.sales_targets import FeishuTargetReader

        root = Path(__file__).resolve().parents[5]
        return FeishuTargetReader(root=root, heartbeat=heartbeat).read_stable(basis=basis)

    return SalesTargetStore(engine, archive_root=archive_root, basis=basis).run(receive)


def query_targets(snapshot, start, cutoff, basis="original_gmv"):
    """固定RR版本；目标金额只取一次，按目标固定成员汇总数仓原值。"""
    from .query import number, ratio, total

    if basis != "original_gmv":
        raise SalesError("TARGET_BASIS_NOT_APPROVED")
    c, version = snapshot["connection"], snapshot["versions"].get("sales_targets")
    target_rows, members = [], {}
    if version:
        revision = c.execute(
            sa.text("SELECT row_schema_revision FROM public.dataset_versions WHERE id=:v"),
            dict(v=version),
        ).scalar_one()
        if revision != f"{TARGET_REVISION}:{basis}":
            raise SalesError("TARGET_DEFINITION_INCOMPATIBLE", 503)
        target_rows = (
            c.execute(
                sa.text(
                    "SELECT * FROM public.sales_targets WHERE dataset_version_id=:v "
                    "ORDER BY period_start,source_row_key"
                ),
                dict(v=version),
            )
            .mappings()
            .all()
        )
        for row in c.execute(
            sa.text(
                "SELECT m.target_id,m.shop_id FROM public.sales_target_shops m "
                "JOIN public.sales_targets t ON t.id=m.target_id WHERE t.dataset_version_id=:v"
            ),
            dict(v=version),
        ).mappings():
            members.setdefault(row["target_id"], set()).add(row["shop_id"])
    by_month = {}
    for row in target_rows:
        if (
            row["period_kind"] != "month"
            or row["period_status"] != "confirmed"
            or row["period_start"] in by_month
        ):
            raise SalesError("TARGET_PUBLICATION_INVALID", 503)
        by_month[row["period_start"]] = row
    expected = [date(2026, month, 1) for month in range(2, 13)] + [date(2027, 1, 1)]
    missing_months = [d.strftime("%Y-%m") for d in expected if d not in by_month]
    current = by_month.get(start)
    chosen = members.get(current["id"], set()) if current else set(MEMBERS)
    month_scope = bool(current and current["scope_status"] == "complete" and chosen == set(MEMBERS))
    annual_scope = not missing_months and all(
        by_month[d]["scope_status"] == "complete" and members.get(by_month[d]["id"]) == set(MEMBERS)
        for d in expected
    )

    def actual(lo, hi, shops):
        observed = [
            r
            for r in snapshot["rows"]
            if lo <= r["business_date"] <= hi
            and r["shop_id"] in shops
            and r["shop_revenue"] is not None
            and r["shop_revenue"] >= 0
        ]
        with localcontext() as context:
            context.prec = 80
            value = total(observed, "shop_revenue")
        missing = ((hi - lo).days + 1) * len(shops) - len(
            {(r["shop_id"], r["business_date"]) for r in observed}
        )
        return value, missing

    month_actual, month_missing = actual(start, cutoff, chosen or set(MEMBERS))
    annual_actual, annual_missing = actual(APPROVED_START, cutoff, set(MEMBERS))
    month_target = current["target_amount"] if month_scope else None
    with localcontext() as context:
        context.prec = 80
        annual_target = (
            sum((by_month[d]["target_amount"] for d in expected), Decimal(0))
            if annual_scope
            else None
        )
        gap = (
            max(month_target - month_actual, Decimal(0))
            if month_target is not None and month_actual is not None
            else None
        )
        month_ratio, annual_ratio = (
            ratio(month_actual, month_target),
            ratio(annual_actual, annual_target),
        )
    attempt = (
        c.execute(
            sa.text(
                "SELECT status,started_at,finished_at,queued_at,error_code FROM public.task_runs "
                "WHERE job_code='sync_sales_targets' ORDER BY queued_at DESC,id DESC LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    success = (
        c.execute(
            sa.text(
                "SELECT finished_at FROM public.task_runs WHERE result_version_id=:v "
                "AND status='succeeded' ORDER BY finished_at DESC,id DESC LIMIT 1"
            ),
            dict(v=version),
        ).scalar_one_or_none()
        if version
        else None
    )
    source_state = (
        "failed_retained"
        if version and attempt and attempt["status"] == "failed"
        else "observed"
        if version
        else "source_unavailable"
    )
    note = "目标最近更新失败，保留上次成功目标。" if source_state == "failed_retained" else ""
    note += "原版 GMV 目标按元读取，固定六店渠道范围；完成额为数仓原值，不重复扣退款或做单。"
    if not version:
        note += "目标尚未接收，目标/达成率/缺口暂不可用。"
    elif missing_months:
        note += f"财年缺{len(missing_months)}个月目标，不以部分和充全年。"
    note += (
        f"月/年分别缺{month_missing}/{annual_missing}个销售店铺日；"
        "完整日未知，达成率仅为已观察参考。"
    )
    return dict(
        month_actual=number(month_actual, money=True),
        annual_actual=number(annual_actual, money=True),
        month_target=number(month_target, money=True),
        annual_target=number(annual_target, money=True),
        month_ratio=month_ratio,
        annual_ratio=annual_ratio,
        gap=number(gap, money=True),
        month_missing_days=month_missing,
        annual_missing_days=annual_missing,
        state="source_unavailable"
        if not version
        else "partial"
        if month_missing or annual_missing or not annual_scope or not month_scope
        else "observed_reference",
        target_basis=basis,
        target_basis_label=BASIS[basis][1],
        target_coverage=dict(
            available_months=[d.strftime("%Y-%m") for d in expected if d in by_month],
            missing_months=missing_months,
            member_shop_ids=list(MEMBERS),
            scope_state="complete" if annual_scope else "partial",
        ),
        target_source=dict(
            state=source_state,
            url=TARGET_URL,
            version=version,
            field_id="fldZq41Hnj",
            last_success_at=success.isoformat() if success else None,
            last_attempt_status=attempt["status"] if attempt else None,
            last_attempt_at=(
                attempt["finished_at"] or attempt["started_at"] or attempt["queued_at"]
            ).isoformat()
            if attempt
            else None,
        ),
        monthly_window=dict(start=str(start), end=str(cutoff)),
        annual_window=dict(start=str(APPROVED_START), end=str(cutoff)),
        note=note,
    )
