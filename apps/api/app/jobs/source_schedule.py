"""固定时点来源编排；平台只需注入时刻、只读探针、接收器和数据库。"""

import json
import time
from datetime import UTC, datetime, timedelta

from app.jobs.source_monitor import fingerprint
from app.modules.data_status.checkpoint_store import CheckpointStore
from app.modules.data_status.schedule_policy import (
    FEISHU_TIMES,
    WAREHOUSE_SOURCES,
    WAREHOUSE_TIMES,
    due_sources,
)


def run_scheduled_sources(
    engine,
    probes,
    synchronize,
    *,
    now=None,
    warehouse_times=WAREHOUSE_TIMES,
    feishu_times=FEISHU_TIMES,
    force=False,
):
    """同槽去重、失败下槽再试；synchronize(source_ids,持锁连接)返回各来源接收结果。

    16:10仍执行获准范围对账。探针失败不伪造修订；对账成功后也须重验才推进水位。
    源成功与派生处理成功独立，派生失败不会使下一槽重复下载未变化来源。
    """

    def clock():
        return now or datetime.now(UTC)

    started = clock()
    slots = due_sources(started, warehouse_times, feishu_times)
    if not set(probes) <= set((*WAREHOUSE_SOURCES, "shop_refunds", "sales_targets")):
        raise ValueError("INVALID_SCHEDULE_SCOPE")
    store = CheckpointStore(engine)
    with store.locked() as c:
        if c is None:
            return {"status": "already_running", "checked": 0}
        previous = store.read(c)
        observed, selected = {}, []

        def write(code, **values):
            store.write(c, code, **values)

        def observe(code):
            tick = time.monotonic()
            write(code, checked_at=clock(), check_status="checking", error_code=None)
            try:
                value = probes[code]()
                if not isinstance(value, dict) or len(json.dumps(value, default=str)) > 16000:
                    raise ValueError("INVALID_OBSERVATION")
                digest = fingerprint(value)
                web = {}
                if value.get("basis") in ("warehouse_web_metadata", "warehouse_web_page"):
                    web = dict(web_updated_at=value["updated_at"], web_checked_at=clock())
                write(
                    code,
                    checked_at=clock(),
                    check_status="succeeded",
                    observed_at=clock(),
                    observation=value,
                    observed_fingerprint=digest,
                    error_code=None,
                    duration_ms=int((time.monotonic() - tick) * 1000),
                    **web,
                )
                return digest
            except Exception as exc:
                write(
                    code,
                    checked_at=clock(),
                    check_status="failed",
                    error_code=(
                        "WAREHOUSE_WEB_LOGIN_REQUIRED"
                        if getattr(exc, "code", None) == "WAREHOUSE_WEB_LOGIN_REQUIRED"
                        else "CHECK_FAILED"
                    ),
                    duration_ms=int((time.monotonic() - tick) * 1000),
                )
                return None

        for code in probes:
            slot = slots.get(code)
            if slot is None and not force:
                continue
            prior = previous.get(code, {})
            same = prior.get("scheduled_slot_at") == slot
            interrupted = (
                prior.get("sync_status") == "running" or prior.get("check_status") == "checking"
            )
            if (
                not force
                and same
                and not (
                    interrupted
                    and started >= prior["checked_at"] + timedelta(minutes=20)
                    and prior.get("scheduled_attempts", 0) < 2
                )
            ):
                continue
            if not force:
                write(
                    code,
                    checked_at=clock(),
                    check_status="checking",
                    scheduled_slot_at=slot,
                    scheduled_attempts=prior.get("scheduled_attempts", 0) + 1 if same else 1,
                )
            digest = observe(code)
            observed[code] = digest
            reconcile = force or (
                code in WAREHOUSE_SOURCES and slot and slot.strftime("%H:%M") == "16:10"
            )
            if reconcile or (
                digest
                and (
                    digest != prior.get("accepted_fingerprint")
                    or prior.get("sync_status") in ("failed", "running")
                )
            ):
                selected.append(code)
                write(code, sync_status="running", sync_started_at=clock(), sync_finished_at=None)
        if not observed:
            return {"status": "checked", "checked": 0, "sources": []}
        try:
            results = synchronize(tuple(selected), c)
        except Exception:
            results = {"_processing_failed": True}
        for code in selected:
            succeeded = bool(results.get(code))
            write(
                code, sync_status="succeeded" if succeeded else "failed", sync_finished_at=clock()
            )
            if not succeeded:
                write(code, error_code="SYNC_FAILED")
                continue
            digest = observed[code]
            after = observe(code)
            if digest and after == digest:
                write(code, accepted_fingerprint=digest, accepted_at=clock(), error_code=None)
            elif after:
                write(code, error_code="SOURCE_CHANGED_DURING_SYNC")
        from app.modules.data_status.repository import refresh_platform_snapshots

        refresh_platform_snapshots(c)
        current = store.read(c)
        failures = sum(
            current[k]["check_status"] == "failed" or current[k].get("error_code") == "SYNC_FAILED"
            for k in observed
        )
        pending = sum(
            current[k].get("error_code") == "SOURCE_CHANGED_DURING_SYNC" for k in observed
        )
        processing_failed = bool(results.get("_processing_failed"))
        return {
            "status": "partial" if failures or pending or processing_failed else "checked",
            "checked": len(observed),
            "failed": failures,
            "pending": pending,
            "sources": selected,
            "processing_failed": processing_failed,
        }
