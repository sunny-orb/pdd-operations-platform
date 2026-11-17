"""来源触发规则：仅计算逻辑槽，不读库、不访问上游、不依赖运行平台。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WAREHOUSE_TIMES = ("08:30", "09:40", "10:10", "10:50", "13:10", "15:10", "16:10")
FEISHU_TIMES = ("08:20", "10:10")
SOURCE_RETRY_SECONDS = 300
RECONCILIATION_TIME = "17:00"
RECONCILIATION_RETRY_SECONDS = 300
WAREHOUSE_SOURCES = (
    "shop_daily",
    "product_daily",
    "product_promotion",
    "profile_records",
    "shop_ratings",
    "shop_channel_product_promotion",
    "shop_channel_star_shop",
    "shop_regions",
)
RECONCILIATION_SOURCES = (*WAREHOUSE_SOURCES, "product_regions")
FEISHU_SOURCES = ("shop_refunds", "sales_targets")
MONITOR_ONLY_SOURCES = ("aftersales_rpa",)
JST_AFTERSALES_SOURCES = ("jst_returns", "jst_sales", "jst_master")
# 退款目标被RPA共用，不随旧JST采集一起停用。
JST_AFTERSALES_STEPS = (
    "jst_returns", "jst_shipped_sales", "jst_cancelled_sales", "aftersales_report",
)
LEGACY_SOURCES = (
    "product_regions",
    "jst_returns",
    "jst_sales",
    "jst_master",
    "weather_bulletin",
    "weather_mid_range",
)
LEGACY_STEPS = (
    "product_regions",
    "jst_returns",
    "jst_shipped_sales",
    "jst_cancelled_sales",
    "refund_targets",
    "aftersales_report",
    "weather",
)


def source_times(code, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES):
    return (
        warehouse_times
        if code in WAREHOUSE_SOURCES
        else feishu_times
        if code in FEISHU_SOURCES
        else ()
    )


def due_sources(now, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES):
    """漏过同日多个槽只返回最近槽；跨日不补旧日，上游检查/接收成功不改变计划。"""
    if now.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    result = {}
    for code in (*WAREHOUSE_SOURCES, *FEISHU_SOURCES):
        due = [
            t
            for t in source_times(code, warehouse_times, feishu_times)
            if t <= local.strftime("%H:%M")
        ]
        if due:
            result[code] = datetime.fromisoformat(f"{local.date()}T{max(due)}+08:00")
    return result


def next_check(code, now, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES):
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    times = source_times(code, warehouse_times, feishu_times)
    if not times:
        return None
    later = [t for t in times if t > local.strftime("%H:%M")]
    day = local.date() if later else local.date() + timedelta(days=1)
    return datetime.fromisoformat(f"{day}T{min(later or times)}+08:00")


def first_reconciliation(now, time=RECONCILIATION_TIME):
    """首次启用只安排本地当天周日或最近未来的周日，不补部署前的日期。"""
    if now.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    sunday = day + timedelta(days=(6 - day.weekday()) % 7)
    return datetime.fromisoformat(f"{sunday}T{time}+08:00")


def next_reconciliation(
    cycle, time=RECONCILIATION_TIME, retry_seconds=RECONCILIATION_RETRY_SECONDS
):
    """由持久状态推导下一次到期。运行中由持锁执行者结算或由接手者恢复。"""
    if cycle["status"] == "running":
        return None
    if cycle["status"] == "waiting":
        return cycle["cycle_at"]
    finished = cycle["last_finished_at"]
    if cycle["status"] in ("failed", "deferred"):
        return finished + timedelta(seconds=retry_seconds)
    sunday = first_reconciliation(finished, time)
    return sunday if sunday > finished else sunday + timedelta(days=7)


def unconfirmed_receipt(checkpoint):
    """发布完成后前后标记未核对稳定，普通检查仍须复验这次接收。"""
    if checkpoint.get("sync_status") != "succeeded":
        return False
    accepted = checkpoint.get("accepted_at")
    return accepted is None or accepted < checkpoint["sync_finished_at"]


def source_retry_at(checkpoint):
    """检查失败、接收失败或待复核时，从最近结束的检查/接收等待五分钟。"""
    if (
        checkpoint.get("check_status") != "failed"
        and checkpoint.get("sync_status") != "failed"
        and not unconfirmed_receipt(checkpoint)
    ):
        return None
    finished = checkpoint.get("sync_finished_at")
    checked = checkpoint.get("checked_at")
    return max(stamp for stamp in (finished, checked) if stamp is not None) + timedelta(
        seconds=SOURCE_RETRY_SECONDS
    )


def receipt_after_attempt(checkpoint, task):
    """后续稳定接收可覆盖旧失败尝试；不改写原任务，也不把元数据成功当接收。"""
    accepted = checkpoint.get("accepted_at")
    ended = task.get("finished_at") or task.get("lease_expires_at")
    return bool(accepted and ended and accepted >= ended)
