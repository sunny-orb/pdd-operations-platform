"""项目独立调度：配置钟点、PG跨进程互斥/同点去重、串行来源及安全结果。"""

import json
import os
import re
import time
import tomllib
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.modules.data_status.schedule_policy import (
    FEISHU_TIMES,
    RECONCILIATION_RETRY_SECONDS,
    RECONCILIATION_TIME,
    WAREHOUSE_TIMES,
)
from app.modules.database_residency import deferred_reason


@dataclass(frozen=True)
class Schedule:
    times: tuple[str, ...] = ("10:00", "14:00", "18:00")
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    max_attempts_per_slot: int = 2
    monitor_enabled: bool = False
    monitor_interval_seconds: int = 900
    source_schedule_enabled: bool = False
    warehouse_times: tuple[str, ...] = WAREHOUSE_TIMES
    feishu_times: tuple[str, ...] = FEISHU_TIMES
    reconciliation_time: str = RECONCILIATION_TIME
    reconciliation_retry_seconds: int = RECONCILIATION_RETRY_SECONDS

    def __post_init__(self):
        if (
            self.timezone != "Asia/Shanghai"
            or not self.times
            or len(set(self.times)) != len(self.times)
            or any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in self.times)
            or not 1 <= self.max_attempts_per_slot <= 3
            or not 300 <= self.monitor_interval_seconds <= 3600
        ):
            raise ValueError("INVALID_SYNC_SCHEDULE")
        for times in (self.warehouse_times, self.feishu_times):
            if (
                not times
                or len(set(times)) != len(times)
                or any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in times)
            ):
                raise ValueError("INVALID_SOURCE_SCHEDULE")
        if (
            not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.reconciliation_time)
            or not isinstance(self.reconciliation_retry_seconds, int)
            or self.reconciliation_retry_seconds < RECONCILIATION_RETRY_SECONDS
        ):
            raise ValueError("INVALID_RECONCILIATION_SCHEDULE")


def read_schedule(path):
    raw = tomllib.loads(Path(path).read_text())
    for key in ("times", "warehouse_times", "feishu_times"):
        if key in raw:
            raw[key] = tuple(raw[key])
    return Schedule(**raw)


def due_slot(config, now):
    """同日漏点合并为最近到点的一次；开机前的历史日不制造无限补跑。"""
    if not config.enabled:
        return None
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    local = now.astimezone(ZoneInfo(config.timezone))
    due = [t for t in config.times if t <= local.strftime("%H:%M")]
    return f"{local.date()}T{max(due)}+08:00" if due else None


def run_project(
    engine, slot, steps, archive_root, *, max_attempts=2, depends_on=None, lock_connection=None
):
    """每个step串行返回来源结果；失败不抹去其它来源成功，日志不含异常原文。"""
    m = sa.MetaData()
    tasks = sa.Table("task_runs", m, schema="public", autoload_with=engine, resolve_fks=False)
    run = str(uuid4())
    evidence = Path(archive_root) / f"{run}.json"
    with nullcontext(lock_connection) if lock_connection is not None else engine.connect() as c:
        locked = c.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext('project-source-sync'))")
        ).scalar_one()
        c.commit()
        if not locked:
            return dict(status="already_running")
        try:
            with c.begin():
                previous = (
                    c.execute(
                        sa.select(tasks.c.status).where(
                            tasks.c.job_code == "sync_project", tasks.c.request_ref == slot
                        )
                    )
                    .scalars()
                    .all()
                )
                if "succeeded" in previous:
                    return dict(status="already_succeeded")
                if len(previous) >= max_attempts:
                    return dict(status="retry_budget_exhausted")
                # 已拿到全局锁，旧running的进程不再持有本作用域。
                c.execute(
                    tasks.update()
                    .where(tasks.c.job_code == "sync_project", tasks.c.status == "running")
                    .values(
                        status="abandoned",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        error_code="WORKER_LOST",
                    )
                )
                now = c.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                c.execute(
                    tasks.insert().values(
                        id=run,
                        job_code="sync_project",
                        request_ref=slot,
                        lock_key="project-source-sync",
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=run,
                        lease_expires_at=now + timedelta(hours=2),
                    )
                )
            results, version = {}, None
            for name, step in steps.items():
                if any(
                    results.get(k, {}).get("status") != "succeeded"
                    for k in (depends_on or {}).get(name, ())
                ):
                    results[name] = dict(status="failed", error="DEPENDENCY_FAILED")
                    continue
                started = time.monotonic()
                with c.begin():
                    c.execute(
                        tasks.update()
                        .where(tasks.c.id == run)
                        .values(
                            lease_expires_at=sa.func.clock_timestamp()
                            + sa.text("interval '2 hours'")
                        )
                    )
                try:
                    result = step()
                    if result.get("status") in ("failed", "cancelled"):
                        raise ValueError("SOURCE_STEP_FAILED")
                    version = (
                        result.get("version_id")
                        or result.get("versions", {}).get("profile_links")
                        or version
                    )
                    results[name] = dict(status="succeeded", result=result)
                except Exception as exc:
                    from app.connectors.retry import safe_diagnostic

                    # 错误诊断仅保留白名单字段，不记录查询参数和完整业务行。
                    results[name] = dict(
                        status="failed",
                        error=type(exc).__name__,
                        error_code=deferred_reason(exc),
                        error_module=type(exc).__module__,
                        diagnostic=safe_diagnostic(exc),
                        source_code=exc.args[0]
                        if exc.args and isinstance(exc.args[0], int)
                        else None,
                    )
                results[name]["elapsed_seconds"] = round(time.monotonic() - started, 3)
                print(
                    json.dumps(
                        dict(run_id=run, dataset=name, **results[name]),
                        ensure_ascii=False,
                        default=str,
                    ),
                    flush=True,
                )
            failed = not results or any(v["status"] == "failed" for v in results.values())
            failed = failed or version is None
            report = dict(
                status="failed" if failed else "succeeded", run_id=run, slot=slot, datasets=results
            )
            evidence.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            evidence.write_text(json.dumps(report, ensure_ascii=False, default=str))
            os.chmod(evidence, 0o600)
            with c.begin():
                c.execute(
                    tasks.update()
                    .where(tasks.c.id == run)
                    .values(
                        status=report["status"],
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        result_version_id=version,
                        error_code="SOURCE_STEP_FAILED" if failed else None,
                        evidence_ref=str(evidence),
                    )
                )
            return report
        finally:
            if c.in_transaction():
                c.rollback()
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
            c.commit()


def run_guarded(engine, command):
    """历史手工CLI也遵守项目全局互斥；内部reader/store仍使用各域锁与租约。"""
    with engine.connect() as c:
        locked = c.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext('project-source-sync'))")
        ).scalar_one()
        c.commit()
        if not locked:
            print(json.dumps(dict(status="already_running")))
            return 1
        try:
            return command()
        finally:
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
            c.commit()
