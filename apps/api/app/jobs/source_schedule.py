"""固定时点变化接收及每周对账，复用来源读取及项目互斥锁。"""

import json
import time
from datetime import UTC, datetime, timedelta

from app.jobs.source_monitor import fingerprint
from app.modules.data_status.checkpoint_store import CheckpointStore
from app.modules.data_status.schedule_policy import (
    FEISHU_SOURCES,
    FEISHU_TIMES,
    RECONCILIATION_RETRY_SECONDS,
    RECONCILIATION_TIME,
    WAREHOUSE_SOURCES,
    WAREHOUSE_TIMES,
    due_sources,
    first_reconciliation,
    next_reconciliation,
)
from app.modules.database_residency import deferred_reason


def _ordinary_slot(prior, slot, started):
    if slot is None:
        return False
    if prior.get("scheduled_slot_at") != slot:
        return True
    interrupted = (
        prior.get("sync_status") == "running"
        or prior.get("check_status") == "checking"
        or prior.get("error_code") == "MAINTENANCE_DEFERRED"
    )
    return (
        interrupted
        and started >= prior["checked_at"] + timedelta(minutes=20)
        and prior["scheduled_attempts"] < 2
    )


def _probe(read, clock):
    tick = time.monotonic()
    try:
        value = read()
        if not isinstance(value, dict) or len(json.dumps(value, default=str)) > 16000:
            raise ValueError("INVALID_OBSERVATION")
        stamp = clock()
        evidence = {
            "observation": value,
            "observed_fingerprint": fingerprint(value),
            "observed_at": stamp,
        }
        if value.get("basis") in ("warehouse_web_metadata", "warehouse_web_page"):
            evidence.update(web_updated_at=value["updated_at"], web_checked_at=stamp)
        return dict(
            checked_at=stamp,
            check_status="succeeded",
            error_code=None,
            duration_ms=int((time.monotonic() - tick) * 1000),
            **evidence,
        )
    except Exception as exc:
        return dict(
            checked_at=clock(),
            check_status="failed",
            error_code=(
                "WAREHOUSE_WEB_LOGIN_REQUIRED"
                if getattr(exc, "code", None) == "WAREHOUSE_WEB_LOGIN_REQUIRED"
                else "CHECK_FAILED"
            ),
            duration_ms=int((time.monotonic() - tick) * 1000),
        )


def _log_reconciliation(code, cycle_at, status, finished_at, error):
    print(
        json.dumps(
            dict(
                event="source_reconciliation",
                source_id=code,
                cycle_at=cycle_at.isoformat(),
                status=status,
                finished_at=finished_at.isoformat(),
                error_code=error,
            ),
            ensure_ascii=False,
        ),
        flush=True,
    )


def _reconciliation_slot(store, c, code, cycle, now, sunday_time, retry_seconds):
    if cycle is None:
        cycle = dict(cycle_at=first_reconciliation(now, sunday_time), status="waiting")
        with c.begin():
            store.write_reconciliation(c, code, **cycle)
    if cycle["status"] == "running":
        # 当前持有项目锁，旧执行者已中断。真实结束时刻未知，以发现时刻起算。
        with c.begin():
            store.write_reconciliation(
                c, code, status="failed", last_finished_at=now, error_code="WORKER_LOST"
            )
        _log_reconciliation(code, cycle["cycle_at"], "failed", now, "WORKER_LOST")
        return None
    due_at = next_reconciliation(cycle, sunday_time, retry_seconds)
    if now < due_at:
        return None
    return due_at if cycle["status"] == "succeeded" else cycle["cycle_at"]


def run_scheduled_sources(
    engine,
    probes,
    synchronize,
    *,
    now=None,
    clock=None,
    warehouse_times=WAREHOUSE_TIMES,
    feishu_times=FEISHU_TIMES,
    reconciliation_time=RECONCILIATION_TIME,
    reconciliation_retry_seconds=RECONCILIATION_RETRY_SECONDS,
    force=False,
):
    """接收器在持锁连接上串行处理范围；同轮普通与对账共享下载和前后标记。"""
    clock = clock or (lambda: now or datetime.now(UTC))
    started = clock()
    slots = due_sources(started, warehouse_times, feishu_times)
    if not probes.keys() <= set((*WAREHOUSE_SOURCES, *FEISHU_SOURCES)):
        raise ValueError("INVALID_SCHEDULE_SCOPE")
    store = CheckpointStore(engine)
    with store.locked() as c:
        if c is None:
            return {"status": "already_running", "checked": 0}
        previous = store.read(c)
        cycles = store.read(c, store.reconciliations) if not force else {}
        observed, selected, ordinary, weekly = {}, [], {}, {}
        regular_codes = set()

        def observe(code, *, ordinary_check):
            if ordinary_check:
                with c.begin():
                    store.write(
                        c, code, checked_at=clock(), check_status="checking", error_code=None
                    )
            value = _probe(probes[code], clock)
            if ordinary_check:
                with c.begin():
                    store.write(c, code, **value)
            return value

        for code in probes:
            slot = slots.get(code)
            prior = previous.get(code, {})
            regular = force or _ordinary_slot(prior, slot, started)
            cycle = cycles.get(code)
            weekly_slot = (
                _reconciliation_slot(
                    store,
                    c,
                    code,
                    cycle,
                    started,
                    reconciliation_time,
                    reconciliation_retry_seconds,
                )
                if not force and code in WAREHOUSE_SOURCES
                else None
            )
            if not regular and weekly_slot is None:
                continue
            if regular:
                regular_codes.add(code)
            with c.begin():
                if regular and not force:
                    same = prior.get("scheduled_slot_at") == slot
                    store.write(
                        c,
                        code,
                        checked_at=clock(),
                        check_status="checking",
                        scheduled_slot_at=slot,
                        scheduled_attempts=prior["scheduled_attempts"] + 1 if same else 1,
                    )
                if weekly_slot is not None:
                    weekly[code] = (weekly_slot, clock())
                    store.write_reconciliation(
                        c,
                        code,
                        cycle_at=weekly_slot,
                        status="running",
                        last_attempt_at=weekly[code][1],
                        last_finished_at=None,
                    )
            before = observe(code, ordinary_check=regular)
            observed[code] = before
            digest = before.get("observed_fingerprint")
            if force or (
                regular
                and digest
                and (
                    digest != prior.get("accepted_fingerprint")
                    or prior.get("sync_status") in ("failed", "running")
                )
            ):
                ordinary[code] = clock()
                with c.begin():
                    store.write(
                        c,
                        code,
                        sync_status="running",
                        sync_started_at=ordinary[code],
                        sync_finished_at=None,
                    )
            if code in ordinary or code in weekly:
                selected.append(code)
        if not observed:
            return {"status": "checked", "checked": 0, "sources": []}
        try:
            results = synchronize(tuple(selected), c)
        except Exception as exc:
            reason = deferred_reason(exc)
            results = (
                {"_deferred": {code: reason for code in selected}}
                if reason
                else {"_processing_failed": True}
            )
        deferred = results.get("_deferred", {})
        weekly_failed = set()
        for code in selected:
            before = observed[code]
            accepted = before.get("observed_fingerprint")
            succeeded = bool(results.get(code)) and code not in deferred
            after = observe(code, ordinary_check=code in ordinary) if succeeded else {}
            stable = bool(accepted and after.get("observed_fingerprint") == accepted)
            finished = clock()
            prior = previous.get(code, {})
            with c.begin():
                if code in ordinary:
                    if code in deferred:
                        store.write(
                            c,
                            code,
                            sync_status=prior.get("sync_status"),
                            sync_started_at=prior.get("sync_started_at"),
                            sync_finished_at=prior.get("sync_finished_at"),
                            error_code=deferred[code],
                        )
                    else:
                        values = dict(
                            sync_status="succeeded" if succeeded else "failed",
                            sync_finished_at=finished,
                        )
                        if stable:
                            values.update(accepted_fingerprint=accepted, accepted_at=finished)
                        if not succeeded:
                            values["error_code"] = "SYNC_FAILED"
                        elif not stable and after.get("observed_fingerprint"):
                            values["error_code"] = "SOURCE_CHANGED_DURING_SYNC"
                        store.write(c, code, **values)
                if code in weekly:
                    weekly_slot, attempted = weekly[code]
                    if stable and code not in ordinary:
                        # 稳定证据可接受，但只关闭已确认解决的普通失败。
                        values = dict(after, accepted_fingerprint=accepted, accepted_at=finished)
                        if prior or code in regular_codes:
                            for key in ("checked_at", "check_status", "duration_ms", "error_code"):
                                values.pop(key)
                        if (
                            prior.get("sync_status") in ("failed", "running")
                            and prior.get("observed_fingerprint") == accepted
                        ):
                            values.update(
                                sync_status="succeeded",
                                sync_started_at=attempted,
                                sync_finished_at=finished,
                            )
                            if prior["error_code"] in ("SYNC_FAILED", "SOURCE_CHANGED_DURING_SYNC"):
                                values["error_code"] = None
                        store.write(c, code, **values)
                    error = (
                        deferred[code]
                        if code in deferred
                        else "SYNC_FAILED"
                        if not succeeded
                        else None
                        if stable
                        else before["error_code"]
                        or after["error_code"]
                        or "SOURCE_CHANGED_DURING_SYNC"
                    )
                    status = "deferred" if code in deferred else "failed" if error else "succeeded"
                    success = {"last_success_at": finished} if stable else {}
                    store.write_reconciliation(
                        c,
                        code,
                        status=status,
                        last_finished_at=finished,
                        error_code=error,
                        **success,
                    )
            if code in weekly:
                _log_reconciliation(code, weekly_slot, status, finished, error)
                if status == "failed":
                    weekly_failed.add(code)
        from app.modules.data_status.repository import refresh_platform_snapshots

        refresh_platform_snapshots(c)
        current = store.read(c)
        failures = weekly_failed | {
            code
            for code in regular_codes
            if current[code]["check_status"] == "failed"
            or current[code].get("error_code") == "SYNC_FAILED"
        }
        pending = sum(
            current[code].get("error_code") == "SOURCE_CHANGED_DURING_SYNC"
            for code in regular_codes
        )
        processing_failed = bool(results.get("_processing_failed"))
        return {
            "status": "deferred"
            if deferred
            else "partial"
            if failures or pending or processing_failed
            else "checked",
            "checked": len(observed),
            "failed": len(failures),
            "pending": pending + len(deferred),
            "sources": selected,
            "processing_failed": processing_failed,
        }
