"""预测在五页共用业务入口，固定模板、原文引用和范围都是交付契约。"""

import asyncio
from datetime import date

import pytest

from app.modules.assistant import catalog, dashboard
from app.modules.assistant.business import report
from app.modules.assistant.chat import ChatScope, QueryRun, answer_question
from app.modules.assistant.forecast_answers import forecast_intent, forecast_period

TODAY = date(2026, 11, 27)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("预测一下11月的GMV", "forecast"),
        ("按SOP估一下本月成交额", "forecast"),
        ("照现在的情况，这个月能卖多少？", "forecast"),
        ("用2026年11月25日的日报估计六家店11月月底GMV。", "forecast"),
        ("月底六店的GMV大概会到什么水平？", "forecast"),
        ("到这个月收官，六家店的成交额大致有多少？", "forecast"),
        ("上个月收官时实际GMV是多少？", None),
        ("月底各店当前累计成交额是多少？", None),
        ("本月的 GMV 预测怎么算？", "knowledge"),
        ("11月的GMV目标还差多少？", None),
        ("预测明天的天气", None),
        ("预测11月GMV，顺便看退款最多的商品", "mixed"),
    ],
)
def test_recognizes_monthly_forecast_without_intercepting_other_queries(question, expected):
    assert forecast_intent(question) == expected


@pytest.mark.parametrize(
    ("question", "selected", "expected"),
    [
        ("预测一下11月的GMV", "2025-12", date(2026, 11, 1)),
        ("估一下这个月GMV", "2025-12", date(2026, 11, 1)),
        ("预测选中月的GMV", "2026-10", date(2026, 10, 1)),
        ("2025年11月月底能卖多少", None, date(2025, 11, 1)),
        ("预测本月GMV", None, date(2026, 11, 1)),
        ("预测下月GMV", None, date(2026, 12, 1)),
    ],
)
def test_month_is_resolved_by_user_expression(question, selected, expected):
    assert forecast_period(question, selected, TODAY)[0] == expected


def test_estimate_with_dated_source_preserves_the_historical_cutoff():
    from app.modules.assistant.knowledge_answers import only_observed_facts

    question = "用2026年11月25日的日报估计六家店11月月底GMV。"
    assert forecast_period(question, None, TODAY) == (date(2026, 11, 1), date(2026, 11, 25))
    assert not only_observed_facts(question)


@pytest.mark.parametrize(
    "question", ["预测2026年第4季度GMV", "预测10月和11月的GMV", "预测未来7天的GMV"]
)
def test_unsupported_periods_do_not_silently_use_current_month(question):
    from app.modules.assistant.chat import Clarify

    with pytest.raises(Clarify):
        forecast_period(question, None, TODAY)


@pytest.mark.parametrize("domain", ["all", "products", "sales", "promotion_store", "aftersales"])
def test_standalone_question_uses_same_report_on_every_page(monkeypatch, domain):
    calls = []

    def read(engine, capability, params):
        calls.append((capability, params))
        data = report(
            "sales",
            capability,
            "月末GMV预测",
            "六店销售日报",
            "v1",
            scope={"month": "2026-11"},
            versions={"forecast_sop": "4"},
        )
        return dict(data, answer="六店月末中心估计 125.62 万元。", forecast_status="forecast")

    monkeypatch.setattr(catalog, "read", read)
    q = "预测一下2026年11月的GMV"
    run = QueryRun(None, ChatScope(domain=domain, versions={"forecast_sop": "3"}), q)
    result = asyncio.run(answer_question(None, q, "运营看板中心", [], run))
    assert result["answer"] == "六店月末中心估计 125.62 万元。"
    assert result["usage"]["requests"] == 0 and len(result["reports"]) == len(calls) == 1
    assert calls[0][1]["month"] == "2026-11"
    assert result["query_context"]["versions"]["forecast_sop"] == "4"


def test_single_shop_forecast_cannot_be_presented_as_channel():
    run = QueryRun(None, ChatScope(domain="sales", shop_id="one"), "预测一下这家店本月的GMV")
    result = asyncio.run(answer_question(None, run.question, "店铺销售", [], run))
    assert result["status"] == "no_evidence" and "六店" in result["answer"]
    assert not result["reports"]


def test_model_cannot_choose_a_different_month():
    from app.modules.assistant.chat import Clarify

    run = QueryRun(None, ChatScope(domain="sales"), "预测2026年11月GMV")
    with pytest.raises(Clarify):
        asyncio.run(
            dashboard.read(
                run, dashboard.DashboardRequest(capability="sales_forecast", month="2026-10")
            )
        )


def test_method_only_returns_matching_source_passage_and_excludes_worked_example():
    from app.modules.knowledge.passages import select_passages

    text = "# 4 推演剩余销售\n按两个窗口加权。\n\n# 算例\n旧示例680万元"
    assert select_passages(text, "GMV预测的权重是多少") == [("4 推演剩余销售", "按两个窗口加权。")]
    assert not select_passages(text, "售后抽检SOP")


def test_monthly_report_uses_fixed_order_and_no_generated_numbers():
    from decimal import Decimal as D

    from app.modules.assistant.forecast_reports import format_forecast

    result = {
        "month": date(2026, 11, 1),
        "cutoff": date(2026, 11, 25),
        "actual": D("1000000"),
        "remaining_days": 5,
        "complete_days": 25,
        "target": D("2000000"),
        "achievement_rate": D(".5"),
        "gap": D("1000000"),
        "needed_daily": D("200000"),
        "center_daily": D("300000"),
        "center": D("2500000"),
        "forecast_achievement_rate": D("1.25"),
        "status": "forecast",
        "scenarios": {"baseline": {"low": D("2400000"), "high": D("2600000")}},
        "windows": [],
        "comparison": None,
        "notes": [],
        "report_order": [
            "截止日和累计实绩",
            "目标与进度",
            "月末中心及区间",
            "较上次变化",
            "驱动、风险与待办",
        ],
    }
    answer = format_forecast(
        result,
        {"revision": 4, "source_url": "https://example.test/docx/sop", "state": "active"},
        {},
    )
    assert answer.index("100.00 万元") < answer.index("200.00 万元") < answer.index("250.00 万元")
    assert "240.00 万" in answer and "260.00 万" in answer
    assert "上次" in answer and "[查看预测 SOP](https://example.test/docx/sop)" in answer
    assert "第 4 版" not in answer


def test_revision_followup_requires_forecast_context():
    assert forecast_intent("第几版？", prior="预测一下11月GMV") == "knowledge"
    assert forecast_intent("预测 SOP 用的是哪一版？") == "knowledge"
    assert forecast_intent("第几版？") is None
    assert forecast_intent("商品评分用的是哪一版？") is None


@pytest.mark.parametrize("question", ["预测11月的净GMV", "如果加投20万，预测本月GMV"])
def test_other_basis_or_hypothesis_cannot_use_official_forecast(question):
    run = QueryRun(None, ChatScope(), question)
    result = asyncio.run(answer_question(None, question, "运营看板中心", [], run))
    assert result["status"] == "no_evidence" and not result["reports"]


def test_forecast_does_not_intercept_weather_or_volume():
    assert forecast_intent("预测11月的天气") is None
    assert forecast_intent("预计11月能销售多少件") is None


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("天气预测的权重怎么算？", None),
        ("推广SOP更新了吗？", None),
        ("本月退款率和GMV预计多少？", "mixed"),
        ("六店11月预计GMV和推广ROI是多少？", "mixed"),
    ],
)
def test_routes_mixed_questions_and_leaves_other_domains_alone(question, expected):
    assert forecast_intent(question) == expected


def test_forecast_cutoff_is_kept_across_method_and_followup(monkeypatch):
    calls = []

    def read(engine, capability, params):
        calls.append((capability, params))
        return dict(
            report(
                "sales" if capability == "sales_forecast" else "definitions",
                capability,
                "测试",
                "测试",
                "v",
                scope=params,
            ),
            forecast_status="forecast",
            answer="测试已取得",
        )

    monkeypatch.setattr(catalog, "read", read)
    scope = ChatScope(domain="all")
    for question in ("截至2026年11月15日，预测六店GMV", "权重是多少？", "目标还差多少"):
        deps = QueryRun(None, scope, question)
        answer = asyncio.run(answer_question(None, question, "运营看板中心", [], deps))
        scope = ChatScope.model_validate(answer["query_context"])
    assert calls[0][1]["cutoff"] == calls[2][1]["cutoff"] == date(2026, 11, 15)
    assert calls[1][0] == "business_knowledge"


def test_date_scope_never_confuses_daily_and_monthly_forecasts():
    from app.modules.assistant.chat import Clarify

    with pytest.raises(Clarify):
        forecast_period("预测今天GMV", None, TODAY)
    assert forecast_period("预测六月销售额", None, TODAY)[0] == date(2026, 6, 1)


def test_knowledge_ignores_examples_nested_under_different_headings():
    from app.modules.knowledge.passages import select_passages

    content = "# 算例\n## 推演示例\n不适用的历史比例\n# 4 推演剩余销售\n当前正式条款"
    passages = select_passages(content, "预测的权重是多少")
    assert passages == [("4 推演剩余销售", "当前正式条款")]


def test_old_interpretation_does_not_bypass_current_compiler(monkeypatch):
    from app.modules.knowledge import interpretation

    archived = {
        "state": "active",
        "rule": {"type": "old"},
        "content": "未受支持的计算公式",
        "revision": 4,
    }
    monkeypatch.setattr(interpretation, "read_forecast_sop", lambda _: archived)
    assert interpretation.effective_forecast_sop(None)["state"] == "unsupported"
    assert interpretation.effective_forecast_sop(None)["rule"] is None
    assert archived["rule"] == {"type": "old"}


def test_historical_snapshot_can_be_requested_in_operating_language():
    q = "按11月25日的完整日报预测11月GMV，截至11月25日"
    assert forecast_period(q, None, TODAY) == (date(2026, 11, 1), date(2026, 11, 25))
    assert forecast_period("按近3日和7日的日均预测本月GMV", None, TODAY)[0] == date(2026, 11, 1)
