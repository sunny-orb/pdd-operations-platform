"""已确认五分支边界，固定合成输入独立核对规则和单日估算。"""

from decimal import Decimal

import pytest

from app.modules.products.service import assess_risk


@pytest.mark.parametrize(
    "uv,orders,top,expected",
    [
        (100, 0, True, ("no_transaction", "P1", 100)),
        (200, 14, False, ("severe_order_conversion_drop", "P1", 90)),
        (100, 10, True, ("severe_traffic_drop", "P0", 95)),
        (100, 10, False, ("severe_traffic_drop", "P1", 85)),
        (200, 17, False, ("moderate_order_conversion_drop", "P2", 70)),
        (160, 16, False, ("moderate_traffic_drop", "P2", 65)),
        (161, 17, False, None),
    ],
)
def test_first_matching_online_rule_and_inclusive_boundaries(uv, orders, top, expected):
    prior = dict(visitors=200, orders=20, product_revenue=Decimal("100"))
    result = assess_risk([prior] * 7 + [dict(prior, visitors=uv, orders=orders)], top)
    assert (
        tuple(result[k] for k in ("rule_id", "priority", "risk_score")) if result else None
    ) == expected


def test_missing_amount_blocks_only_impact_not_observed_traffic_signal():
    prior = dict(visitors=200, orders=20, product_revenue=None)
    result = assess_risk([prior] * 7 + [dict(prior, visitors=100, orders=10)], False)
    assert result["rule_id"] == "severe_traffic_drop"
    assert result["estimated_impact_amount"] is None
    assert result["impact_status"] == "not_computable"


def test_review_negative_amount_cannot_be_cancelled_by_other_days_for_impact():
    prior = dict(visitors=200, orders=20, product_revenue=Decimal("100"))
    rows = [dict(prior, product_revenue=Decimal("-100"))] + [prior] * 6
    result = assess_risk(rows + [dict(prior, visitors=100, orders=10)], False)
    assert result["rule_id"] == "severe_traffic_drop"
    assert result["estimated_impact_amount"] is None
    assert result["impact_missing_reasons"]
