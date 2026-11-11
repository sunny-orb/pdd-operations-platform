"""有限频率来源检查；全局PG锁内串行，同步验证成功才推进接收标记。"""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.modules.data_status.catalog import BY_ID, job_for
from app.modules.database_residency import deferred_reason


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def run_monitor(engine, probes, synchronize, *, interval_seconds=900, now=None):
    """注入来源检查函数和同步器(groups,持锁连接)；外部异常只落固定错误码。

    首次检查也必须实际接收，不能用观测时间认定已有平台版本与上游相同。
    synchronize返回每组来源是否成功，派生计算失败由产物状态单独表达。
    """
    if interval_seconds < 60 or not set(probes) <= BY_ID.keys():
        raise ValueError("INVALID_MONITOR_SCOPE")

    def clock():
        return now or datetime.now(UTC)

    started_at = clock()
    table = sa.Table("source_checkpoints", sa.MetaData(), schema="public", autoload_with=engine)
    with engine.connect() as c:
        locked = c.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext('project-source-sync'))"))
        c.commit()
        if not locked:
            return {"status": "already_running"}

        def write(code, **values):
            with c.begin():
                result = c.execute(table.update().where(table.c.source_id == code).values(**values))
                if not result.rowcount:
                    c.execute(table.insert().values(source_id=code, **values))

        def observe(code):
            started = time.monotonic()
            write(code, checked_at=clock(), check_status="checking", error_code=None)
            try:
                value = probes[code]()
                # 只接受小型元信息，禁止将整行经营数据写入状态表。
                if not isinstance(value, dict) or len(json.dumps(value, default=str)) > 16000:
                    raise ValueError("INVALID_OBSERVATION")
                digest = fingerprint(value)
                write(
                    code,
                    checked_at=clock(),
                    check_status="succeeded",
                    observed_at=clock(),
                    observation=value,
                    observed_fingerprint=digest,
                    error_code=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                return digest
            except Exception:
                write(
                    code,
                    checked_at=clock(),
                    check_status="failed",
                    error_code="CHECK_FAILED",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                return None

        try:
            with c.begin():
                previous = {r["source_id"]: dict(r) for r in c.execute(sa.select(table)).mappings()}
                tasks = {
                    r["job_code"]: r
                    for r in c.execute(
                        sa.text("""
                    SELECT DISTINCT ON(job_code) job_code,status,lease_expires_at FROM task_runs
                    ORDER BY job_code,queued_at DESC,id DESC
                """)
                    ).mappings()
                }
            observed, groups = {}, []
            for code in probes:
                prior = previous.get(code)
                if prior and started_at < prior["checked_at"] + timedelta(seconds=interval_seconds):
                    continue
                digest = observe(code)
                observed[code] = digest
                retry = any(
                    task
                    and (
                        task["status"] in ("failed", "abandoned", "cancelled")
                        or task["status"] == "running"
                        and task["lease_expires_at"] <= started_at
                    )
                    for task in (tasks.get(job_for(code)) for code in BY_ID[code].datasets)
                )
                if digest and (digest != (prior or {}).get("accepted_fingerprint") or retry):
                    group = BY_ID[code].group
                    if group not in groups:
                        groups.append(group)
            # 同一一致性组只接收一次；先检查完所有来源，失败来源不误当已追平。
            try:
                results = synchronize(tuple(groups), c) if observed else {}
            except Exception as exc:
                reason = deferred_reason(exc)
                results = (
                    {"_deferred": {group: reason for group in groups}}
                    if reason
                    else {"_processing_failed": True}
                )
            for code, digest in observed.items():
                group = BY_ID[code].group
                if not digest or group not in groups:
                    continue
                if group in results.get("_deferred", {}):
                    write(code, error_code=results["_deferred"][group])
                    continue
                if not results.get(group):
                    write(
                        code, checked_at=clock(), check_status="succeeded", error_code="SYNC_FAILED"
                    )
                    continue
                after = observe(code)
                if after == digest:
                    write(
                        code,
                        checked_at=clock(),
                        check_status="succeeded",
                        error_code=None,
                        accepted_fingerprint=digest,
                        accepted_at=clock(),
                    )
                elif after:
                    write(
                        code,
                        checked_at=clock(),
                        check_status="succeeded",
                        error_code="SOURCE_CHANGED_DURING_SYNC",
                    )
            from app.modules.data_status.repository import refresh_platform_snapshots

            refresh_platform_snapshots(c)
            from app.modules.data_status.history_coverage import refresh_history_coverage

            refresh_history_coverage(c, now=clock(), source_ids=probes)
            with c.begin():
                checked_rows = (
                    c.execute(sa.select(table).where(table.c.source_id.in_(tuple(observed))))
                    .mappings()
                    .all()
                )
            failed = sum(
                r["check_status"] == "failed" or r["error_code"] == "SYNC_FAILED"
                for r in checked_rows
            )
            pending = sum(
                r["error_code"]
                in (
                    "SOURCE_CHANGED_DURING_SYNC",
                    "DATASET_RESTORE_REQUIRED",
                    "MAINTENANCE_DEFERRED",
                )
                for r in checked_rows
            )
            processing_failed = bool(results.get("_processing_failed"))
            status = (
                "deferred"
                if results.get("_deferred")
                else "failed"
                if observed and failed == len(observed)
                else "partial"
                if failed or processing_failed
                else "checked"
            )
            return dict(
                status=status,
                checked=len(observed),
                failed=failed,
                pending=pending,
                processing_failed=processing_failed,
                groups=groups,
            )
        finally:
            if c.in_transaction():
                c.rollback()
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
            c.commit()
