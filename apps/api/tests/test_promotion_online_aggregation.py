"""商品推广独立汇总与原始空值边界；店铺计算保持原有规则。"""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.promotion.diagnosis import assess
from app.modules.promotion.metrics import store_metrics
from app.modules.promotion.query import all_metrics


def row(spend, revenue, clicks="2", net="5"):
    return dict(
        date=date(2026, 10, 27),
        spend=None if spend is None else Decimal(spend),
        attributed_revenue=None if revenue is None else Decimal(revenue),
        net_attributed_revenue=Decimal(net),
        clicks=Decimal(clicks),
        impressions=Decimal("100"),
        attributed_deals=Decimal("1"),
    )


def product(rows):
    return all_metrics(SimpleNamespace(mode="product"), None, rows)


def test_all_known_amounts_participate_without_rewriting_missing_or_store_rules():
    rows = [row("10", "40"), row(None, "100", "3"), row("0", "5"), row("2", None)]
    original = deepcopy(rows)
    metrics = product(rows)
    assert metrics["roi"]["numerator"] == Decimal("145")
    assert metrics["roi"]["denominator"] == Decimal("12")
    assert metrics["roi"]["value"] == pytest.approx(Decimal("145") / 12, abs=Decimal("1e-25"))
    assert metrics["net_roi"]["value"] == pytest.approx(Decimal("20") / 12, abs=Decimal("1e-25"))
    assert metrics["cpc"]["value"] == pytest.approx(Decimal("12") / 9, abs=Decimal("1e-25"))
    for key in ("roi", "net_roi", "cpc"):
        assert metrics[key]["status"] == "partial"
    assert rows == original
    assert store_metrics(rows)["roi"]["value"] is None
    diagnosis = assess(rows, [row("400", "800")])
    for key in ("roi", "net_roi", "cpc"):
        assert diagnosis["current"][key]["value"] == metrics[key]["value"]
    assert (
        next(r for r in diagnosis["rule_results"] if r["key"] == "roi_drop")["state"]
        == "not_applicable"  # 已知花费12元，未到300元；不是缺项阻断。
    )


@pytest.mark.parametrize(
    "rows,roi,cpc",
    [
        ([], None, None),
        ([row(None, "5")], None, None),
        ([row("0", "5")], None, Decimal("0")),
        ([row("10", None)], None, Decimal("5")),
        ([row("10", "0")], Decimal("0"), Decimal("5")),
        ([row("10", None), row(None, "40")], Decimal("4"), Decimal("2.5")),
    ],
)
def test_missing_and_zero_are_distinct(rows, roi, cpc):
    result = product(rows)
    assert result["roi"]["value"] == roi
    assert result["cpc"]["value"] == cpc


pytest_plugins = ["tests.sales_pg_support"]


def test_published_rows_match_across_cards_daily_top_sop_and_last_year(sales_pg, tmp_path):
    from datetime import date

    from tests.test_promotion_api import client_for, publish_products
    from tests.test_promotion_query_performance import promotion_row

    rows = []
    for day, cost, revenue in [
        ("2026-10-27", "400", "800"),
        ("2026-10-20", "400", "1000"),
        ("2025-10-27", "200", "600"),
    ]:
        base = dict(
            promotion_row(date.fromisoformat(day), "900", spend=cost),
            transaction_amount=revenue,
            net_transaction_amount="100",
        )
        rows.extend(
            [
                base,
                dict(
                    base,
                    id=base["id"] + "-older",
                    total_cost=None,
                    transaction_amount="200",
                    net_transaction_amount="50",
                ),
            ]
        )
    publish_products(sales_pg, tmp_path, rows)
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta", params={"mode": "product"}).json()
        q = dict(
            mode="product",
            date_from="2026-10-27",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        data = client.get("/api/v1/promotion/overview", params=q).json()["data"]
        assert data["current_record_count"] == "2"  # 不按商品日/旧更新时间误删第二行
        assert Decimal(data["metrics"]["roi"]["value"]) == Decimal("2.5")
        point = data["series"][0]
        assert point["metrics"]["roi"]["value"] == data["metrics"]["roi"]["value"]
        assert Decimal(point["previous"]["roi"]["value"]) == 4
        assert point["previous"]["roi"]["status"] == "partial"
        assert Decimal(data["top5"][0]["roi"]) == Decimal("2.5")
        days = client.get("/api/v1/promotion/days", params=q).json()["data"]
        assert Decimal(days["items"][0]["roi"]) == Decimal("2.5")
        diagnosis = client.get("/api/v1/promotion/diagnosis", params=q).json()["data"]
        assert Decimal(diagnosis["overall"]["current"]["roi"]["value"]) == Decimal("2.5")
        assert Decimal(diagnosis["overall"]["previous"]["roi"]["value"]) == 3
        empty = client.get("/api/v1/promotion/overview", params=q | {"keyword": "不存在"}).json()[
            "data"
        ]
        assert empty["metrics"]["roi"]["value"] is None
        assert empty["top5"] == []


def test_complementary_missing_fields_in_ctr_cvr_are_reference():
    rows = [
        dict(row("10", "40"), impressions=None, attributed_deals=None),
        dict(row(None, "20"), clicks=None),
    ]
    result = product(rows)
    assert result["ctr"]["value"] == Decimal(".02")
    assert result["cvr"]["value"] == Decimal(".5")
    assert result["ctr"]["status"] == result["cvr"]["status"] == "partial"


def test_missing_cost_does_not_hide_ratios_or_rewrite_original_cost():
    result = product([row("10", "40"), row(None, "0")])
    assert result["roi"]["value"] == 4
    assert result["net_roi"]["value"] == 1
    assert result["cpc"]["value"] == Decimal("2.5")
    assert result["revenue"]["value"] == 40


@pytest.mark.parametrize(
    "field,blocked",
    [
        ("net_attributed_revenue", {"net_roi"}),
        ("attributed_revenue", {"roi"}),
        ("clicks", {"cpc"}),
    ],
)
def test_each_cost_ratio_sums_its_known_fields_independently(field, blocked):
    result = product([row("10", "40"), dict(row("2", "4"), **{field: None})])
    for key in ("roi", "net_roi", "cpc"):
        assert result[key]["value"] is not None
        assert (result[key]["status"] == "partial") == (key in blocked)


def test_sparse_dates_and_real_zero_cost_keep_attributed_revenue():
    rows = [row("10", "40"), dict(row("0", "5"), date=date(2026, 10, 29))]
    result = product(rows)
    assert result["roi"]["value"] == Decimal("4.5")
    assert result["roi"]["status"] == "observed"
    assert product([row("0", "5"), row(None, "5")])["roi"]["status"] == "zero_denominator"


def test_known_positive_deals_rule_out_zero_deals_despite_unallocated_cost():
    result = assess([row("400", "800"), row(None, "20")], [row("400", "800")])
    rules = {r["key"]: r for r in result["rule_results"]}
    assert rules["zero_deals"]["state"] == "clear"
    assert rules["roi_drop"]["state"] == "clear"
    assert result["evaluation_state"] == "rated"
    missing = [dict(row(None, "0"), attributed_deals=Decimal("0"))]
    assert (
        next(r for r in assess(missing, [])["rule_results"] if r["key"] == "zero_deals")["state"]
        == "unavailable"
    )
