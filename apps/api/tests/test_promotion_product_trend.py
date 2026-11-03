"""商品趋势真实PG反例：缺商品日不遮挡有值日，同期按当日商品配对。"""

from datetime import date
from decimal import Decimal

from app.modules.promotion.query import PromotionQuery, overview, previous_year
from app.modules.promotion.repository import snapshot
from tests.test_promotion_api import publish_products
from tests.test_promotion_query_performance import promotion_row

pytest_plugins = ["tests.sales_pg_support"]


def test_product_trend_uses_daily_members_and_preserves_missing_zero(sales_pg, tmp_path):
    def row(day, product, spend, revenue):
        return dict(
            promotion_row(date.fromisoformat(day), product, spend=spend), transaction_amount=revenue
        )

    publish_products(
        sales_pg,
        tmp_path,
        [
            row("2026-10-25", "900", "10", "100"),
            row("2026-10-26", "901", None, "70"),
            row("2026-10-27", "900", "0", "0"),
            row("2025-10-24", "900", "99", "999"),
            row("2025-10-25", "900", "2", "20"),
            row("2025-10-25", "901", "90", "900"),
            row("2025-10-26", "901", "0", "10"),
            row("2025-10-26", "900", "100", "1000"),
            row("2025-10-27", "900", "0", "0"),
        ],
    )
    with snapshot(sales_pg, "product") as repo:
        data = overview(repo, PromotionQuery(date(2026, 10, 24), date(2026, 10, 27), "product"))
        empty, first, second, third = data["series"]
        assert data["coverage"]["missing_count"] > 0
        assert empty["missing"] and empty["metrics"]["revenue"]["value"] is None
        assert empty["previous"]["revenue"]["value"] is None
        assert not first["missing"]
        assert first["metrics"]["revenue"]["value"] == Decimal("100")
        assert first["previous"]["revenue"]["value"] == Decimal("20")
        assert first["previous"]["spend"]["value"] == Decimal("2")
        assert not second["missing"]
        assert second["metrics"]["spend"]["value"] is None
        assert second["metrics"]["revenue"]["value"] == Decimal("70")
        assert second["previous"]["revenue"]["value"] == Decimal("10")
        assert second["previous"]["spend"]["value"] == 0
        assert second["previous"]["roi"]["value"] is None
        assert not third["missing"]
        assert third["metrics"]["spend"]["value"] == 0
        assert third["previous"]["revenue"]["value"] == 0
        # 主卡与趋势分别汇总已知金额，空花费成交计入。
        assert data["metrics"]["revenue"]["value"] == Decimal("170")
        assert data["metrics"]["roi"]["value"] == Decimal("17")
        assert first["metrics"]["roi"]["value"] == 10
        assert first["previous"]["roi"]["value"] == 10


def test_previous_year_keeps_calendar_date_and_leap_day_empty():
    assert previous_year(date(2024, 2, 29)) is None
    assert previous_year(date(2026, 1, 1)) == date(2025, 1, 1)
    assert previous_year(date(2025, 12, 31)) == date(2024, 12, 31)
