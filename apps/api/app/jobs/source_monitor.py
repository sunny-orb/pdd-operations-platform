"""有限频率来源检查；全局PG锁内串行，同步验证成功才推进接收标记。"""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.modules.data_status.catalog import BY_ID, job_for
from app.modules.data_status.checkpoint_store import (
    CheckpointStore,
    reconciliation_owned_sources,
    recover_interrupted_checks,
)
from app.modules.data_status.failure_codes import SYNC_FAILURE_CODES, safe_failure_code
from app.modules.data_status.schedule_policy import (
    RECONCILIATION_TIME,
    SOURCE_RETRY_SECONDS,
    receipt_after_attempt,
    source_retry_at,
)
from app.modules.database_residency import deferred_reason


def fingerprint(value):
    # HTTP和旧可见网页是同一tableId/dataLastUpdateTime证据；切换读取方式不制造接收副本。
    if "upstream_day" in value:
        value = {k: v for k, v in value.items() if k != "upstream_day"}
    if value.get("basis") == "warehouse_web_metadata":
        value = {**value, "basis": "warehouse_web_page"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def run_monitor(
    engine,
    probes,
    synchronize,
    *,
    interval_seconds=900,
    now=None,
    reconciliation_sources=(),
    reconciliation_time=RECONCILIATION_TIME,
    receiving_sources=(),
):
    """注入来源检查函数和同步器(groups,持锁连接)；外部异常只落固定错误码。

    首次检查也必须实际接收，不能用观测时间认定已有平台版本与上游相同。
    synchronize返回每组来源是否成功，派生计算失败由产物状态单独表达。
    """
    if (
        interval_seconds < 60
        or not set(probes) <= BY_ID.keys()
        or not set(receiving_sources) <= probes.keys()
    ):
        raise ValueError("INVALID_MONITOR_SCOPE")

    def clock():
        return now or datetime.now(UTC)

    def receives(code):
        return not BY_ID[code].monitor_only or code in receiving_sources

    started_at = clock()
    store = CheckpointStore(engine)
    table = store.table
    with engine.connect() as c:
        locked = c.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext('project-source-sync'))"))
        c.commit()
        if not locked:
            return {"status": "already_running"}

        def write(code, **values):
            with c.begin():
                store.write(c, code, **values)

        def observe(code, *, retry_error=None):
            started = time.monotonic()
            write(code, checked_at=clock(), check_status="checking", error_code=None)
            try:
                value = probes[code]()
                # 只接受小型元信息，禁止将整行经营数据写入状态表。
                if not isinstance(value, dict) or len(json.dumps(value, default=str)) > 16000:
                    raise ValueError("INVALID_OBSERVATION")
                digest = fingerprint(value)
                if BY_ID[code].monitor_only:
                    before = previous.get(code, {}).get("observed_fingerprint")
                    value = {
                        **value,
                        "changed_since_previous_check": digest != before if before else None,
                    }
                stamp = clock()
                write(
                    code,
                    checked_at=stamp,
                    check_status="succeeded",
                    observed_at=stamp,
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
                    error_code=retry_error or "CHECK_FAILED",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                return None

        try:
            with c.begin():
                weekly_owned = reconciliation_owned_sources(
                    c, reconciliation_sources, started_at, reconciliation_time
                )
                probes = {code: probe for code, probe in probes.items() if code not in weekly_owned}
                previous = {r["source_id"]: dict(r) for r in c.execute(sa.select(table)).mappings()}
                recover_interrupted_checks(c, table, previous, probes, started_at)
                tasks = {
                    r["job_code"]: r
                    for r in c.execute(
                        sa.text("""
                    SELECT DISTINCT ON(job_code) job_code,status,lease_expires_at,finished_at
                    FROM task_runs
                    ORDER BY job_code,queued_at DESC,id DESC
                """)
                    ).mappings()
                }
            observed, groups = {}, []
            retry_groups = {}
            retry_interval = timedelta(seconds=SOURCE_RETRY_SECONDS)
            for code in probes:
                if not receives(code):
                    continue
                prior = previous.get(code)
                failed_tasks = [
                    task
                    for job in (job_for(dataset) for dataset in BY_ID[code].datasets)
                    if (task := tasks.get(job))
                    and (
                        task["status"] in ("failed", "abandoned", "cancelled")
                        or task["status"] == "running"
                        and task["lease_expires_at"] <= started_at
                    )
                    and not receipt_after_attempt(prior or {}, task)
                ]
                retry_error = (prior or {}).get("error_code")
                if not failed_tasks and retry_error not in SYNC_FAILURE_CODES | {
                    "SOURCE_CHANGED_DURING_SYNC"
                }:
                    continue
                checked = (prior or {}).get("checked_at")
                stamps = [task["finished_at"] for task in failed_tasks if task["finished_at"]]
                if checked:
                    stamps.append(checked)
                group = BY_ID[code].group
                retry_groups[group] = max(
                    max(stamps) + retry_interval if stamps else started_at,
                    retry_groups.get(group, started_at),
                )

            for code in probes:
                prior = previous.get(code) or {}
                group = BY_ID[code].group
                retry_at = retry_groups.get(group)
                check_retry_at = (
                    source_retry_at(prior) if prior.get("check_status") == "failed" else None
                )
                due_retry = max(
                    (stamp for stamp in (retry_at, check_retry_at) if stamp is not None),
                    default=None,
                )
                checked = prior.get("checked_at")
                if due_retry is not None:
                    if started_at < due_retry:
                        continue
                elif checked and started_at < checked + timedelta(seconds=interval_seconds):
                    continue
                digest = observe(
                    code,
                    retry_error=(
                        "SOURCE_CHANGED_DURING_SYNC"
                        if prior.get("error_code") == "SOURCE_CHANGED_DURING_SYNC"
                        else safe_failure_code(prior.get("error_code"))
                    )
                    if retry_at is not None
                    else None,
                )
                observed[code] = digest
                if (
                    digest
                    and receives(code)
                    and (digest != prior.get("accepted_fingerprint") or retry_at is not None)
                ):
                    if group not in groups:
                        groups.append(group)
            # 同一一致性组只接收一次；先检查完所有来源，失败来源不误当已追平。
            try:
                results = (
                    synchronize(tuple(groups), c)
                    if any(receives(code) for code in observed)
                    else {}
                )
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
                        code,
                        checked_at=clock(),
                        check_status="succeeded",
                        error_code=safe_failure_code(results.get("_errors", {}).get(group)),
                    )
                    continue
                after = observe(code, retry_error="SOURCE_CHANGED_DURING_SYNC")
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
                r["check_status"] == "failed" or r["error_code"] in SYNC_FAILURE_CODES
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
