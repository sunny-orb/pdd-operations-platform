from datetime import date, timedelta
from decimal import Decimal

from app.modules.product_board.metrics import aggregate, compare, risk


def test_paired_denominators_and_zero_are_distinct():
    rows = [
        dict(visitors=10, buyers=None, product_revenue=Decimal("99"), orders=3),
        dict(visitors=20, buyers=2, product_revenue=Decimal("10"), orders=2),
    ]
    value = aggregate(rows)
    assert value["conversion"]["value"] == Decimal(".1")
    assert value["conversion"]["status"] == "partial"
    assert value["aov"]["value"] == Decimal("5")
    assert value["favorites"]["value"] is None
    assert compare(10, 0) is None
    assert compare(0, 10) == Decimal("-1")


def test_risk_first_branch_and_calendar_coverage():
    end = date(2026, 10, 26)
    rows = [
        dict(
            business_date=end - timedelta(days=i),
            visitors=200,
            orders=0 if i == 0 else 5,
            product_revenue=Decimal("100"),
        )
        for i in range(8)
    ]
    value = risk(rows, end, True)
    assert value["priority"] == "P1" and value["label"] == "有流量无成交"
    assert len(value["trend"]) == 7
    assert all(isinstance(v, Decimal) for v in value["trend"])
    assert risk(rows[:-1], end, True) is None
    rows[0]["visitors"] = 50
    value = risk(rows, end, True)
    assert value["priority"] == "P0" and value["label"] == "爆品严重流量下滑"


def test_year_mapping_is_linear_in_observed_rows(monkeypatch):
    from app.modules.product_board import query
    from app.modules.product_board.source import previous_year

    end = date(2026, 10, 26)
    start = end - timedelta(days=30)
    current = [
        dict(
            listing_id=str(p),
            source_shop_id="s",
            source_product_id=str(p),
            source_shop_name="店",
            source_product_name="商品",
            business_date=start + timedelta(days=i),
            product_revenue=Decimal("1"),
            visitors=10,
            orders=1,
            buyers=1,
            units=1,
        )
        for p in range(10)
        for i in range(31)
    ]
    older = [dict(r, business_date=previous_year(r["business_date"])) for r in current]

    class Repo:
        def validate(self, *_):
            pass

        def profiles(self):
            return {}

        def rows(self, a, b):
            return current if a.year == 2026 else older

    repo = Repo()
    repo.start, repo.end = start, end
    calls = 0

    def counted(day):
        nonlocal calls
        calls += 1
        return previous_year(day)

    monkeypatch.setattr(query, "previous_year", counted)
    query.overview(repo, query.BoardQuery(start, end))
    assert calls < 15000, "同比日期映射不得对每条去年记录重复扫描全部本期记录"


def test_risk_conversion_labels_name_order_numerator():
    end = date(2026, 10, 26)
    rows = [
        dict(
            business_date=end - timedelta(days=i),
            visitors=200,
            orders=20,
            product_revenue=Decimal("100"),
        )
        for i in range(8)
    ]
    for orders, label in ((10, "严重订单转化下滑"), (16, "中度订单转化下滑")):
        rows[0]["orders"] = orders
        value = risk(rows, end, False)
        assert value["label"] == label
        assert "订单" in value["basis"] and "访客" in value["basis"]
