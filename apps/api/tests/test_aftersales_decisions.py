"""售后已核规则：分层排序、SOP门槛与建议，不生成执行事实。"""

from decimal import Decimal as D

import pytest

from app.modules.aftersales.classification import rule_parameters


def block(
    a="1500", s="9000", p="1000", qty="100", reason="尺码不合适", style="K1", category="童装"
):
    base = dict(
        _rpa=True,
        source_product_code=style + "01",
        source_style_code=style,
        source_product_category=category,
        source_product_name="款一",
    )
    return [
        [
            dict(
                base,
                actual_returned_qty=D(qty),
                actual_inbound_amount=D(a),
                source_return_reason=reason,
            )
        ],
        [dict(base, sales_amount=D(s))],
        [dict(base, sales_amount=D(p))],
    ]


def build(now, before=None, params=None):
    from app.modules.aftersales.decisions import decisions

    return decisions(
        now,
        before,
        params or rule_parameters(risk_available=True),
        rule_parameters(risk_available=True),
    )


@pytest.mark.parametrize(
    "a,status", [("2299.99", "建议核查"), ("2300", "关注 P2"), ("2500", "预警 P1")]
)
def test_exact_sop_rate_boundaries(a, status):
    # G=10000, prior total=20%, current=23% /25%; other rates also independent.
    result = build(block(a=a, s="10000", p="0"), block(a="2000", s="10000", p="0"))
    assert result["actions"][0]["status"] == status
    assert result["category_risks"][0]["risk_score"] == D(100)
    assert result["decision"]["title"] == "童装"


def test_missing_previous_and_low_sample_are_not_warnings():
    result = build(block(s="900", p="100", a="900"), block(s="900", p="100", a="10"))
    assert all(x["status"] not in ("预警 P1", "关注 P2") for x in result["actions"])
    assert "样本" in result["actions"][0]["note"]
    result = build(block())
    assert result["category_risks"][0]["change_pp"] is None
    assert result["actions"][0]["previous_rate"] is None
    assert result["actions"][0]["status"] == "建议核查"


def test_no_cross_level_score_comparison_and_no_truncated_actions():
    roles = [[], [], []]
    for i in range(12):
        for dest, src in zip(roles, block(style=f"K{i}"), strict=True):
            dest.extend(src)
    result = build(roles)
    assert result["actions"][0]["object"].startswith("品类")
    assert len(result["actions"]) == 13  # one category, all 12 styles, not old hard cap 8
    assert len({x["id"] for x in result["actions"]}) == 13
    assert len(result["priorities"]) <= 3


@pytest.mark.parametrize("include_p1_category", [False, True])
def test_main_risk_uses_grade_then_inbound_amount_across_levels(include_p1_category):
    current = block(a="1600", s="10000", p="0")
    previous = block(a="1000", s="10000", p="0")
    for dest, src in zip(current, block(a="2300", s="10000", p="0", style="K2"), strict=True):
        dest.extend(src)
    for dest, src in zip(previous, block(a="2000", s="10000", p="0", style="K2"), strict=True):
        dest.extend(src)
    if include_p1_category:
        other = dict(s="10000", p="0", style="K3", category="童鞋")
        for dest, src in zip(current, block(a="1200", **other), strict=True):
            dest.extend(src)
        for dest, src in zip(previous, block(a="600", **other), strict=True):
            dest.extend(src)

    result = build(current, previous)
    first_action = result["actions"][0]
    assert (first_action["object"], first_action["status"], first_action["amount"]) == (
        ("品类 童鞋", "预警 P1", D(1200))
        if include_p1_category
        else ("品类 童装", "关注 P2", D(3900))
    )
    assert result["decision"]["title"] == "K1 款一"
    assert result["decision"]["status"] == "预警 P1"
    assert result["decision"]["amount"] == D(1600)


def test_null_denominator_not_zero_rate_and_old_rules_not_reinterpreted():
    now = block()
    now[1] = None
    result = build(now)
    assert not result["category_risks"]
    assert all(x["risk_score"] is None for x in result["actions"])
    assert all(x["current_rate"] is None for x in result["actions"])
    assert build(block(), params=rule_parameters()) is None


def test_quality_multilabel_counted_once_in_action():
    result = build(block(qty="11", reason="质量问题，做工破损"))
    assert "11件" in result["category_actions"][0]["detail"]
    assert "33件" not in result["category_actions"][0]["detail"]
    assert "建议" in result["category_actions"][0]["detail"]
    assert "已整改" not in str(result)


def test_issue_share_warning_and_reason_does_not_claim_cause():
    now = block(qty="10")
    now[0].append(
        {
            **now[0][0],
            "source_return_reason": "不想要了",
            "actual_returned_qty": D(20),
            "actual_inbound_amount": D(0),
        }
    )
    old = block(qty="1")
    old[0].append(
        {
            **old[0][0],
            "source_return_reason": "不想要了",
            "actual_returned_qty": D(29),
            "actual_inbound_amount": D(0),
        }
    )
    result = build(now, old)
    assert result["actions"][0]["status"] == "预警 P1"
    assert "尺码" in result["actions"][0]["note"]
    assert "不直接认定" in result["actions"][0]["hypothesis"]


def test_frozen_templates_and_negative_quality_reason():
    params = rule_parameters(risk_available=True)
    config = next(
        b["thresholds"] for b in params["branches"] if b["code"] == "separate_risk_ranking"
    )
    config["templates"] = {k: list(v) for k, v in config["templates"].items()}
    config["templates"]["size"][1] = "报告当时尺码建议"
    result = build(block(), params=params)
    assert "报告当时尺码建议" in result["priorities"][0]["detail"]
    negative = build(block(reason="不是质量问题"))
    assert not negative["category_actions"]
    assert "定向质检" not in negative["actions"][0]["action"]


@pytest.mark.parametrize("amount,status", [("1360", "关注 P2"), ("1600", "预警 P1")])
def test_recurring_decimal_does_not_downgrade_exact_threshold(amount, status):
    result = build(
        block(a=amount, s="12000", p="0", reason="不想要了"),
        block(a="1000", s="12000", p="0", reason="不想要了"),
    )
    assert result["actions"][0]["status"] == status


def test_exact_issue_change_with_recurring_decimal():
    from app.modules.aftersales.decisions import meets_change

    assert meets_change(D(190), D(3000), D(100), D(3000), "0.03")
    assert meets_change(D(250), D(3000), D(100), D(3000), "0.05")
