"""用合成 SOP 验证模型所读的范围、权重与原文出处。"""

import json
from datetime import date
from decimal import Decimal

import pytest

from tests.test_knowledge_forecast_rules import FORMULA, SOP, calculate, sample

SYMBOLIC = "月末中心估计 = 累计 GMV + 剩余天数 ×（近3日销售日均 × 70% + 近7日销售日均 × 30%）"
CENTER = "月底中心估计先取本月累计成交额，再加上剩余天数乘以加权日均"
ALLOCATION = "加权日均按最近三天的销售日均占七成，最近七天的销售日均占三成"
SPOKEN = (
    "本月月底的成交额按已累计的 GMV 加上剩余天数的预估销售来算，"
    "余下每日销售按最近三天的日均销售额占七成、最近七天的日均销售额占三成"
)


def module():
    from app.modules.knowledge import forecast_review

    return forecast_review


def review(source, centers, weights, issues=()):
    # 模型协议引用给它看的本版序号。预期的原句、数值和相互对应关系由各用例独立给出。
    rules = module()
    clauses = json.loads(rules.review_request(source))["clauses"]
    ids = {item["quote"]: item["id"] for item in clauses}
    return rules.ForecastReview(
        center_clauses=[ids[text] for text in centers],
        weights=[{"clause": ids[text], "days": days, "weight": w} for text, days, w in weights],
        issues=list(issues),
    )


def test_model_request_excludes_example_money_nested_examples_and_source_urls():
    source = (
        SOP.replace(FORMULA, SYMBOLIC)
        .replace(
            "核对店铺与活动。",
            "核对店铺与活动。[经营资料](https://example.test/x?secret=not-for-model)。",
        )
        .replace("# 算例", "# 七、算例\n## 分店明细\n金额为 915366 元。\n## 预测公式")
    )
    request = module().review_request(source)
    assert all(word not in request for word in ("915366", "999", "not-for-model", "https://"))
    data = json.loads(request)
    assert SYMBOLIC in [clause["quote"] for clause in data["clauses"]]
    assert len({c["id"] for c in data["clauses"]}) == len(data["clauses"])


def test_inline_example_paragraph_and_bare_url_do_not_expose_operating_amounts_or_parameters():
    source = SOP.replace(
        "核对店铺与活动。",
        "核对店铺与活动。\n\n例如：累计 GMV 为 913681 元。剩余 6 天预估 12720 元。\n\n"
        "资料见 https://example.test/private?token=private97 。",
    )
    request = module().review_request(source)
    assert all(value not in request for value in ("913681", "12720", "private97"))


def test_markdown_link_with_parentheses_keeps_only_its_label_in_the_model_request():
    source = SOP.replace(
        "核对店铺与活动。",
        "核对店铺与活动。[经营资料](https://example.test/a(b(c))?token=private87)。",
    )
    request = module().review_request(source)
    assert "private87" not in request
    assert any(c["quote"] == "经营资料" for c in json.loads(request)["clauses"])


def test_model_reads_new_formal_heading_and_reports_conflicting_claim_before_title_diagnostics():
    rules = module()
    note = "另外，最近三天的权重是四成"
    source = SOP.replace(FORMULA, SYMBOLIC).replace(
        "# 算例", "## 月末金额的补充说明\n" + note + "。\n\n# 算例"
    )
    extracted = review(source, [SYMBOLIC], [(SYMBOLIC, 3, "0.7"), (SYMBOLIC, 7, "0.3")])
    with pytest.raises(rules.ForecastRuleError) as error:
        rules.validate_review(source, extracted)
    assert error.value.issues[0]["citations"] == [
        {"section": "4. 月末预测", "quote": SYMBOLIC},
        {"section": "月末金额的补充说明", "quote": note},
    ]


def test_feishu_bold_ending_after_the_sentence_mark_keeps_complete_visible_quotes():
    source = SOP.replace(FORMULA + "。", "**" + SYMBOLIC + "。**").replace(
        "目标缺失或冲突时", "**目标。**目标缺失或冲突时"
    )
    extracted = review(source, [SYMBOLIC], [(SYMBOLIC, 3, "0.7"), (SYMBOLIC, 7, "0.3")])
    rule = module().validate_review(source, extracted)
    assert rule["evidence"]["center"]["quote"] == SYMBOLIC


@pytest.mark.parametrize(
    ("source_line", "short", "long"),
    [
        (SPOKEN, "0.7", "0.3"),
        (SPOKEN.replace("七成", "六成").replace("三成", "四成"), "0.6", "0.4"),
        (
            "当月月末 GMV 取本月累计成交额加上余下自然天数的预计成交额计算，"
            "剩余每天的成交额取过去七天的日均成交额占30%、近三天的日均成交额占70%",
            "0.7",
            "0.3",
        ),
        (
            "余下每日销售按近三日占七成、近七日占三成，本月月底的成交额按"
            "剩余天数的预估销售加上已累计的 GMV 来算",
            "0.7",
            "0.3",
        ),
    ],
)
def test_month_total_and_remaining_daily_amount_in_plain_business_language(
    source_line, short, long
):
    source = SOP.replace(FORMULA, source_line)
    extracted = review(source, [source_line], [(source_line, 3, short), (source_line, 7, long)])
    rule = module().validate_review(source, extracted)
    assert rule["terms"] == [{"days": 3, "weight": short}, {"days": 7, "weight": long}]
    assert rule["evidence"]["center"]["quote"] == source_line


def test_paired_windows_must_explicitly_assign_each_spoken_ratio_in_order():
    allocation = "加权日均取最近七天及最近三天的日均销售额，权重分别为三成和七成"
    source = SOP.replace(FORMULA, CENTER + "。" + allocation)
    extracted = review(source, [CENTER], [(allocation, 7, "0.3"), (allocation, 3, "0.7")])
    assert module().validate_review(source, extracted)["terms"][0] == {"days": 3, "weight": "0.7"}
    wrong = review(source, [CENTER], [(allocation, 3, "0.3"), (allocation, 7, "0.7")])
    with pytest.raises(module().ForecastReviewMismatch):
        module().validate_review(source, wrong)


def test_period_projection_and_daily_weights_can_be_cited_as_two_complete_sentences():
    total, per_day = SPOKEN.split("，", 1)
    source = SOP.replace(FORMULA, total + "。\n## 加权日均\n" + per_day)
    terms = [(per_day, 3, "0.7"), (per_day, 7, "0.3")]
    rule = module().validate_review(source, review(source, [total], terms))
    assert rule["evidence"]["center"] == {"section": "4. 月末预测", "quote": total}
    assert rule["evidence"]["weight_definition"] == {"section": "加权日均", "quote": per_day}
    # 只说剩余期的总销售，没有说明该期如何使用每日值时，不借用其他地方的比例。
    loose = "近三天占七成，近七天占三成"
    source = source.replace(per_day, loose)
    with pytest.raises(module().ForecastRuleError) as caught:
        module().validate_review(
            source, review(source, [total], [(loose, 3, "0.7"), (loose, 7, "0.3")])
        )
    assert {"section": "4. 月末预测", "quote": total} in caught.value.issues[0]["citations"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("加上", "减去"),
        ("已累计的 GMV", "目标 GMV"),
        ("预估销售", "预计库存"),
        ("余下每日销售", "余下每周销售"),
        ("的日均销售额占七成", "的推广日均占七成"),
    ],
)
def test_ungrounded_month_total_unit_or_operator_cannot_be_normalized_by_model(old, new):
    invalid = SPOKEN.replace(old, new)
    source = SOP.replace(FORMULA, invalid)
    extracted = review(source, [invalid], [(invalid, 3, "0.7"), (invalid, 7, "0.3")])
    with pytest.raises(module().ForecastRuleError) as error:
        module().validate_review(source, extracted)
    assert {"section": "4. 月末预测", "quote": invalid} in error.value.issues[0]["citations"]


@pytest.mark.parametrize(
    ("allocation", "short", "long"),
    [
        (ALLOCATION, "0.7", "0.3"),
        ("近三日占六成、近七日占四成", "0.6", "0.4"),
        ("最近一周的权重为百分之二十五，最近三天的权重是百分之七十五", "0.75", "0.25"),
    ],
)
def test_review_uses_spoken_weights_and_both_verbatim_clauses(allocation, short, long):
    source = SOP.replace(FORMULA, CENTER + "。\n\n## 加权日均\n" + allocation)
    extracted = review(source, [CENTER], [(allocation, 3, short), (allocation, 7, long)])
    rule = module().validate_review(source, extracted)
    assert rule["terms"] == [{"days": 3, "weight": short}, {"days": 7, "weight": long}]
    assert rule["evidence"]["center"] == {"section": "4. 月末预测", "quote": CENTER}
    assert rule["evidence"]["weight_definition"] == {"section": "加权日均", "quote": allocation}
    args = dict(month=date(2032, 6, 1), daily=sample(), target=None)
    result = calculate(rule, cutoff=date(2032, 6, 8), **args)
    actual = Decimal("96.68")
    expected_daily = Decimal("44.68") / 3 * Decimal(short) + Decimal("86.68") / 7 * Decimal(long)
    assert abs(result["center"] - (actual + 22 * expected_daily)) < Decimal("1e-23")
    assert calculate(rule, cutoff=date(2032, 6, 3), **args)["center"] is None


def test_wrong_model_mapping_missing_occurrence_or_extraneous_issue_cannot_publish():
    rules = module()
    source = SOP.replace(FORMULA, SYMBOLIC)
    correct = review(source, [SYMBOLIC], [(SYMBOLIC, 3, "0.70"), (SYMBOLIC, 7, "0.30")])
    assert rules.validate_review(source, correct)["terms"][0] == {"days": 3, "weight": "0.7"}
    i = correct.center_clauses[0]
    for changes in [
        {
            "weights": [
                {"clause": i, "days": 7, "weight": "0.7"},
                {"clause": i, "days": 3, "weight": "0.3"},
            ]
        },
        {"center_clauses": []},
        {"center_clauses": [i + 1]},
        {"weights": []},
        {"weights": [item.model_dump() | {"clause": 999} for item in correct.weights]},
        {"issues": [{"kind": "unsupported", "clauses": [i]}]},
    ]:
        wrong = rules.ForecastReview.model_validate(correct.model_dump() | changes)
        with pytest.raises(rules.ForecastReviewMismatch):
            rules.validate_review(source, wrong)


@pytest.mark.parametrize(
    "addition",
    [
        "另外，最近三天的权重是四成",
        "另按补充说明，最近三天销售日均的预测权重应为四成",
    ],
)
def test_disagreeing_single_weight_reports_both_real_sections_even_if_model_omits_it(addition):
    rules = module()
    source = SOP.replace(FORMULA, SYMBOLIC).replace(
        "核对店铺与活动。", "核对店铺与活动。" + addition + "。"
    )
    extracted = review(source, [SYMBOLIC], [(SYMBOLIC, 3, "0.7"), (SYMBOLIC, 7, "0.3")])
    with pytest.raises(rules.ForecastRuleError) as error:
        rules.validate_review(source, extracted)
    assert not isinstance(error.value, rules.ForecastReviewMismatch)
    assert error.value.issues[0]["citations"] == [
        {"section": "4. 月末预测", "quote": SYMBOLIC},
        {"section": "5. 复核依据", "quote": addition},
    ]
    assert SYMBOLIC not in str(error.value) and addition not in str(error.value)


@pytest.mark.parametrize(
    "addition",
    [
        "另外，月底还需乘以可售库存系数",
        "仅在库存周转正常时才执行上述公式",
        "近三日占七成、近七日占三成，但缺销售记录时要沿用昨天的预测",
    ],
)
def test_verified_numbers_cannot_hide_inventory_scope_or_missing_data_exception(addition):
    rules = module()
    source = SOP.replace(FORMULA, SYMBOLIC + "。" + addition)
    extracted = review(source, [SYMBOLIC], [(SYMBOLIC, 3, "0.7"), (SYMBOLIC, 7, "0.3")])
    with pytest.raises(rules.ForecastRuleError) as error:
        rules.validate_review(source, extracted)
    assert {"section": "4. 月末预测", "quote": addition} in error.value.issues[0]["citations"]


@pytest.mark.parametrize(
    ("old", "new", "chapter"),
    [
        ("11月双十一后", "12月双十一后", "4. 月末预测"),
        ("近2天和近4天分别", "近2天和近32天分别", "3. 销售与推广"),
        ("较上次变化 → 驱动", "上周库存 → 驱动", "6. 报告"),
    ],
)
def test_rejected_activity_comparison_and_report_return_the_affected_clause(old, new, chapter):
    rules = module()
    source = SOP.replace(old, new)
    extracted = review(source, [FORMULA], [(FORMULA, 2, "0.75"), (FORMULA, 4, "0.25")])
    with pytest.raises(rules.ForecastRuleError) as error:
        rules.validate_review(source, extracted)
    assert any(
        ref["section"] == chapter and new in ref["quote"] and ref["quote"] in source
        for issue in error.value.issues
        for ref in issue["citations"]
    )


def test_prepublication_trial_detects_drift_in_the_calculator_before_returning_rule(monkeypatch):
    from app.modules.knowledge import forecast_calculation

    run = forecast_calculation.calculate_forecast

    def swapped_weights(rule, **inputs):
        a, b = rule["terms"]
        swapped = [dict(a, weight=b["weight"]), dict(b, weight=a["weight"])]
        return run(dict(rule, terms=swapped), **inputs)

    monkeypatch.setattr(forecast_calculation, "calculate_forecast", swapped_weights)
    rules = module()
    extracted = review(SOP, [FORMULA], [(FORMULA, 2, "0.75"), (FORMULA, 4, "0.25")])
    with pytest.raises(rules.ForecastReviewMismatch, match="试算"):
        rules.validate_review(SOP, extracted)


def test_verified_baseline_and_activity_are_separate_from_the_center_estimate():
    rules = module()
    source = SOP.replace(FORMULA, SPOKEN)
    ids = {c["quote"]: c["id"] for c in json.loads(rules.review_request(source))["clauses"]}
    center = ids[SPOKEN]
    baseline = ids["基准取近2日与近4日日均之间"]
    activity = ids["11月双十一后，以活动后最低连续2日日均作下限，以近4天最高单日作乐观上限"]
    scenario_flags = [
        {"kind": "conflict", "clauses": [center, baseline]},
        {"kind": "conflict", "clauses": [center, activity]},
        {"kind": "unsupported", "clauses": [activity]},
    ]
    extracted = review(source, [SPOKEN], [(SPOKEN, 3, "0.7"), (SPOKEN, 7, "0.3")], scenario_flags)
    rule = rules.validate_review(source, extracted)
    assert rule["terms"] == [{"days": 3, "weight": "0.7"}, {"days": 7, "weight": "0.3"}]
    assert rule["baseline_windows"] == [2, 4] and rule["activity"]["low_days"] == 2
    for issue in [
        {"kind": "conflict", "clauses": [center, 999]},
        {"kind": "conflict", "clauses": [center, center]},
        {"kind": "missing", "clauses": [baseline]},
    ]:
        wrong = rules.ForecastReview.model_validate(extracted.model_dump() | {"issues": [issue]})
        with pytest.raises(rules.ForecastReviewMismatch):
            rules.validate_review(source, wrong)


def test_related_equations_can_be_excluded_only_with_a_verified_primary_center():
    rules = module()
    source = SOP.replace(FORMULA, SPOKEN)
    bounds = "各情景按「累计GMV+剩余天数×该情景日均」计算上下界"
    achievement = "预测达成率=中心估计÷目标"
    terms = [(SPOKEN, 3, "0.7"), (SPOKEN, 7, "0.3")]
    model_read = review(source, [SPOKEN, bounds, achievement], terms)
    assert rules.validate_review(source, model_read)["terms"][1]["weight"] == "0.3"
    for centers in ([bounds, achievement], [SPOKEN, "分母为零时无法计算"]):
        with pytest.raises(rules.ForecastReviewMismatch):
            rules.validate_review(source, review(source, centers, terms))
