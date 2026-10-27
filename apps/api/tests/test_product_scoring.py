"""十项评分算术与建议门控；输入均为合成参数，不代表业务模型验收。"""

from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.modules.products.scoring import METRICS, score_product


def case():
    inputs = {k: D("25") for k in METRICS}
    inputs["revenue_multiple_7d"] = D("2.5")
    standard = {
        "status": "validated",
        "metrics": {
            k: dict(
                thresholds=dict(
                    zip(
                        ("basic", "good", "excellent", "outstanding"),
                        ("10", "20", "30", "40")
                        if k != "revenue_multiple_7d"
                        else ("1.1", "1.5", "2", "3"),
                        strict=True,
                    )
                ),
                unit=m[2],
            )
            for k, m in METRICS.items()
        },
    }
    return inputs, standard


def test_weights_exact_decimal_boundaries_equal_lines_and_no_renormalization():
    values, standard = case()
    standard["metrics"]["product_revenue_7d"]["thresholds"] = {
        "basic": "10.123456789",
        "good": "10.123456789",
        "excellent": "10.123456789",
        "outstanding": "10.123456790",
    }
    values["product_revenue_7d"] = D("10.123456789")
    result = score_product(values, standard, blocked={"favorites_7d": ["definition_pending"]})
    assert sum(m[1] for m in METRICS.values()) == 100
    sig = {s["metric_key"]: s for s in result["signals"]}
    assert sig["product_revenue_7d"]["tier"] == "excellent"
    assert sig["product_revenue_7d"]["points"] == D("13.5")
    assert result["pending_weight"] == 10
    assert result["complete_score"] is None
    assert result["decision"]["code"] == "pending_validation"
    assert result["verified_score"] == sum(
        s["points"] for s in sig.values() if s["points"] is not None
    )


@pytest.mark.parametrize(
    "value,tier,points",
    [
        ("0", "below_basic", "0"),
        ("9.999999", "below_basic", "0"),
        ("10", "basic", "3"),
        ("20", "good", "6"),
        ("40", "outstanding", "12"),
        ("1000", "outstanding", "12"),
    ],
)
def test_raw_thresholds_not_rounded_and_zero_is_a_valid_value(value, tier, points):
    values, standard = case()
    values["visitors_7d"] = D(value)
    sig = score_product(values, standard)["signals"][0]
    assert (sig["tier"], sig["points"]) == (tier, D(points))


@pytest.mark.parametrize("bad", [None, "NaN", "Infinity", -1, 0.1])
def test_missing_nonfinite_negative_and_float_inputs_do_not_become_zero(bad):
    values, standard = case()
    values["visitors_7d"] = bad
    result = score_product(values, standard)
    assert result["signals"][0]["points"] is None
    assert result["pending_weight"] == 12


def test_missing_invalid_or_wrong_unit_threshold_only_blocks_that_metric():
    values, standard = case()
    standard["metrics"]["visitors_7d"]["thresholds"]["good"] = "9"
    standard["metrics"]["units_7d"]["unit"] = "yuan"
    del standard["metrics"]["ad_roi_7d"]
    result = score_product(values, standard)
    assert result["pending_weight"] == 32
    assert result["active_signal_count"] == 7


def test_growth_multiple_at_most_one_valid_but_zero_points():
    values, standard = case()
    standard["metrics"]["revenue_multiple_7d"]["thresholds"] = dict.fromkeys(
        ("basic", "good", "excellent", "outstanding"), "0"
    )
    values["revenue_multiple_7d"] = D("1")
    result = score_product(values, standard)
    assert result["signals"][-1]["points"] == 0
    assert result["signals"][-1]["status"] == "valid"


def test_all_unscored_is_null_not_zero_and_trial_never_recommends_scaling():
    values, standard = case()
    result = score_product({k: None for k in values}, standard)
    assert result["score_status"] == "unscored" and result["verified_score"] is None
    assert result["decision"]["code"] == "unscored"
    values = {k: D("100") for k in values}
    standard["status"] = "fixed_display_trial"
    result = score_product(values, standard, evidence_reasons=["source_completeness_unknown"])
    assert result["complete_score"] == 100
    assert result["decision"]["code"] == "pending_validation"


@pytest.mark.parametrize(
    "score,code",
    [("49.999", "optimize"), ("50", "hold"), ("74.999", "hold"), ("75", "small_scale")],
)
def test_decision_thresholds_use_exact_score(score, code):
    from app.modules.products.scoring import decide

    signals = [{"metric_key": k, "tier": "good", "points": D("1")} for k in METRICS]
    values = {"revenue_multiple_7d": D("2")}
    assert decide(D(score), 0, signals, values, [])["code"] == code


def test_high_score_requires_each_key_signal_and_all_shortfalls_returned():
    from app.modules.products.scoring import decide

    values, _ = case()
    signals = [{"metric_key": k, "tier": "basic", "points": D("1")} for k in METRICS]
    values["revenue_multiple_7d"] = D("1")
    result = decide(D("90"), 0, signals, values, [])
    assert result["code"] == "fix_key_metrics" and len(result["reasons"]) == 3


def test_arguments_not_mutated():
    values, standard = case()
    copy = deepcopy(standard)
    score_product(values, standard)
    assert standard == copy
