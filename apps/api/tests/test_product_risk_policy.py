"""香草流量门槛：流量下滑双侧≥100，其他预警当日>100；原命中顺序保留。"""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.product_board.metrics import risk

END = date(2026, 11, 1)


def observations(visitors=200, orders=20, prior_visitors=200, prior_orders=140, amount=100):
    per_day, remainder = divmod(prior_orders, 7)
    return [
        dict(
            business_date=END - timedelta(days=i),
            visitors=visitors if i == 0 else prior_visitors,
            orders=orders if i == 0 else per_day + (i <= remainder),
            product_revenue=D(amount),
        )
        for i in range(8)
    ]


@pytest.mark.parametrize(
    "visitors,orders,prior_visitors,prior_orders,top,expected",
    [
        (100, 0, 100, 70, True, None),
        (99, 0, 100, 70, False, None),
        (101, 0, 50, 70, False, ("有流量无成交", "P1", 100)),
        (100, 1, 100, 70, False, None),
        (101, 10, 50, 70, False, ("严重订单转化下滑", "P1", 90)),
        (100, 8, 100, 70, False, None),
        (101, 16, 50, 70, False, ("中度订单转化下滑", "P2", 70)),
        (200, 14, 200, 140, False, ("严重订单转化下滑", "P1", 90)),
        (200, 15, 200, 140, False, ("中度订单转化下滑", "P2", 70)),
        (200, 17, 200, 140, False, ("中度订单转化下滑", "P2", 70)),
        (200, 18, 200, 140, False, None),
        (60, 6, 100, 70, False, None),
        (60, 6, 100, 70, True, None),
        (99, 10, 200, 70, True, None),
        (100, 10, 200, 70, False, ("严重流量下滑", "P1", 85)),
        (101, 10, 200, 70, True, ("爆品严重流量下滑", "P0", 95)),
        (100, 10, 200, 9, True, ("严重流量下滑", "P1", 85)),
        (100, 10, 200, 10, True, ("爆品严重流量下滑", "P0", 95)),
        (30, 3, 50, 70, False, None),
        (80, 8, 100, 70, False, None),
        (100, 10, 125, 70, False, ("中度流量下滑", "P2", 65)),
        (101, 10, 125, 70, False, None),
        (81, 8, 100, 70, False, None),
        (0, 0, 29, 70, True, None),
        (0, 0, 500, 70, True, None),
    ],
)
def test_confirmed_traffic_thresholds(
    visitors, orders, prior_visitors, prior_orders, top, expected
):
    result = risk(observations(visitors, orders, prior_visitors, prior_orders), END, top)
    actual = (result["label"], result["priority"], result["score"]) if result else None
    assert actual == expected


def test_one_day_triggers_without_minimum_impact_and_first_match_wins():
    # 前七日平稳，当天同时达到P0流量与P1无成交条件；按线上先返回无成交。
    result = risk(observations(101, 0, 300, 70, amount=0), END, True)
    assert result["label"] == "有流量无成交" and result["priority"] == "P1"
    assert result["expected_loss"] == 0
    assert result["trend"] == [D(10)] * 6 + [D(0)]
    # 无成交未命中时，严重转化仍先于P0流量，而非自动挑最高优先级。
    result = risk(observations(101, 1, 300, 70), END, True)
    assert result["label"] == "严重订单转化下滑" and result["priority"] == "P1"


@pytest.mark.parametrize("field", ["visitors", "orders"])
def test_missing_required_field_is_not_zero(field):
    rows = observations(101, 0)
    rows[3][field] = None
    assert risk(rows, END, True) is None


def test_missing_date_invalid_money_and_real_zero_are_distinct():
    rows = observations(0, 0)
    assert risk(rows, END, True) is None
    rows = observations(100, 10)
    assert risk(rows[:-1], END, True) is None
    result = risk(rows, END, True)
    assert result["priority"] == "P0" and result["trend"][-1] == 100
    rows[1]["product_revenue"] = None
    result = risk(rows, END, True)
    assert result["priority"] == "P0" and result["expected_loss"] is None


def test_zero_order_baseline_does_not_fabricate_minus_one_hundred_percent():
    result = risk(observations(101, 0, prior_visitors=50, prior_orders=0), END, False)
    assert result["label"] == "有流量无成交"
    assert result["baseline"] == 0 and result["change"] is None
