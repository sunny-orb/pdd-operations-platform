"""SOP按规则判断：正常无记录、字段缺失与未评级不得混为一谈。"""

from datetime import date
from decimal import Decimal

import pytest

from app.modules.promotion.diagnosis import assess


def row(day, *, spend="400", revenue="800", net="600", deals="10"):
    return dict(
        date=date(2026, 10, day),
        spend=Decimal(spend) if spend else None,
        attributed_revenue=Decimal(revenue) if revenue else None,
        net_attributed_revenue=Decimal(net) if net else None,
        attributed_deals=Decimal(deals) if deals else None,
        impressions=Decimal(1000),
        clicks=Decimal(100),
    )


def rules(result):
    return {r["key"]: r for r in result["rule_results"]}


def test_normal_days_without_rows_still_evaluate_existing_records():
    result = assess([row(26, revenue="600")], [row(19)])
    assert result["priority"] == "P1"
    assert rules(result)["roi_drop"]["state"] == "triggered"
    assert result["current"]["roi"]["change"] == Decimal("-.25")
    assert result["evaluation_state"] == "rated"


def test_missing_net_only_limits_net_rule():
    result = assess([row(26, revenue="600", net=None)], [row(19)])
    assert rules(result)["roi_drop"]["state"] == "triggered"
    assert rules(result)["net_roi_drop"]["state"] == "unavailable"
    assert result["evaluation_state"] == "partial"
    assert result["priority"] == "P1"


def test_partial_spend_uses_known_totals_for_roi_and_zero_deal_reminders():
    result = assess(
        [row(26, revenue="400", deals="0"), row(27, spend=None, revenue="100", deals="0")],
        [row(19)],
    )
    assert result["current"]["roi"]["value"] == Decimal("1.25")
    assert result["current"]["revenue"]["value"] == 500
    assert result["current"]["roi"]["comparison_status"] == "reference"
    assert rules(result)["roi_drop"]["state"] == "triggered"
    assert rules(result)["zero_deals"]["state"] == "triggered"
    assert result["evaluation_state"] == "rated"
    assert result["priority"] == "P1"


@pytest.mark.parametrize(
    "field,metric,expected",
    [
        ("revenue", "roi", "1"),
        ("net", "net_roi", ".75"),
    ],
)
def test_partial_revenue_keeps_online_rating_from_known_totals(field, metric, expected):
    result = assess([row(26), row(27, **{field: None})], [row(19)])
    assert result["current"][metric]["value"] == Decimal(expected)
    assert rules(result)[metric + "_drop"]["state"] == "triggered"
    assert result["priority"] == "P1"


@pytest.mark.parametrize("partial_period", ["current", "previous"])
def test_partial_spend_does_not_hide_spend_growth_reminder(partial_period):
    current = [row(26, spend="600", revenue="900")]
    previous = [row(19, spend="400", revenue="800")]
    # 本期花费+50%、成交+12.5%；只检验增长组合，不需额外最低样本。
    (current if partial_period == "current" else previous).append(
        row(27 if partial_period == "current" else 20, spend=None, revenue="0")
    )
    result = assess(current, previous)
    assert rules(result)["spend_growth"]["state"] == "triggered"
    assert result["priority"] == "P1"  # 投产2→1.5，恰好下降25%。


@pytest.mark.parametrize("spend", [None, "0"])
def test_all_missing_or_zero_cost_does_not_invent_roi_or_growth(spend):
    result = assess([row(26, spend=spend)], [row(19, spend=spend)])
    assert result["current"]["roi"]["value"] is None
    assert result["findings"] == []
    assert rules(result)["spend_growth"]["state"] == "unavailable"


def test_all_missing_deals_is_not_zero_deals_observation():
    result = assess([row(26, deals=None)], [row(19)])
    assert rules(result)["zero_deals"]["state"] == "unavailable"
    assert result["findings"] == []


@pytest.mark.parametrize("current,previous", [([], []), ([], [row(19)])])
def test_no_current_records_is_not_zero_anomalies(current, previous):
    result = assess(current, previous)
    assert result["evaluation_state"] == "unrated"
    assert result["priority"] == "未评级"
    assert result["current"]["roi"]["value"] is None
    assert all(r["state"] == "unavailable" for r in result["rule_results"])


def test_no_prior_or_short_history_does_not_stop_current_zero_deals_observation():
    for previous in ([], [row(19)]):
        result = assess(
            [row(26, revenue="0", net="0", deals="0")], previous, previous_available=False
        )
        assert rules(result)["roi_drop"]["state"] == "unavailable"
        assert rules(result)["zero_deals"]["state"] == "triggered"
        assert result["priority"] == "观察"
        assert result["current"]["roi"]["change"] is None


@pytest.mark.parametrize(
    "level,focus",
    [("A级/B级混合", True), ("B级/S级混合", True), ("B级", False), (None, False), ("新品", True)],
)
def test_focus_includes_members_of_multiple_levels(level, focus):
    from app.modules.promotion.diagnosis import is_focused

    assert is_focused(level) is focus


@pytest.mark.parametrize("revenue,expected", [("680", "P2"), ("600", "P1"), ("680.01", "未触发")])
def test_roi_threshold_uses_unrounded_change(revenue, expected):
    assert assess([row(26, revenue=revenue)], [row(19)])["priority"] == expected


@pytest.mark.parametrize(
    "revenue,expected", [("26.35", "P2"), ("23.25", "P1"), ("26.35000001", "未触发")]
)
def test_repeating_decimal_roi_does_not_cross_exact_threshold(revenue, expected):
    assert (
        assess([row(26, spend="300", revenue=revenue)], [row(19, spend="300", revenue="31")])[
            "priority"
        ]
        == expected
    )


def test_list_search_matches_business_priority_label():
    from app.modules.promotion.diagnosis import object_matches

    item = assess([row(26, revenue="600")], [row(19)])
    assert object_matches(item, "预警")


def test_p3_search_only_matches_triggered_observation_without_changing_rating():
    from app.modules.promotion.diagnosis import object_matches

    observation = assess([row(26, spend="400", revenue="0", net="0", deals="0")], [])
    clear = assess([row(26)], [row(19)])
    warning = assess([row(26, revenue="600")], [row(19)])
    assert observation["priority"] == "观察"
    assert object_matches(observation, "P3 观察")
    assert object_matches(observation, "p3")
    assert not object_matches(clear, "P3")
    assert not object_matches(warning, "P3")
    assert object_matches(warning, "P1 预警")
