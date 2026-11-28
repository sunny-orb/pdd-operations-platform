"""RPA三角色整批原子发布，复用事实/版本/报告与归档，不触及上游。"""

import hashlib
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.modules.archive_budget import consume
from app.modules.archive_payload import open_payload
from app.modules.archive_store import require_archive, write_archive_stream
from app.modules.streaming_json import canonical_chunks, fingerprint, fingerprint_rows

from . import rpa_ingestion
from .classification import rule_parameters
from .publisher import canonical, private, reflect, source_chunks
from .rpa_ingestion import REVISION, normalize

NOTE = "RPA期间商品报表；数值空白按业务确认计0；确认与支付期间非同批订单；采集完整性未知。"


def producer():
    return fingerprint(
        {
            p.name: p.read_text()
            for p in (
                Path(__file__),
                Path(__file__).with_name("rpa_ingestion.py"),
                Path(__file__).with_name("classification.py"),
                Path(__file__).with_name("decisions.py"),
                Path(__file__).with_name("decision_policy.py"),
                Path(__file__).parents[2] / "connectors/aftersales_rpa.py",
            )
        }
    )


def seal(c, versions, vid, digest, evidence):
    c.execute(
        versions.update()
        .where(versions.c.id == vid)
        .values(state="validated", content_fingerprint=digest, validation_evidence_ref=evidence)
    )
    c.execute(versions.update().where(versions.c.id == vid).values(state="published"))


def input_digest(raw, rows, *, exclude_updated_at=True):
    """流式摘要只排除原行顶层写入时间；原字段、精度、次序与规范规则均参与。"""
    digest = hashlib.sha256(b'{"raw":[')
    separator = b""
    for row in raw:
        digest.update(separator)
        value = {k: v for k, v in row.items() if k != "updated_at"} if exclude_updated_at else row
        for chunk in canonical_chunks(value):
            digest.update(chunk)
        separator = b","
    digest.update(b'],"revision":')
    for chunk in canonical_chunks(REVISION):
        digest.update(chunk)
    digest.update(b',"rows":')
    for chunk in canonical_chunks(rows):
        digest.update(chunk)
    digest.update(b"}")
    return digest.hexdigest()


def legacy_input_matches(old, group):
    """仅核对同组最近封版；原归档和当前规范行重算旧摘要一致才复用原身份。"""
    path = require_archive(old["retained_input_ref"])
    require_archive(old["interpretation_ref"])
    matched, count = True, 0

    def archived_rows():
        nonlocal matched, count
        with open_payload(path) as source:
            for line in source:
                consume(len(line))
                count += 1
                if count > rpa_ingestion.MAX_GROUP_ROWS:
                    raise ValueError("RPA_ARCHIVE_ROW_LIMIT")
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise ValueError("RPA_ARCHIVE_CONTENT_MISMATCH") from exc
                if not isinstance(row, dict):
                    raise ValueError("RPA_ARCHIVE_CONTENT_MISMATCH")
                # 逐行比规范字节而非Python数值相等，保留小数位、类型与未知JSON字段。
                expected = group.raw[count - 1] if count <= len(group.raw) else None
                if expected is None or canonical(
                    {k: v for k, v in row.items() if k != "updated_at"}
                ) != canonical({k: v for k, v in expected.items() if k != "updated_at"}):
                    matched = False
                yield row

    legacy_digest = input_digest(archived_rows(), group.rows, exclude_updated_at=False)
    if not matched or count != len(group.raw):
        return False
    if legacy_digest != old["content_fingerprint"]:
        raise ValueError("RPA_ARCHIVE_CONTENT_MISMATCH")
    return True


def receive_group(c, tables, group, root, code_hash):
    versions = tables["dataset_versions"]
    digest = input_digest(group.raw, group.rows)
    scope = (
        versions.c.dataset_code == group.code,
        versions.c.period_start == group.start,
        versions.c.period_end == group.end,
        versions.c.row_schema_revision == REVISION,
        versions.c.state == "published",
    )
    old = (
        c.execute(
            sa.select(versions).where(*scope, versions.c.content_fingerprint == digest).limit(1)
        )
        .mappings()
        .first()
    )
    if old:
        require_archive(old["retained_input_ref"])
        require_archive(old["interpretation_ref"])
        return old["id"], False
    inputs, reports = tables["version_inputs"], tables["aftersales_reports"]
    # 30天自然月可与滚动周报同窗；用原报告绑定选同类型，不试读另一类型的大归档。
    same_kind = (
        sa.select(1)
        .select_from(
            inputs.join(reports, reports.c.dataset_version_id == inputs.c.output_version_id)
        )
        .where(
            inputs.c.input_version_id == versions.c.id,
            inputs.c.input_role == group.role,
            reports.c.report_type == group.kind,
            reports.c.period_start == group.start,
            reports.c.period_end == group.end,
        )
        .exists()
    )
    previous = (
        c.execute(
            sa.select(versions)
            .where(*scope, same_kind)
            .order_by(versions.c.sealed_at.desc(), versions.c.created_at.desc(), versions.c.id)
            .limit(1)
        )
        .mappings()
        .first()
    )
    if previous and legacy_input_matches(previous, group):
        return previous["id"], False
    vid = str(uuid4())
    # 使用项目已支持压缩分块的输入文件类型，避免逐原行扫描全局归档配额。
    archive = write_archive_stream(root / vid / "input.ndjson", source_chunks(group.raw))
    evidence = private(
        root / vid / "interpretation.json",
        canonical(
            dict(
                revision=REVISION,
                source_class=group.source_class,
                kind=group.kind,
                start=group.start,
                end=group.end,
                note=NOTE,
                rows=len(group.rows),
                totals=sum(r["row_kind"] == "total" for r in group.rows),
            )
        ),
    )
    c.execute(
        versions.insert().values(
            id=vid,
            dataset_code=group.code,
            origin_kind="source",
            state="draft",
            source_system="company_rpa_postgres",
            source_locator="public.aftersales_reports_rpa",
            row_schema_revision=REVISION,
            interpretation_ref=evidence,
            producer_revision=code_hash,
            retained_input_ref=archive,
            received_at=sa.func.clock_timestamp(),
            period_start=group.start,
            period_end=group.end,
        )
    )
    facts = tables[group.table]
    # RPA是期间原行，不进入仅允许JST日明细代码的共享内容块。
    for i in range(0, len(group.rows), 1000):
        c.execute(
            facts.insert(),
            [
                dict(row, id=str(uuid4()), dataset_version_id=vid)
                for row in group.rows[i : i + 1000]
            ],
        )
    query = (
        sa.select(*(facts.c[k] for k in group.rows[0]))
        .where(facts.c.dataset_version_id == vid)
        .order_by(facts.c.source_row_key.collate("C"))
        .execution_options(yield_per=1000)
    )
    with c.execute(query) as actual:
        if fingerprint_rows(dict(r) for r in actual.mappings()) != fingerprint_rows(
            sorted(group.rows, key=lambda r: r["source_row_key"])
        ):
            raise ValueError("RPA_PG_CONTENT_MISMATCH")
    seal(c, versions, vid, digest, evidence)
    return vid, True


def comparison_period(kind, start, end):
    if kind == "weekly":
        return start - timedelta(days=7), end - timedelta(days=7)
    stop = start - timedelta(days=1)
    return stop.replace(day=1), stop


def publish(c, tables, groups, root, code_hash):
    versions, reports = tables["dataset_versions"], tables["aftersales_reports"]
    current, inputs, rules = (
        tables[n] for n in ("current_datasets", "version_inputs", "rule_sets")
    )
    target = c.scalar(
        sa.select(current.c.version_id).where(current.c.dataset_code == "refund_targets")
    )
    params = rule_parameters(targets_available=bool(target), risk_available=True)
    rhash = fingerprint(params)
    rule_id = c.scalar(
        sa.select(rules.c.id).where(rules.c.set_code == "m5-rpa", rules.c.revision == rhash)
    )
    if not rule_id:
        rule_id = str(uuid4())
        c.execute(
            rules.insert().values(
                id=rule_id,
                set_code="m5-rpa",
                revision=rhash,
                domain="aftersales",
                schema_revision="v1",
                parameters=params,
                confirmation_status="partially_confirmed",
                content_sha256=rhash,
                source_locator="docs/implementation/m5-aftersales-implementation.md",
                evidence_note=NOTE,
            )
        )
    by_period = defaultdict(list)
    new_inputs = 0
    for g in groups:
        vid, created = receive_group(c, tables, g, root, code_hash)
        new_inputs += created
        by_period[(g.kind, g.start, g.end)].append((g, vid))
    outputs, latest_vid = {}, None
    # 上游可滚动删去旧期间；比较仍使用本地已经验证并固定的同来源历史。
    for report in c.execute(
        sa.select(reports)
        .join(versions, versions.c.id == reports.c.dataset_version_id)
        .where(versions.c.state == "published", versions.c.row_schema_revision == REVISION)
        .order_by(reports.c.revision_no.desc())
    ).mappings():
        outputs.setdefault(
            (report["report_type"], report["period_start"], report["period_end"]), report["id"]
        )
    # 先发布去年的真实输入和较早期间，再绑定本报告的固定比较版本。
    for key in sorted(by_period, key=lambda k: (k[2], k[1], k[0])):
        kind, start, end = key
        previous_key = (kind, *comparison_period(kind, start, end))
        try:
            yoy_key = (kind, start.replace(year=start.year - 1), end.replace(year=end.year - 1))
        except ValueError:
            yoy_key = None  # 2月29日无同日期；不猜测替代期间。
        previous, yoy = outputs.get(previous_key), outputs.get(yoy_key)
        heads = {g.role: vid for g, vid in by_period[key]}
        digest = fingerprint(
            dict(
                revision=REVISION,
                producer=code_hash,
                period=key,
                inputs=heads,
                previous=previous,
                yoy=yoy,
                target=target,
                rule=rule_id,
            )
        )
        old = (
            c.execute(
                sa.select(reports.c.id, versions.c.id.label("vid"), versions.c.retained_input_ref)
                .join(versions, versions.c.id == reports.c.dataset_version_id)
                .where(
                    versions.c.dataset_code == "aftersales_report",
                    versions.c.content_fingerprint == digest,
                    versions.c.state == "published",
                )
            )
            .mappings()
            .first()
        )
        if old:
            require_archive(old["retained_input_ref"])
            outputs[key], latest_vid = old["id"], old["vid"]
            continue
        vid, rid = str(uuid4()), str(uuid4())
        evidence = private(
            root / vid / "report.json",
            canonical(
                dict(
                    period=key,
                    inputs=heads,
                    previous=previous,
                    yoy=yoy,
                    target=target,
                    rule=rule_id,
                    note=NOTE,
                )
            ),
        )
        c.execute(
            versions.insert().values(
                id=vid,
                dataset_code="aftersales_report",
                origin_kind="derived",
                state="draft",
                row_schema_revision=REVISION,
                interpretation_ref=evidence,
                retained_input_ref=evidence,
                producer_revision=code_hash,
                rule_set_id=rule_id,
                period_start=start,
                period_end=end,
            )
        )
        for role, input_id in {**heads, **({"targets": target} if target else {})}.items():
            c.execute(
                inputs.insert().values(
                    output_version_id=vid,
                    input_role=role,
                    input_version_id=input_id,
                    window_start=start,
                    window_end=end,
                    usage_note=NOTE,
                )
            )
        revision = (
            c.scalar(
                sa.select(sa.func.max(reports.c.revision_no)).where(
                    reports.c.report_type == kind,
                    reports.c.period_start == start,
                    reports.c.period_end == end,
                )
            )
            or 0
        ) + 1
        c.execute(
            reports.insert().values(
                id=rid,
                dataset_version_id=vid,
                report_type=kind,
                period_start=start,
                period_end=end,
                revision_no=revision,
                source_report_key=f"rpa:{kind}:{start}:{end}",
                source_label="近30天退货分析 · 周报"
                if kind == "weekly"
                else f"{start:%Y年%m月}退货分析 · 月报",
                source_generated_time_status="unknown",
                source_generated_time_note="updated_at是RPA写入时间，不冒充报表生成或完整截至时间",
                previous_report_id=previous,
                previous_comparison_status="available" if previous else "missing",
                previous_comparison_reason=None if previous else "未收到相邻上期同范围RPA报表",
                yoy_report_id=yoy,
                yoy_comparison_status="available" if yoy else "missing",
                yoy_comparison_reason=None if yoy else "未收到去年相同起止日期RPA报表",
                evidence_note=NOTE,
            )
        )
        seal(c, versions, vid, digest, evidence)
        outputs[key], latest_vid = rid, vid
    # 与页面默认选择完全一致；较早补报不倒退，同结束日月报优先于周报。
    latest_vid = c.scalar(
        sa.select(reports.c.dataset_version_id)
        .join(versions, versions.c.id == reports.c.dataset_version_id)
        .where(versions.c.state == "published", versions.c.row_schema_revision == REVISION)
        .order_by(
            reports.c.period_end.desc(),
            sa.case(
                (reports.c.report_type == "monthly", 0),
                (reports.c.report_type == "weekly", 1),
                else_=2,
            ),
            reports.c.period_start.desc(),
            reports.c.revision_no.desc(),
            reports.c.id,
        )
        .limit(1)
    )
    c.execute(
        insert(current)
        .values(dataset_code="aftersales_report", version_id=latest_vid)
        .on_conflict_do_update(
            index_elements=[current.c.dataset_code], set_={"version_id": latest_vid}
        )
    )
    for source in c.execute(
        sa.select(versions.c.dataset_code, versions.c.id)
        .join(inputs, inputs.c.input_version_id == versions.c.id)
        .where(
            inputs.c.output_version_id == latest_vid,
            versions.c.dataset_code.in_([g.code for g in groups]),
        )
    ):
        c.execute(
            insert(current)
            .values(dataset_code=source.dataset_code, version_id=source.id)
            .on_conflict_do_update(
                index_elements=[current.c.dataset_code], set_={"version_id": source.id}
            )
        )
    return dict(
        status="published" if new_inputs else "reused",
        version_id=latest_vid,
        reports=len(by_period),
        new_inputs=new_inputs,
        rows=sum(len(g.rows) for g in groups),
    )


def sync_rpa(engine, receive, root):
    """receive完成源双读；登记任务后读源，一次事务发布全部相容报告。"""
    tables, run, code_hash = reflect(engine), str(uuid4()), producer()
    task = tables["task_runs"]
    with engine.connect() as c:
        locked = c.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext('m5-source-report'))"))
        c.commit()
        if not locked:
            raise ValueError("M5_SYNC_RUNNING")
        try:
            with c.begin():
                now = c.scalar(sa.select(sa.func.clock_timestamp()))
                c.execute(
                    task.update()
                    .where(task.c.job_code == "sync_rpa_aftersales", task.c.status == "running")
                    .values(
                        status="abandoned",
                        finished_at=now,
                        lease_expires_at=None,
                        error_code="WORKER_LOST",
                    )
                )
                c.execute(
                    task.insert().values(
                        id=run,
                        job_code="sync_rpa_aftersales",
                        request_ref=REVISION,
                        lock_key="m5-rpa",
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=run,
                        lease_expires_at=now + timedelta(hours=2),
                    )
                )
            groups = normalize(receive())
            if producer() != code_hash:
                raise ValueError("RPA_PRODUCER_CHANGED")
            with c.begin():
                result = publish(c, tables, groups, Path(root), code_hash)
                c.execute(
                    task.update()
                    .where(task.c.id == run)
                    .values(
                        status="succeeded",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        result_version_id=result["version_id"],
                    )
                )
            return result
        except (Exception, KeyboardInterrupt):
            c.rollback()
            with c.begin():
                c.execute(
                    task.update()
                    .where(task.c.id == run, task.c.status == "running")
                    .values(
                        status="failed",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        error_code="RPA_RECEPTION_FAILED",
                    )
                )
            raise
        finally:
            c.rollback()
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m5-source-report'))"))
            c.commit()
