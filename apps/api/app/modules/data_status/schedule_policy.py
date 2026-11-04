"""来源触发规则：仅计算逻辑槽，不读库、不访问上游、不依赖运行平台。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WAREHOUSE_TIMES = ("08:20", "09:10", "10:10", "10:50", "13:10", "15:10", "16:10")
FEISHU_TIMES = ("08:20", "10:10")
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
FEISHU_SOURCES = ("shop_refunds", "sales_targets")
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
