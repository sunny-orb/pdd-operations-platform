"""趋势与总览七卡同源同范围；诊断不借用同款健康度，不把空同比当稳定。"""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.product_board.query import BoardQuery, overview
from app.modules.product_board.source import previous_year
from tests.test_product_board_review import END, Repo
from tests.test_product_metric_pairs import fact

pytest_plugins = ["tests.sales_pg_support"]


def test_prior_curve_keeps_available_product_days_and_matches_cards():
    rows = [
        fact(END, "old", product_revenue=D(50)),
        fact(END, "new", product_revenue=D(200)),
        fact(previous_year(END), "old", product_revenue=D(100)),
        fact(previous_year(END), "prior-only", product_revenue=D(999)),
    ]
    result = overview(Repo(rows), BoardQuery(END, END))
    point = result["series"][0]
    assert point["values"]["revenue"] == 250
    assert point["previous"]["revenue"] == result["metrics"]["revenue"]["previous"] == 100
    assert result["health"]["metrics"]["revenue"]["yoy"] == D("-.5")
    assert result["diagnosis"]["growth_title"] == "交易增长"
    assert result["metrics"]["revenue"]["yoy"] == D("1.5")


def test_prior_values_require_same_product_on_each_current_day():
    rows = [
        fact(END, "a"),
        fact(END - timedelta(days=1), "b"),
        fact(previous_year(END), "b"),
        fact(previous_year(END - timedelta(days=1)), "a"),
    ]
    result = overview(Repo(rows), BoardQuery(END - timedelta(days=1), END))
    assert all(p["previous"]["revenue"] is None for p in result["series"])


def test_missing_days_zero_values_and_partial_fields_are_distinct():
    rows = [
        fact(END, "a"),
        fact(END, "b"),
        fact(previous_year(END), "a", product_revenue=D(0)),
        fact(previous_year(END), "b", product_revenue=None),
    ]
    result = overview(Repo(rows), BoardQuery(END - timedelta(days=1), END))
    assert result["series"][0]["values"]["revenue"] is None
    assert result["series"][0]["previous"]["revenue"] is None
    assert result["series"][1]["previous"]["revenue"] == 0
    assert "revenue" in result["series"][1]["previous_partial"]
    assert "visitors" not in result["series"][1]["previous_partial"]
    assert result["metrics"]["revenue"]["yoy"] is None
    assert result["diagnosis"]["growth_title"] == "交易表现"


def test_leap_day_has_no_fabricated_prior_point():
    end = date(2028, 2, 29)
    repo = Repo([fact(end), fact(date(2027, 2, 28))])
    repo.end = end
    result = overview(repo, BoardQuery(end, end))
    assert result["series"][0]["previous"]["revenue"] is None


@pytest.mark.parametrize(
    "current,old,expected",
    [
        (dict(visitors=200), {}, "主图"),
        (dict(product_revenue=D(50)), {}, "客单价"),
        (dict(visitors=50), {}, "流量入口"),
        ({}, {}, "高效商品"),
    ],
)
def test_diagnosis_reuses_online_priority_without_false_stability(current, old, expected):
    result = overview(
        Repo([fact(END, **current), fact(previous_year(END), **old)]), BoardQuery(END, END)
    )
    assert expected in result["diagnosis"]["action"]
    assert result["diagnosis"]["generation_method"] == "rules"


def test_no_comparison_is_not_zero_growth_or_a_scaling_recommendation():
    result = overview(Repo([fact(END)]), BoardQuery(END, END))
    diagnosis = result["diagnosis"]
    assert diagnosis["growth_title"] == "交易表现"
    assert "放大" not in diagnosis["action"] and "稳定" not in diagnosis["action"]


def test_postgres_curve_recovers_prior_record_without_changing_health(sales_pg, tmp_path):
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.source import normalize
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    end, data = candidate(duplicate_profile=False)
    rows = [
        dict(
            z_id=i,
            shop_id="663599807",
            spu_id=pid,
            dt=str(day),
            time_granularity="day",
            transaction_amount=amount,
            product_uv=100,
            transaction_users=10,
            transaction_orders=10,
            transaction_products=10,
            favorite_product_users=3,
        )
        for i, (pid, day, amount) in enumerate(
            [
                ("123", end, "50"),
                ("456", end, "200"),
                ("123", previous_year(end), "100"),
                ("789", previous_year(end), "999"),
            ],
            1,
        )
    ]
    data["product_daily"] = normalize("product_daily", rows, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        result = overview(repo, BoardQuery(end, end))
    assert result["series"][0]["previous"]["revenue"] == 100
    assert result["metrics"]["revenue"]["value"] == 250
    assert result["health"]["metrics"]["revenue"]["yoy"] == D("-.5")
