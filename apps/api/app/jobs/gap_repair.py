"""在项目会话锁内按来源登记每日补缺，保留普通及周对账的独立水位。"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.modules.data_status.schedule_policy import DAILY_GAP_SOURCES, SOURCE_RETRY_SECONDS

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


def due_gap_day(code, prior, now, warehouse_times):
    """当日最后一个数仓槽起检查一次；失败或进程中断后按持久化时刻限频重试。"""
    if not prior or code not in DAILY_GAP_SOURCES or not warehouse_times:
        return None
    local = now.astimezone(LOCAL_ZONE)
    day = local.date()
    state = prior.get("platform_snapshot", {}).get("gap_repair", {})
    pending = state.get("status") in ("running", "failed")
    if pending and now < datetime.fromisoformat(state["at"]) + timedelta(
        seconds=SOURCE_RETRY_SECONDS
    ):
        return None
    if local.strftime("%H:%M") < max(warehouse_times):
        previous_day = day - timedelta(days=1)
        return previous_day if pending and state["day"] == previous_day.isoformat() else None
    if state.get("day") == day.isoformat() and not pending:
        return None
    return day


def write_gap_state(c, code, day, now, status, *, date_count=0):
    """调用方持项目锁及短事务，只改快照中的补缺字段。"""
    c.execute(
        sa.text("""
        UPDATE source_checkpoints
        SET platform_snapshot=jsonb_set(platform_snapshot, '{gap_repair}', CAST(:state AS jsonb))
        WHERE source_id=:code
    """),
        {
            "code": code,
            "state": json.dumps(
                {
                    "day": day.isoformat(),
                    "at": now.isoformat(),
                    "status": status,
                    "date_count": date_count,
                }
            ),
        },
    )


class DailyGapRepair:
    """当前项目锁内各来源的补缺意图；进程间去重和重试以数据库中的日期、时刻为准。"""

    def __init__(self, candidates, warehouse_times, clock):
        self.candidates = candidates
        self.warehouse_times = warehouse_times
        self.clock = clock
        self.dates = {}
        self.days = {}
        self.failures = set()

    def plan(self, c, code, prior, started, cycle, weekly_slot):
        if self.candidates is None:
            return
        day = due_gap_day(code, prior, started, self.warehouse_times)
        weekly_owned = cycle and cycle["status"] in ("running", "failed", "deferred")
        if not day or (weekly_owned and weekly_slot is None):
            return
        # 首次周全量可能早于普通检查点的建立；其成功证据同样覆盖当日已到期补缺。
        weekly_success = (cycle or {}).get("last_success_at")
        if weekly_success:
            local = weekly_success.astimezone(LOCAL_ZONE)
            if local.date() == day and local.strftime("%H:%M") >= max(self.warehouse_times):
                return
        self.days[code] = day
        with c.begin():
            write_gap_state(c, code, day, self.clock(), "running")
        if weekly_slot is not None:
            # 周全量将稳定重读整个批准范围；共用接收及其结果。
            return
        try:
            dates = self.candidates(c, code, day)
        except Exception:
            if c.in_transaction():
                c.rollback()
            with c.begin():
                self.finish(c, code, False)
            return
        if dates:
            self.dates[code] = tuple(dates)
        else:
            with c.begin():
                write_gap_state(
                    c, code, day, self.clock(), "no_baseline" if dates is None else "succeeded"
                )
            del self.days[code]

    def finish(self, c, code, stable):
        """与同轮普通/周接收的结算共用短事务；永不改动它们的成功水位。"""
        if code not in self.days:
            return
        day = self.days.pop(code)
        if not stable:
            self.failures.add(code)
        write_gap_state(
            c,
            code,
            day,
            self.clock(),
            "succeeded" if stable else "failed",
            date_count=len(self.dates.get(code, ())),
        )
