"""新版六表接收/发布；长读取不持发布事务，封版与指针短事务切换。"""

import os
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from .contracts import PublishResult, SalesError
from .normalization import canonical, fingerprint
from .source_policy import (
    HEARTBEAT_SECONDS,
    LEASE_SECONDS,
    POLICY_REVISION,
    SHOPS,
    SOURCE_TABLE,
    approved_window,
)

TABLE_NAMES = (
    "shops",
    "shop_daily_facts",
    "dataset_versions",
    "coverage_records",
    "task_runs",
    "current_datasets",
)


def reflect_tables(engine):
    """只反射实际v2六表的列；不导入历史M1 ORM，不运行DDL。"""
    metadata = sa.MetaData()
    return {
        name: sa.Table(name, metadata, schema="public", autoload_with=engine, resolve_fks=False)
        for name in TABLE_NAMES
    }


class ShopDailyStore:
    def __init__(self, engine, *, archive_root: Path, approved=None):
        self.engine, self.archive_root = engine, Path(archive_root)
        self.approved = approved or approved_window()
        self.tables = reflect_tables(engine)

    def _lease(self, connection, run_id):
        tasks = self.tables["task_runs"]
        task = (
            connection.execute(sa.select(tasks).where(tasks.c.id == run_id).with_for_update())
            .mappings()
            .one()
        )
        now = connection.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        if task["status"] != "running" or task["lease_expires_at"] <= now:
            raise SalesError("TASK_LEASE_LOST", 409)
        return task

    def run(self, receive):
        """receive(heartbeat)读取来源并返回候选；无锁/无租约不得写入或发布。"""
        if producer_sources() != LOADED_PRODUCER_SOURCES:
            raise SalesError("PRODUCER_CODE_CHANGED")
        run_id, worker = str(uuid4()), str(uuid4())
        tasks = self.tables["task_runs"]
        connection = self.engine.connect()
        locked = False
        try:
            locked = connection.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext('m3-shop-daily'))")
            ).scalar_one()
            connection.commit()
            if not locked:
                raise SalesError("SYNC_ALREADY_RUNNING", 409)
            with connection.begin():
                now = connection.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                connection.execute(
                    tasks.update()
                    .where(
                        tasks.c.lock_key == "shop_daily",
                        tasks.c.status == "running",
                        tasks.c.lease_expires_at <= now,
                    )
                    .values(
                        status="abandoned",
                        finished_at=now,
                        lease_expires_at=None,
                        error_code="LEASE_EXPIRED",
                        error_summary="旧任务租约已失效",
                    )
                )
                existing = connection.execute(
                    sa.select(tasks.c.id).where(
                        tasks.c.lock_key == "shop_daily", tasks.c.status == "running"
                    )
                ).first()
                if existing:
                    raise SalesError("SYNC_ALREADY_RUNNING", 409)
                connection.execute(
                    tasks.insert().values(
                        id=run_id,
                        job_code="sync_shop_daily",
                        request_ref=f"{self.approved.start}/{self.approved.end}",
                        lock_key="shop_daily",
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=worker,
                        lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                    )
                )
            last_heartbeat = time.monotonic()

            def heartbeat(force=False):
                nonlocal last_heartbeat
                if force or time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                    with connection.begin():
                        task = self._lease(connection, run_id)
                        if task["worker_ref"] != worker:
                            raise SalesError("TASK_LEASE_LOST", 409)
                        connection.execute(
                            tasks.update()
                            .where(tasks.c.id == run_id)
                            .values(
                                lease_expires_at=sa.func.clock_timestamp()
                                + sa.text(f"interval '{LEASE_SECONDS} seconds'")
                            )
                        )
                    last_heartbeat = time.monotonic()

            try:
                candidate = receive(heartbeat)
                if producer_sources() != LOADED_PRODUCER_SOURCES:
                    raise SalesError("PRODUCER_CODE_CHANGED")
                heartbeat(force=True)
                if candidate.window != self.approved:
                    raise SalesError("PUBLICATION_SCOPE_MISMATCH")
                return self._publish(connection, run_id, candidate)
            except Exception as exc:
                if connection.in_transaction():
                    connection.rollback()
                with connection.begin():
                    task = (
                        connection.execute(
                            sa.select(tasks).where(tasks.c.id == run_id).with_for_update()
                        )
                        .mappings()
                        .one()
                    )
                    if task["status"] == "running" and task["worker_ref"] == worker:
                        code = exc.code if isinstance(exc, SalesError) else "INGESTION_FAILED"
                        connection.execute(
                            tasks.update()
                            .where(tasks.c.id == run_id)
                            .values(
                                status="failed",
                                finished_at=sa.func.clock_timestamp(),
                                lease_expires_at=None,
                                error_code=code,
                                error_summary="接收或校验失败；保留上次成功版本",
                            )
                        )
                raise
        finally:
            if connection.in_transaction():
                connection.rollback()
            if locked:
                connection.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m3-shop-daily'))"))
                connection.commit()
            connection.close()

    def publish_candidate(self, candidate):
        if candidate.window != self.approved:
            raise SalesError("PUBLICATION_SCOPE_MISMATCH")
        return self.run(lambda _: candidate)

    def _archive(self, version_id, candidate):
        """独占创建必要投影和解释清单；摘要对应实际内容，权限固定600/700。"""
        self.archive_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.archive_root, 0o700)
        folder = self.archive_root / version_id
        folder.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chmod(folder, 0o700)
        data = ("\n".join(canonical(row) for row in candidate.raw_rows) + "\n").encode()
        policy = canonical(
            dict(
                policy=POLICY_REVISION,
                start=str(candidate.window.start),
                end=str(candidate.window.end),
                shops=SHOPS,
                fingerprint=candidate.fingerprint,
                producer_revision=producer_revision(),
                input_sha256=__import__("hashlib").sha256(data).hexdigest(),
            )
        )
        for name, content in (
            ("input.ndjson", data),
            ("interpretation.json", policy.encode()),
            ("producer-source.json", canonical(LOADED_PRODUCER_SOURCES).encode()),
        ):
            with open(folder / name, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if (folder / name).stat().st_mode & 0o077:
                raise SalesError("ARCHIVE_PERMISSIONS")
        if (folder / "input.ndjson").read_bytes() != data:
            raise SalesError("ARCHIVE_CHECKSUM_MISMATCH")
        return str(folder / "input.ndjson"), str(folder / "interpretation.json")

    def _publish(self, connection, run_id, candidate):
        versions, facts, current, tasks, shops, coverage = (
            self.tables[n]
            for n in (
                "dataset_versions",
                "shop_daily_facts",
                "current_datasets",
                "task_runs",
                "shops",
                "coverage_records",
            )
        )
        version_id = str(uuid4())
        # 留存先于发布事务；失败候选留存不自动删除，不能影响旧版。
        archive, interpretation = self._archive(version_id, candidate)
        with connection.begin():
            self._lease(connection, run_id)
            old = connection.execute(
                sa.select(current.c.version_id)
                .where(current.c.dataset_code == "shop_daily")
                .with_for_update()
            ).scalar_one_or_none()
            if old:
                old_rows = connection.execute(
                    sa.select(facts.c.source_row_key, facts.c.shop_id, facts.c.business_date).where(
                        facts.c.dataset_version_id == old
                    )
                ).all()
                new_keys = {
                    (r["source_row_key"], r["shop_id"], r["business_date"]) for r in candidate.rows
                }
                if any(tuple(row) not in new_keys for row in old_rows):
                    raise SalesError("SOURCE_OLD_KEY_MISSING")
            reuse = connection.execute(
                sa.select(versions.c.id).where(
                    versions.c.dataset_code == "shop_daily",
                    versions.c.content_fingerprint == candidate.fingerprint,
                    versions.c.state.in_(["validated", "published"]),
                )
            ).scalar_one_or_none()
            if reuse:
                version_id = reuse
            else:
                for _, (id, name, _) in SHOPS.items():
                    connection.execute(
                        insert(shops)
                        .values(id=id, shop_name=name, status="active")
                        .on_conflict_do_nothing(index_elements=[shops.c.id])
                    )
                connection.execute(
                    versions.insert().values(
                        id=version_id,
                        dataset_code="shop_daily",
                        origin_kind="source",
                        state="draft",
                        source_system="warehouse",
                        source_locator="cubeappdata." + SOURCE_TABLE,
                        row_schema_revision=POLICY_REVISION,
                        interpretation_ref=interpretation,
                        producer_revision=producer_revision(),
                        retained_input_ref=archive,
                        received_at=sa.func.clock_timestamp(),
                    )
                )
                connection.execute(
                    sa.select(versions.c.id).where(versions.c.id == version_id).with_for_update()
                )
                connection.execute(
                    facts.insert(),
                    [
                        dict(row, id=str(uuid4()), dataset_version_id=version_id)
                        for row in candidate.rows
                    ],
                )
                for source_shop, (shop_id, _, _) in SHOPS.items():
                    observed = any(r["source_shop_id"] == source_shop for r in candidate.rows)
                    connection.execute(
                        coverage.insert().values(
                            id=str(uuid4()),
                            dataset_version_id=version_id,
                            scope_kind="shop",
                            shop_id=shop_id,
                            window_start=candidate.window.start,
                            window_end=candidate.window.end,
                            availability_state="observed" if observed else "no_records",
                            completeness_state="unknown",
                            reason_code="UPSTREAM_COMPLETENESS_UNKNOWN",
                            evidence_ref=interpretation,
                            checked_at=sa.func.clock_timestamp(),
                        )
                    )
                count = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(facts)
                    .where(facts.c.dataset_version_id == version_id)
                ).scalar_one()
                if count != len(candidate.rows):
                    raise SalesError("CANDIDATE_ROW_COUNT_MISMATCH")
                connection.execute(
                    versions.update()
                    .where(versions.c.id == version_id)
                    .values(
                        state="validated",
                        content_fingerprint=candidate.fingerprint,
                        validation_evidence_ref=interpretation,
                    )
                )
                connection.execute(
                    versions.update().where(versions.c.id == version_id).values(state="published")
                )
            self._lease(connection, run_id)
            connection.execute(
                insert(current)
                .values(dataset_code="shop_daily", version_id=version_id)
                .on_conflict_do_update(
                    index_elements=[current.c.dataset_code], set_={"version_id": version_id}
                )
            )
            source_time = max(
                (
                    r["source_updated_time_raw"]
                    for r in candidate.rows
                    if r["source_updated_time_raw"]
                ),
                default=None,
            )
            connection.execute(
                tasks.update()
                .where(tasks.c.id == run_id)
                .values(
                    status="succeeded",
                    result_version_id=version_id,
                    finished_at=sa.func.clock_timestamp(),
                    lease_expires_at=None,
                    source_updated_text=source_time,
                    source_update_observed_at=sa.func.clock_timestamp() if source_time else None,
                    evidence_ref=interpretation,
                )
            )
        return PublishResult(run_id, version_id, bool(reuse), "succeeded")


def producer_sources():
    """仅首链必要实现的版本内容；无配置文件、凭据、依赖或其他业务域。"""
    root = Path(__file__).resolve().parents[2]
    names = [
        "modules/sales/contracts.py",
        "modules/sales/source_policy.py",
        "modules/sales/normalization.py",
        "modules/sales/storage.py",
        "connectors/shop_daily.py",
        "jobs/sync_shop_daily.py",
    ]
    return {name: (root / name).read_text() for name in names}


def producer_revision():
    return "sha256:" + fingerprint(LOADED_PRODUCER_SOURCES)


LOADED_PRODUCER_SOURCES = producer_sources()
