"""预测口径用合成文案和账本验算，不保存业务原文、台账或经营算例。"""

import json
from datetime import date
from decimal import ROUND_DOWN, localcontext
from decimal import Decimal as D

import pytest

FORMULA = "月末中心估计 = 累计 GMV + 剩余天数 ×（近 2 日销售日均 × 75% + 近 4 日销售日均 × 25%）"
SOP = f"""# 月度滚动预测测试

# 1. 范围
只累计月初至最近连续完整销售日。按自然月确定剩余天数。

# 2. 范围核对
目标缺失或冲突时，销售预测可以继续。按日期和店铺核对 GMV。

# 3. 销售与推广
展示近2天、近4天和月累计的起止日、GMV、日均GMV、花费、推广成交额、ROI、推广销售额占比和点击成交转化率。
近2天和近4天分别与之前等长区间比较日均变化。使用元和未舍入值计算。
实际ROI=推广成交额合计÷花费合计。推广销售额占比=推广成交额合计÷相同日期、相同店铺的GMV合计。
点击成交转化率=推广成交笔数合计÷点击量合计。分母为零时无法计算。
月初不足2天或4天时标明实际样本天数。当月没有完整销售日时标记待更新。

# 4. 月末预测
{FORMULA}。有足够完整样本时采用。
各情景按「累计GMV+剩余天数×该情景日均」计算上下界。基准取近2日与近4日日均之间。
保守情景向近期可比低位延伸，乐观情景向近期可持续高位延伸。
11月双十一后，以活动后最低连续2日日均作下限，以近4天最高单日作乐观上限。
换月或换活动阶段应重选可比窗口。情景区间未经概率校准。
目标达成率=累计GMV÷本月目标。剩余差额=目标−累计GMV。
尚未达标时，差额÷剩余天数即达标所需日均。已经达标就列超额金额，月末剩余天数为零时直接核算实绩。
预测达成率=中心估计÷目标。

# 5. 复核依据
核对店铺与活动。仅在证据能量化影响时调整金额。

# 6. 报告
汇报按「截止日和累计实绩 → 目标与进度 → 月末中心及区间 → 较上次变化 → 驱动、风险与待办」排列。

# 算例
展示另一种算法的虚构结果。月末中心估计=最近一天×999。
"""


def compiler():
    from app.modules.knowledge.forecast_rules import compile_forecast_rule

    return compile_forecast_rule


def calculate(rule, **kwargs):
    from app.modules.knowledge.forecast_rules import calculate_forecast

    return calculate_forecast(rule, **kwargs)


@pytest.fixture
def rule():
    return compiler()(SOP)


def sample(month=6):
    return {
        date(2032, month, day): D(amount)
        for day, amount in enumerate(["10", "10", "10", "10", "12", "12", "16.34", "16.34"], 1)
    }


def test_compiles_changed_weights_and_preserves_their_source():
    compile_rule = compiler()
    source = SOP.replace("75%", "55％").replace("25%", "45％")
    rule = json.loads(json.dumps(compile_rule(source)))
    assert rule["terms"] == [{"days": 2, "weight": "0.55"}, {"days": 4, "weight": "0.45"}]
    assert "55％" in rule["evidence"]["center"]["quote"]
    assert "月末预测" in rule["evidence"]["center"]["section"]
    assert rule["report_order"][0] == "截止日和累计实绩"
    assert rule["report_order"][-1] == "驱动、风险与待办"


def test_feishu_document_title_is_metadata_and_not_a_calculation_clause():
    renamed = "<title>【临时验收】合成规则副本 20320601 第 8 版</title>\n" + SOP
    assert compiler()(renamed) == compiler()(SOP)


def test_synonymous_reordered_headings_use_stable_roles_and_keep_original_citation():
    revised = SOP.replace(
        "截止日和累计实绩 → 目标与进度 → 月末中心及区间",
        "月底中心与区间 → 数据截止日及累计实绩 → 月度目标和进度",
    )
    rule = compiler()(revised)
    assert rule["report_order"] == [
        "月末中心及区间",
        "截止日和累计实绩",
        "目标与进度",
        "较上次变化",
        "驱动、风险与待办",
    ]
    assert "月底中心与区间" in rule["evidence"]["report"]["quote"]


def test_equivalent_formula_order_wrapping_and_wording():
    revised = SOP.replace(
        FORMULA,
        "**月底 GMV 中心估计** ＝ 月累计成交额 ＋ 剩余天数 ×（\n"
        " 25% × 最近4天的日均GMV ＋ 75% × 最近2天的日均GMV）",
    )
    assert compiler()(revised)["terms"] == [
        {"days": 2, "weight": "0.75"},
        {"days": 4, "weight": "0.25"},
    ]


def test_reformatted_decimal_weight_formula_and_linked_nonformula_context():
    revised = SOP.replace(
        FORMULA,
        "月底中心预测公式为：本月累计GMV 加上 剩余天数 乘以（"
        "最近2天的日均销售额 × 0.70 与 最近4天的日均销售额 × 0.30之和）",
    ).replace(
        "核对店铺与活动。", "核对店铺与活动。[经营资料](https://example.test/x?table=t&view=v)"
    )
    assert compiler()(revised)["terms"] == [
        {"days": 2, "weight": "0.7"},
        {"days": 4, "weight": "0.3"},
    ]


def test_typographic_quotes_and_formula_colon_keep_same_rule():
    revised = SOP.replace("中心估计 =", "中心估计：").replace("「", "“").replace("」", "”")
    assert compiler()(revised)["terms"] == compiler()(SOP)["terms"]
    assert compiler()(revised)["report_order"] == compiler()(SOP)["report_order"]


def test_zero_weight_uses_only_other_amount_without_bypassing_sample_requirement():
    revised = SOP.replace("75%", "100%").replace("25%", "0%")
    compiled = compiler()(revised)
    args = dict(month=date(2032, 6, 1), daily=sample(), target=None)
    full = calculate(compiled, **args, cutoff=date(2032, 6, 8))
    assert full["center_daily"] == D("16.34")
    assert calculate(compiled, **args, cutoff=date(2032, 6, 2))["center"] is None


@pytest.mark.parametrize(
    "heading",
    [
        "4. 已废止的推演",
        "4. 仅适用于一家店的推演",
        "4. 旧版留档",
        "4. 仅在周末预测",
        "4. 天猫旗舰店月末预测",
        "4. 会员渠道月末预测",
    ],
)
def test_scope_changes_in_regulating_heading_block_body_formula(heading):
    with pytest.raises(ValueError):
        compiler()(SOP.replace("4. 月末预测", heading))


def test_undecoded_operating_condition_cannot_be_ignored_in_another_section():
    with pytest.raises(ValueError):
        compiler()(SOP.replace("按日期和店铺核对 GMV。", "按日期和店铺核对 GMV。六日一律跳过。"))


@pytest.mark.parametrize("after", ["核对店铺与活动。", "有足够完整样本时采用。"])
def test_editorial_notes_need_no_rule_code_change(after):
    revised = SOP.replace(after, after + "供每周经营会复盘参考。修改说明收在附录。")
    assert compiler()(revised) == compiler()(SOP)


def test_plain_heading_numbering_and_typographic_maintenance_keep_numeric_parameters():
    revised = SOP.replace("4. 月末预测", "四、月末预测方法").replace(
        "核对店铺与活动。", "核对店铺与活动。本次统一了标点和排版。修订记录见文末。"
    )
    assert compiler()(revised)["terms"] == compiler()(SOP)["terms"]


@pytest.mark.parametrize(
    "addition",
    ["GMV只计算直营门店。", "库存的在途金额应计入中心估计。", "仅供历史演练。", "每单固定扣五元。"],
)
def test_advisory_chapter_cannot_redefine_scope_or_measure(addition):
    with pytest.raises(ValueError):
        compiler()(SOP.replace("核对店铺与活动。", "核对店铺与活动。" + addition))


@pytest.mark.parametrize(
    "addition",
    [
        "[以下公式已暂停](https://example.test/status)。",
        "核对月度日报后，缺报的自然日按零计算。",
        "所有星期三作罢。",
        "预测需改成中位数并记录原值。",
        "核对店铺时排除旗舰店。",
        "没有最新日报时，记录缺口并继续沿用前次日均。",
        "供经营会复盘参考，同时将外部门店剔出。",
        "修改说明收在附录，后续执行移动中位数。",
        "版本日期为2032年6月1日，改抄上次结论。",
        "核对店铺与活动，同时跳过周一。",
    ],
)
def test_link_labels_and_review_instructions_cannot_hide_a_rule_change(addition):
    with pytest.raises(ValueError):
        compiler()(SOP.replace("核对店铺与活动。", "核对店铺与活动。" + addition))


def test_separately_named_weighted_average_accepts_only_the_declared_definition():
    separate = SOP.replace(
        FORMULA,
        "月末中心估计 = 累计 GMV + 剩余天数 × 加权日均。"
        "加权日均 = 近 2 日销售日均 × 75% + 近 4 日销售日均 × 25%",
    )
    assert compiler()(separate)["terms"] == compiler()(SOP)["terms"]
    with pytest.raises(ValueError):
        compiler()(separate.replace("加权日均 =", "目标日均 ="))


def test_ambient_decimal_context_cannot_round_compiled_weights_or_change_forecast():
    source = SOP.replace("75%", "75.375%").replace("25%", "24.625%")
    args = dict(month=date(2032, 6, 1), cutoff=date(2032, 6, 8), daily=sample(), target=D("222"))
    high_precision = calculate(compiler()(source), **args)
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        assert calculate(compiler()(source), **args) == high_precision


@pytest.mark.parametrize(
    "old,new",
    [
        ("25%", "35%"),
        ("销售日均 × 75%", "推广日均 × 75%"),
        ("剩余天数 ×", "剩余天数 ÷"),
        ("近 2 日销售日均", "前月 2 日销售日均"),
        ("近 2 日销售日均", "近 2**2 日销售日均"),
        ("累计 GMV +", "目标 GMV +"),
        (FORMULA, FORMULA + " × 0.95"),
        (FORMULA, FORMULA + "*"),
        (FORMULA, "下面的公式仅用于回溯，禁止用于本月推演。\n" + FORMULA),
        ("# 2. 范围核对", "# 2. 范围核对\n暂停使用后文月末中心公式。"),
        (FORMULA, "仅对旗舰店适用。\n" + FORMULA),
        ("当月没有完整销售日时标记待更新", "当月没有完整销售日时以目标替代实绩"),
        ("预测达成率=中心估计÷目标", "预测达成率=中心估计÷目标。月末中心另加补贴预计销售额"),
        ("相同日期、相同店铺的GMV合计", "全月所有店铺的GMV合计"),
        ("最低连续2日日均", "最低两个非连续日期的日均"),
        ("各情景按", "原情景方案已停止执行。各情景按"),
        ("汇报按", "预测结果再扣除渠道手续费。汇报按"),
        ("# 4. 月末预测", "# 算例 月末预测"),
    ],
)
def test_rejects_unimplemented_or_contradictory_semantics(old, new):
    with pytest.raises(ValueError):
        compiler()(SOP.replace(old, new))


def test_partial_month_and_previous_periods_use_unrounded_calendar_amounts(rule):
    result = calculate(
        rule,
        month=date(2032, 6, 1),
        cutoff=date(2032, 6, 8),
        daily=sample(),
        target=D("448.68"),
    )
    assert result["status"] == "forecast"
    assert (result["actual"], result["remaining_days"]) == (D("96.68"), 22)
    assert (result["center_daily"], result["center"]) == (D("15.7975"), D("444.225"))
    assert result["needed_daily"] == D(16)
    assert result["scenarios"]["baseline"] == {
        "low_daily": D("14.17"),
        "high_daily": D("16.34"),
        "low": D("408.42"),
        "high": D("456.16"),
    }
    short = result["windows"][0]
    assert (short["sample_days"], short["previous_average"]) == (2, D(12))
    assert (short["previous_start"], short["previous_end"]) == (date(2032, 6, 5), date(2032, 6, 6))
    assert result["comparison"] is None
    assert result["scenarios"]["conservative"] is None
    assert result["scenarios"]["optimistic"] is None


def test_revised_formula_changes_same_frozen_inputs_and_keeps_separate_diagnostic_windows(rule):
    changed = SOP.replace(
        FORMULA,
        "月末中心估计=累计GMV+剩余天数×（近4日销售日均×60%+近8日销售日均×40%）",
    )
    args = dict(month=date(2032, 6, 1), cutoff=date(2032, 6, 8), daily=sample(), target=None)
    first = calculate(rule, **args)
    second = calculate(compiler()(changed), **args)
    assert (first["center"], second["center"]) == (D("444.225"), D("390.072"))
    assert first["windows"] == second["windows"]
    assert second == calculate(compiler()(changed), **args)


def test_missing_first_day_or_midmonth_gap_does_not_turn_later_rows_into_a_forecast(rule):
    data = sample()
    data[date(2032, 6, 3)] = None
    result = calculate(
        rule, month=date(2032, 6, 1), cutoff=date(2032, 6, 8), daily=data, target=None
    )
    assert result["status"] == "insufficient_sample"
    assert result["cutoff"] == date(2032, 6, 2)
    assert result["actual"] == D(20)
    assert result["center"] is result["windows"][0]["previous_average"] is None
    assert result["windows"][1]["sample_days"] == 2
    del data[date(2032, 6, 1)]
    none = calculate(
        rule, month=date(2032, 6, 1), cutoff=date(2032, 6, 8), daily=data, target=D(20)
    )
    assert none["status"] == "no_complete_day"
    assert none["cutoff"] is none["actual"] is none["center"] is none["gap"] is None
    assert none["remaining_days"] is None


def test_no_target_or_promotion_still_returns_sales_forecast_for_zero_sales(rule):
    data = dict.fromkeys(sample(), D(0))
    result = calculate(
        rule, month=date(2032, 6, 1), cutoff=date(2032, 6, 8), daily=data, target=None
    )
    assert result["actual"] == result["center"] == D(0)
    assert (
        result["needed_daily"]
        is result["achievement_rate"]
        is result["forecast_achievement_rate"]
        is None
    )
    assert result["windows"][0]["change_rate"] is None
    assert result["windows"][0]["promotion"]["roi"] is None


def test_finished_month_uses_final_actual_without_required_daily_or_scenarios(rule):
    month = date(2032, 2, 1)
    values = {month.replace(day=d): D(2) for d in range(1, 30)}
    result = calculate(rule, month=month, cutoff=month.replace(day=29), daily=values, target=D(50))
    assert result["status"] == "closed"
    assert (result["remaining_days"], result["actual"], result["center"]) == (0, D(58), D(58))
    assert (result["gap"], result["over_target"], result["needed_daily"]) == (D(-8), D(8), None)
    assert result["achievement_rate"] == result["forecast_achievement_rate"] == D("1.16")
    assert all(v is None for v in result["scenarios"].values())


def test_activity_bounds_require_confirmed_comparable_period_and_full_windows(rule):
    month = date(2032, 11, 1)
    data = dict.fromkeys([month.replace(day=n) for n in range(1, 12)], D(9999))
    for day, value in enumerate([2, 4, 6, 8, 12, 10], 12):
        data[month.replace(day=day)] = D(value)
    args = dict(month=month, cutoff=month.replace(day=17), daily=data, target=None)
    no_phase = calculate(rule, **args)
    assert no_phase["scenarios"]["conservative"] is no_phase["scenarios"]["optimistic"] is None
    pre_event = calculate(rule, **args, comparable_start=month.replace(day=1))["scenarios"]
    assert pre_event["conservative"] is pre_event["optimistic"] is None
    scenarios = calculate(rule, **args, comparable_start=month.replace(day=12))["scenarios"]
    assert scenarios["conservative"]["low_daily"] == D(3)
    assert (scenarios["optimistic"]["low_daily"], scenarios["optimistic"]["high_daily"]) == (
        D(11),
        D(12),
    )
    stage_too_short = calculate(rule, **args, comparable_start=month.replace(day=16))["scenarios"]
    assert stage_too_short["optimistic"] is None


def test_promotion_pairs_each_numerator_with_covered_denominator_and_lists_sample_days(rule):
    promo = {
        date(2032, 6, 6): dict(spend=None, revenue=D(12), clicks=None, orders=None),
        date(2032, 6, 7): dict(spend=D(4), revenue=D("8.17"), clicks=D(40), orders=D(8)),
        date(2032, 6, 8): dict(spend=D(1), revenue=D("8.17"), clicks=D(0), orders=D(2)),
    }
    result = calculate(
        rule,
        month=date(2032, 6, 1),
        cutoff=date(2032, 6, 8),
        daily=sample(),
        target=None,
        promotion=promo,
    )
    short = result["windows"][0]["promotion"]
    assert (short["roi"], short["sales_share"], short["click_conversion"]) == (
        D("3.268"),
        D(".5"),
        D(".25"),
    )
    total = result["windows"][-1]["promotion"]
    assert total["roi"] == D("3.268")
    assert total["metric_days"] == {"roi": 2, "sales_share": 3, "click_conversion": 2}
    assert total["sales_share"] > short["sales_share"]


def test_month_change_and_same_month_prior_require_actual_evidence(rule):
    args = dict(month=date(2032, 6, 1), cutoff=date(2032, 6, 8), daily=sample(), target=None)
    old = {"month": "2032-06-01", "cutoff": "2032-06-07", "center": "440", "center_daily": "15"}
    report = calculate(rule, **args, previous=old)
    assert report["comparison"]["center_change"] == D("4.225")
    assert report["comparison"]["latest_actual_minus_prior_daily"] == D("1.34")
    old["month"] = "2032-05-01"
    assert calculate(rule, **args, previous=old)["comparison"] is None
