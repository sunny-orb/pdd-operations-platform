"""M3已核身份、字段及首期预算；阈值和来源解释在此集中。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SHOPS = {
    "663599807": ("spigf", "kocotree官方旗舰店", "官方店"),
    "799193231": ("spihw", "kocotree运动户外旗舰店", "户外店"),
    "191467219": ("spifs", "kocotree儿童服饰旗舰店", "服饰店"),
    "343339448": ("spiyj", "kocotree旗舰店", "雨具店"),
    "990012831": ("spixb", "kocotree母婴玩具旗舰店", "箱包店"),
    "271470254": ("spitztx", "kocotree童装童鞋旗舰店", "童装童鞋店"),
}
METRICS = {
    "成交金额": "shop_revenue",
    "店铺访客数": "shop_visitors",
    "成交买家数": "buyers",
    "成交订单数": "orders",
    "店铺浏览量": "shop_views",
    "商品访客数": "product_visitors",
    "商品浏览量": "product_views",
    "客单价": "source_aov",
    "成交UV价值": "source_uv_value",
    "成交转化率": "source_conversion_rate",
}
CORE = tuple(list(METRICS)[:4])
MISSING_MARKERS = ("-", "--", "—")
APPROVED_START = date(2026, 2, 1)
FISCAL_END = date(2027, 1, 31)
SOURCE_TABLE = "ods_rpa_pdd_traffic_kanban_overview_f"
POLICY_REVISION = "m3-shop-daily-v2"
PAGE_SIZE, MAX_ROWS, MAX_ATTEMPTS = 500, 10000, 2
LEASE_SECONDS, HEARTBEAT_SECONDS = 120, 30


def calendar_through() -> date:
    return min(datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1), FISCAL_END)


def approved_window():
    from .contracts import ReadWindow

    return ReadWindow(APPROVED_START, calendar_through(), tuple(SHOPS))


def validate_read_window(window):
    """所有来源读取均限定当次批准日历；只读核验也不能读取未来日期。"""
    from .contracts import SalesError

    approved = approved_window()
    if not approved.start <= window.start <= window.end <= approved.end:
        raise SalesError("SOURCE_WINDOW_NOT_APPROVED")
