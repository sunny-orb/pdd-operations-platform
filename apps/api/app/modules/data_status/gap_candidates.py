"""核对当前发布版本和上游在同一店铺日的覆盖，只把可补的日期交给接收器。"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.modules.product_board.incremental import missing_product_shop_days
from app.modules.sales.source_policy import SHOPS, approved_window

from .history_coverage import WINDOW_DAYS, collect_coverage, history_status
from .repository import versions
from .schedule_policy import DAILY_GAP_SOURCES

UPSTREAM_IDS = {values[0]: shop for shop, values in SHOPS.items()}


def find_repair_dates(connection, code, today, probe):
    """无发布基线返回 None；查询失败上抛。常规同步处理昨日，此处核对之前的缺日。"""
    if code not in DAILY_GAP_SOURCES:
        raise ValueError("INVALID_GAP_SOURCE")
    now = datetime.combine(today, time(12), ZoneInfo("Asia/Shanghai"))
    window = approved_window(code)
    start = max(window.start, today - timedelta(days=WINDOW_DAYS))
    end = min(window.end, today - timedelta(days=2))
    with connection.begin():
        connection.execute(sa.text("SET LOCAL statement_timeout='10s'"))
        heads = versions(connection)
        if code not in heads:
            return None
        if start > end:
            return ()
        if code == "product_promotion":
            days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
            gaps = missing_product_shop_days(connection, code, days)
        else:
            saved = connection.scalar(
                sa.text(
                    "SELECT platform_snapshot->'history' FROM source_checkpoints "
                    "WHERE source_id=:code"
                ),
                {"code": code},
            )
            summary = history_status(code, saved, heads, now)
            if summary["state"] not in ("observed", "gaps"):
                summary = collect_coverage(connection, code, heads, now)
            gaps = defaultdict(list)
            for group in summary.get("groups", ()):
                for value in group["dates"]:
                    day = date.fromisoformat(value)
                    if start <= day <= end:
                        gaps[day].append(UPSTREAM_IDS[group["shop_id"]])
            gaps = {day: tuple(shops) for day, shops in gaps.items()}
    return probe.available_gap_days(code, gaps) if gaps else ()
