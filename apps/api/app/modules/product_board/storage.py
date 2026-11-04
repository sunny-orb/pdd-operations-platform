"""M1受控版本接收：短事务分批落draft，最终全投影复核后原子发布四输入。"""

import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.modules.sales.source_policy import SHOPS

from .source import SHOP_PROFILE, TABLES, fingerprint, identity, parse_ids, windows

FACT_TABLES = dict(
    product_daily="product_daily_facts",
    profile_records="profile_source_records",
    product_promotion="product_promotion_records",
    profile_links="profile_listing_links",
)
REVISION = "m1-board-v2-1"


def producer_hash():
    return fingerprint(
        {
            name: Path(__file__).with_name(name).read_text()
            for name in ("storage.py", "source.py", "profile_rules.py")
        }
    )


LOADED_PRODUCER_HASH = producer_hash()


class ProductBoardStore:
    def __init__(self, engine, archive_root, *, reconcile_full_scope=False):
        self.engine, self.archive_root = engine, Path(archive_root)
        self.reconcile_full_scope = reconcile_full_scope
        self.changes = []
        m = sa.MetaData()
        self.tables = {
            n: sa.Table(n, m, schema="public", autoload_with=engine, resolve_fks=False)
            for n in (
                *FACT_TABLES.values(),
                "listings",
                "shops",
                "dataset_versions",
                "version_inputs",
                "current_datasets",
                "task_runs",
                "coverage_records",
            )
        }

    def rebuild_profiles(self):
        """固定已发布三来源重建关系；不接上游，不重写事实/来源时间，返回发布摘要。"""
        source_versions = {}

        def receive(heartbeat):
            rows = {}
            with (
                self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
                c.begin(),
            ):
                c.execute(sa.text("SET TRANSACTION READ ONLY"))
                versions = {
                    r["dataset_code"]: dict(r)
                    for r in c.execute(
                        sa.text(
                            "SELECT v.* FROM public.current_datasets d "
                            "JOIN public.dataset_versions v ON v.id=d.version_id "
                            "WHERE v.state='published' AND v.origin_kind='source' "
                            "AND d.dataset_code=ANY(:codes)"
                        ),
                        dict(codes=list(TABLES)),
                    ).mappings()
                }
                if set(versions) != set(TABLES):
                    raise ValueError("SOURCE_INPUT_MISSING")
                for code, version in versions.items():
                    source_versions[code] = version["id"]
                    table = self.tables[FACT_TABLES[code]]
                    columns = [
                        col for col in table.c if col.name not in ("id", "dataset_version_id")
                    ]
                    rows[code] = [
                        dict(r)
                        for r in c.execute(
                            sa.select(*columns)
                            .where(table.c.dataset_version_id == version["id"])
                            .order_by(table.c.source_row_key)
                        ).mappings()
                    ]
                    heartbeat()
                return versions["product_daily"]["period_end"], rows

        return self.run(receive, _source_versions=source_versions)

    def run_selected(self, selected, receive):
        """仅接收选中物理来源，其他来源固定当前不可变版本；缺输入时不扩大下载。"""
        selected = set(selected)
        if not selected or not selected <= TABLES.keys():
            raise ValueError("INVALID_SOURCE_SELECTION")
        reused = {}

        def merge(heartbeat):
            rows = {}
            with (
                self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
                c.begin(),
            ):
                c.execute(sa.text("SET TRANSACTION READ ONLY"))
                for code in TABLES.keys() - selected:
                    vid = c.scalar(
                        sa.text("SELECT version_id FROM current_datasets WHERE dataset_code=:code"),
                        {"code": code},
                    )
                    if vid is None:
                        raise ValueError("SOURCE_INPUT_MISSING")
                    reused[code] = vid
                    table = self.tables[FACT_TABLES[code]]
                    columns = [
                        col for col in table.c if col.name not in ("id", "dataset_version_id")
                    ]
                    rows[code] = [
                        dict(r)
                        for r in c.execute(
                            sa.select(*columns)
                            .where(table.c.dataset_version_id == vid)
                            .order_by(table.c.source_row_key)
                        ).mappings()
                    ]
            heartbeat()
            end, received = receive(heartbeat)
            if set(received) != selected:
                raise ValueError("SOURCE_SELECTION_MISMATCH")
            return end, {**rows, **received}

        return self.run(merge, _source_versions=reused, _partial=True)

    def run(self, receive, *, _source_versions=None, _partial=False):
        """receive(heartbeat)->(截至日,三来源必要投影)。锁和租约覆盖整个接收，失败保留旧头。"""
        if producer_hash() != LOADED_PRODUCER_HASH:
            raise ValueError("PRODUCER_CODE_CHANGED")
        code_hash = LOADED_PRODUCER_HASH
        reuse_sources = _source_versions if _source_versions is not None else {}
        self.changes = []
        task = self.tables["task_runs"]
        run = str(uuid4())
        with self.engine.connect() as c:
            if not c.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext('m1-product-board'))")
            ).scalar_one():
                c.rollback()
                raise ValueError("SYNC_ALREADY_RUNNING")
            c.commit()
            try:
                with c.begin():
                    now = c.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                    c.execute(
                        task.update()
                        .where(
                            task.c.lock_key == "m1-product-board",
                            task.c.status == "running",
                            task.c.lease_expires_at < sa.func.clock_timestamp(),
                        )
                        .values(
                            status="abandoned",
                            finished_at=sa.func.clock_timestamp(),
                            lease_expires_at=None,
                            error_code="LEASE_EXPIRED",
                        )
                    )
                    c.execute(
                        task.insert().values(
                            id=run,
                            job_code="rebuild_product_profiles"
                            if _source_versions is not None and not _partial
                            else "sync_product_board",
                            request_ref=REVISION
                            + (
                                ":profile_rebuild"
                                if _source_versions is not None and not _partial
                                else ""
                            ),
                            lock_key="m1-product-board",
                            status="running",
                            started_at=now,
                            queued_at=now,
                            worker_ref=run,
                            lease_expires_at=now + timedelta(seconds=180),
                        )
                    )

                def heartbeat():
                    with c.begin():
                        found = c.execute(
                            task.update()
                            .where(
                                task.c.id == run,
                                task.c.status == "running",
                                task.c.lease_expires_at > sa.func.clock_timestamp(),
                            )
                            .values(
                                lease_expires_at=sa.func.clock_timestamp()
                                + sa.text("interval '180 seconds'")
                            )
                        )
                        if found.rowcount != 1:
                            raise ValueError("TASK_LEASE_LOST")

                end, rows = receive(heartbeat)
                if (
                    _source_versions is not None
                    and not _partial
                    and set(reuse_sources) != set(TABLES)
                ):
                    raise ValueError("SOURCE_INPUT_MISSING")
                if producer_hash() != code_hash:
                    raise ValueError("PRODUCER_CODE_CHANGED")
                archive = self.archive_root / run
                archive.mkdir(parents=True, mode=0o700)
                os.chmod(archive, 0o700)
                for code, values in rows.items():
                    if code in reuse_sources:
                        continue
                    p = archive / (code + ".json")
                    p.write_text(
                        json.dumps(values, default=str, ensure_ascii=False, separators=(",", ":"))
                    )
                    os.chmod(p, 0o600)
                for name in ("source.py", "storage.py", "profile_rules.py"):
                    p = archive / name
                    p.write_text(Path(__file__).with_name(name).read_text())
                    os.chmod(p, 0o600)
                manifest = dict(
                    revision=REVISION,
                    producer=code_hash,
                    end=str(end),
                    windows={
                        code: windows(end, code) for code in TABLES if code != "profile_records"
                    },
                    reused_source_versions=dict(reuse_sources),
                    files={p.name: fingerprint(p.read_text()) for p in archive.iterdir()},
                )
                (archive / "manifest.json").write_text(
                    json.dumps(manifest, default=str, ensure_ascii=False)
                )
                os.chmod(archive / "manifest.json", 0o600)
                versions = self._stage(
                    c, rows, end, archive, heartbeat, reuse_sources=reuse_sources
                )
                if self.reconcile_full_scope:
                    changes = archive / "reconciliation.json"
                    changes.write_text(json.dumps(self.changes, ensure_ascii=False))
                    os.chmod(changes, 0o600)
                    manifest["files"][changes.name] = fingerprint(changes.read_text())
                    manifest["reconciliation_policy"] = "stable-full-scope"
                    (archive / "manifest.json").write_text(json.dumps(manifest, default=str))
                if producer_hash() != code_hash:
                    raise ValueError("PRODUCER_CODE_CHANGED")
                with c.begin():
                    self._check_lease(c, run)
                    for code, vid in reuse_sources.items():
                        current = c.execute(
                            sa.text(
                                "SELECT version_id FROM public.current_datasets "
                                "WHERE dataset_code=:code FOR UPDATE"
                            ),
                            dict(code=code),
                        ).scalar_one()
                        if current != vid:
                            raise ValueError("SOURCE_INPUT_CHANGED")
                    for code, vid in versions.items():
                        if code in reuse_sources:
                            continue
                        v = self.tables["dataset_versions"]
                        state = c.execute(
                            sa.select(v.c.state).where(v.c.id == vid).with_for_update()
                        ).scalar_one()
                        if state == "validated":
                            c.execute(v.update().where(v.c.id == vid).values(state="published"))
                        pointer = self.tables["current_datasets"]
                        c.execute(
                            insert(pointer)
                            .values(
                                dataset_code=code,
                                version_id=vid,
                                updated_at=sa.func.clock_timestamp(),
                            )
                            .on_conflict_do_update(
                                index_elements=["dataset_code"], set_=dict(version_id=vid)
                            )
                        )
                    c.execute(
                        task.update()
                        .where(task.c.id == run)
                        .values(
                            status="succeeded",
                            finished_at=sa.func.clock_timestamp(),
                            lease_expires_at=None,
                            result_version_id=versions["profile_links"],
                            evidence_ref=str(archive / "manifest.json"),
                        )
                    )
                return dict(
                    status="published",
                    versions=versions,
                    rows={k: len(v) for k, v in rows.items()},
                    end=str(end),
                )
            except Exception as exc:
                if c.in_transaction():
                    c.rollback()
                with c.begin():
                    c.execute(
                        task.update()
                        .where(task.c.id == run, task.c.status == "running")
                        .values(
                            status="failed",
                            finished_at=sa.func.clock_timestamp(),
                            lease_expires_at=None,
                            error_code="M1_RECEPTION_FAILED",
                            error_summary=type(exc).__name__,
                        )
                    )
                raise
            finally:
                c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m1-product-board'))"))
                c.commit()

    def _check_lease(self, c, run):
        t = self.tables["task_runs"]
        current = c.execute(sa.select(t).where(t.c.id == run).with_for_update()).mappings().one()
        now = c.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        if current["status"] != "running" or current["lease_expires_at"] <= now:
            raise ValueError("TASK_LEASE_LOST")

    def _stage(self, c, rows, end, archive, heartbeat, *, reuse_sources=None):
        reuse_sources = reuse_sources or {}
        if set(rows) != set(TABLES) or not rows["product_daily"] or not rows["profile_records"]:
            raise ValueError("SOURCE_INPUT_MISSING")
        for code in TABLES:
            if code in reuse_sources:
                continue  # 同一RR快照的不可变来源，最终发布时再锁定验证指针。
            # Compare only overlapping received dates: moving the 60-day window is not deletion.
            t = self.tables[FACT_TABLES[code]]
            with c.begin():
                old = c.execute(
                    sa.text(
                        "SELECT version_id FROM public.current_datasets WHERE dataset_code=:code"
                    ),
                    dict(code=code),
                ).scalar_one_or_none()
                if old:
                    fields = (
                        ()
                        if code == "profile_records"
                        else ("source_shop_id", "source_product_id", "business_date")
                        if code == "product_daily"
                        else (
                            "source_shop_id",
                            "source_product_id",
                            "period_start",
                            "period_end",
                            "source_granularity",
                        )
                    )
                    oldrows = (
                        c.execute(
                            sa.select(t.c.source_row_key, *(t.c[k] for k in fields)).where(
                                t.c.dataset_version_id == old
                            )
                        )
                        .mappings()
                        .all()
                    )
                    newrows = {r["source_row_key"]: r for r in rows[code]}
                    oldkeys = {
                        r["source_row_key"]
                        for r in oldrows
                        if not fields or any(a <= r[fields[2]] <= b for a, b in windows(end, code))
                    }
                    removed = oldkeys - newrows.keys()
                    if removed and not self.reconcile_full_scope:
                        raise ValueError("SOURCE_DELETE_UNCONFIRMED")
                    changed = (
                        sum(
                            r["source_row_key"] in newrows
                            and any(r[k] != newrows[r["source_row_key"]][k] for k in fields)
                            for r in oldrows
                        )
                        if fields
                        else 0
                    )
                    if changed and not self.reconcile_full_scope:
                        raise ValueError("SOURCE_KEY_IDENTITY_CHANGED")
                    self.changes.append(
                        dict(
                            dataset=code,
                            previous_version=old,
                            removed_keys=len(removed),
                            identity_changes=changed,
                        )
                    )
        listings = {
            r["listing_id"]: (r["source_shop_id"], r["source_product_id"])
            for code in ("product_daily", "product_promotion")
            for r in rows[code]
        }
        links = []
        for record in rows["profile_records"]:
            for shop, field in SHOP_PROFILE.items():
                for product in parse_ids(record[field]):
                    lid = identity(shop, product)
                    listings[lid] = (shop, product)
                    links.append(dict(profile_key=record["source_row_key"], listing_id=lid))
        with c.begin():
            shops = self.tables["shops"]
            for sid, (_, name, _) in SHOPS.items():
                c.execute(
                    insert(shops)
                    .values(id=sid, shop_name=name, status="active")
                    .on_conflict_do_nothing()
                )
            t = self.tables["listings"]
            values = [
                dict(
                    id=lid,
                    shop_id=sid,
                    platform_code="pdd",
                    platform_product_id=pid,
                    status="active",
                )
                for lid, (sid, pid) in listings.items()
            ]
            for offset in range(0, len(values), 1000):
                c.execute(insert(t).on_conflict_do_nothing(), values[offset : offset + 1000])
        versions = {}
        for code in (*TABLES, "profile_links"):
            heartbeat()
            if producer_hash() != LOADED_PRODUCER_HASH:
                raise ValueError("PRODUCER_CODE_CHANGED")
            if code == "profile_links":
                raw = [
                    dict(
                        profile_record_id=identity(versions["profile_records"], r["profile_key"]),
                        listing_id=r["listing_id"],
                        relationship_status="matched",
                        relationship_reason="numeric_rows_and_explicit_status_notes",
                        listed_date_applicability_status="unknown",
                    )
                    for r in links
                ]
            else:
                raw = rows[code]
            if code in reuse_sources:
                versions[code] = reuse_sources[code]
                continue
            digest = fingerprint(
                dict(
                    rows=raw,
                    revision=REVISION,
                    producer=LOADED_PRODUCER_HASH,
                    end=None if code == "profile_records" else end,
                    inputs=versions if code == "profile_links" else None,
                )
            )
            v = self.tables["dataset_versions"]
            with c.begin():
                existing = c.execute(
                    sa.select(v.c.id, v.c.state).where(
                        v.c.dataset_code == code, v.c.content_fingerprint == digest
                    )
                ).first()
            if existing and existing.state in ("validated", "published"):
                versions[code] = existing.id
                continue
            vid = str(uuid4())
            versions[code] = vid
            with c.begin():
                c.execute(
                    v.insert().values(
                        id=vid,
                        dataset_code=code,
                        origin_kind="derived" if code == "profile_links" else "source",
                        state="draft",
                        source_system=None if code == "profile_links" else "warehouse",
                        source_locator=None
                        if code == "profile_links"
                        else "cubeappdata." + TABLES[code],
                        row_schema_revision="v2-20261028",
                        interpretation_ref="docs/data-contract.md#351-m1-商品经营整页含-m4-新品评分",
                        producer_revision=LOADED_PRODUCER_HASH,
                        retained_input_ref=str(archive),
                        received_at=None if code == "profile_links" else sa.func.clock_timestamp(),
                        period_start=None
                        if code == "profile_records"
                        else windows(end, code)[0][0],
                        period_end=None if code == "profile_records" else end,
                    )
                )
                if code == "profile_links":
                    for role, key in (
                        ("profile_records", "profile_records"),
                        ("product_current", "product_daily"),
                        ("ad_current", "product_promotion"),
                    ):
                        c.execute(
                            self.tables["version_inputs"]
                            .insert()
                            .values(
                                output_version_id=vid,
                                input_role=role,
                                input_version_id=versions[key],
                                usage_note="atomic M1 compatible input",
                            )
                        )
            t = self.tables[FACT_TABLES[code]]
            values = [
                dict(
                    r,
                    id=identity(
                        vid,
                        r["source_row_key"]
                        if code != "profile_links"
                        else r["profile_record_id"] + "/" + r["listing_id"],
                    ),
                    dataset_version_id=vid,
                )
                for r in raw
            ]
            for offset in range(0, len(values), 1000):
                heartbeat()
                with c.begin():
                    c.execute(t.insert(), values[offset : offset + 1000])
            with c.begin():
                # Verify the complete stored projection (not only a row count/sum).
                actual = (
                    [
                        {k: r[k] for k in raw[0]}
                        for r in c.execute(
                            sa.select(t).where(t.c.dataset_version_id == vid).order_by(t.c.id)
                        ).mappings()
                    ]
                    if raw
                    else []
                )
                if sorted(fingerprint(r) for r in actual) != sorted(fingerprint(r) for r in raw):
                    raise ValueError("STORED_PROJECTION_MISMATCH")
                if code != "profile_links":
                    for start, finish in (
                        ((None, None),) if code == "profile_records" else windows(end, code)
                    ):
                        date_field = "business_date" if code == "product_daily" else "period_start"
                        observed = (
                            bool(raw)
                            if start is None
                            else any(start <= r[date_field] <= finish for r in raw)
                        )
                        c.execute(
                            self.tables["coverage_records"]
                            .insert()
                            .values(
                                id=str(uuid4()),
                                dataset_version_id=vid,
                                scope_kind="dataset",
                                window_start=start,
                                window_end=finish,
                                availability_state="observed" if observed else "no_records",
                                completeness_state="unknown",
                                reason_code="SOURCE_COMPLETENESS_UNPROVEN",
                                evidence_ref=str(archive / "manifest.json"),
                                checked_at=sa.func.clock_timestamp(),
                            )
                        )
                c.execute(
                    v.update()
                    .where(v.c.id == vid)
                    .values(
                        content_fingerprint=digest,
                        state="validated",
                        validation_evidence_ref=str(archive / "manifest.json"),
                    )
                )
        return versions
