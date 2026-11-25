"""解释与短追问共用依据机制，受控模型验证流程，不调用外部供应商。"""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.api.assistant import PriorTurn
from app.modules.assistant import catalog, chat, dashboard
from app.modules.assistant.business import report, table


def definition():
    return report(
        "definitions",
        "rule_definitions",
        "生效规则",
        "商品规则",
        "rules-v1",
        scope={"view": "products"},
        tables=[
            table(
                "规则",
                [("rule", "名称", "text"), ("condition", "条件", "text")],
                [{"rule": "严重流量下滑", "condition": "下降至少40%"}],
                total=1,
            )
        ],
        versions={"rules_products": "rules-v1"},
    )


@pytest.fixture
def rules(monkeypatch):
    calls = []

    def read(engine, capability, params):
        assert engine is None and capability == "rule_definitions"
        calls.append(params)
        return definition()

    monkeypatch.setattr(catalog, "read", read)
    return calls


def tool_call():
    return ModelResponse(
        parts=[
            ToolCallPart(
                "get_dashboard_data",
                {"request": {"capability": "rule_definitions", "view": "products"}},
            )
        ]
    )


def answer(text="按生效规则，严重流量下滑的降幅门槛是40%。"):
    return ModelResponse(parts=[TextPart(text)])


def test_short_unresolved_followup_recovers_the_original_question(rules):
    history = [
        PriorTurn(
            page="运营看板中心", question="阈值是多少", answer="旧通用回复", status="no_evidence"
        )
    ]

    def function(messages, info):
        values = str(messages)
        assert "阈值是多少" in values
        assert "上一轮未解决" in values
        assert "旧通用回复" not in values
        return (
            answer()
            if any(getattr(part, "part_kind", None) == "tool-return" for part in messages[-1].parts)
            else tool_call()
        )

    agent = Agent(FunctionModel(function), deps_type=chat.QueryRun, tools=[chat.get_dashboard_data])
    run = chat.QueryRun(None, chat.ChatScope(domain="products"), "我问你呢")
    result = asyncio.run(chat.answer_question(agent, run.question, "运营看板中心", history, run))
    assert result["status"] == "answered" and "40%" in result["answer"]
    assert result["reports"][0]["capability"] == "rule_definitions"
    assert len(rules) == 1


def test_missing_evidence_gets_one_bounded_repair_then_uses_definition(rules):
    def function(messages, info):
        if "本轮尚未取得可核验依据" in str(messages):
            return (
                answer()
                if any(
                    getattr(part, "part_kind", None) == "tool-return" for part in messages[-1].parts
                )
                else tool_call()
            )
        return answer("我猜是下降90%。")

    agent = Agent(FunctionModel(function), deps_type=chat.QueryRun, tools=[chat.get_dashboard_data])
    run = chat.QueryRun(None, chat.ChatScope(domain="products"), "那个触发条件的界限呢？")
    result = asyncio.run(chat.answer_question(agent, run.question, "商品数据", [], run))
    assert result["status"] == "answered" and "90%" not in result["answer"]
    assert result["usage"]["requests"] == 3
    assert result["usage"]["tool_calls"] == 1
    assert len(rules) == 1


def test_repair_cannot_make_unsupported_history_a_fact():
    history = [
        PriorTurn(page="商品数据", question="阈值？", answer="历史猜测90%", status="answered")
    ]
    agent = Agent(FunctionModel(lambda m, i: answer("就是90%。")), deps_type=chat.QueryRun)
    run = chat.QueryRun(None, chat.ChatScope(domain="products"), "你确定吗？")
    result = asyncio.run(chat.answer_question(agent, run.question, "商品数据", history, run))
    assert result["status"] == "no_evidence"
    assert "90%" not in result["answer"]
    assert "可以查询销售、目标" not in result["answer"]
    assert result["usage"]["requests"] == 2


def test_definition_version_changes_fail_closed(rules):
    run = chat.QueryRun(None, chat.ChatScope(versions={"rules_products": "old"}), "说明规则")
    with pytest.raises(Exception, match="DATA_VERSION_CHANGED"):
        asyncio.run(dashboard.read(run, dashboard.DashboardRequest(capability="rule_definitions")))
    assert not run.reports


def test_capability_explanation_uses_the_actual_catalog_not_model_claims():
    def function(messages, info):
        if any(getattr(p, "part_kind", None) == "tool-return" for p in messages[-1].parts):
            return answer("我还可以自动加预算。")
        return ModelResponse(
            parts=[ToolCallPart("discover_dashboard", {"domain": "all", "explain_only": True})]
        )

    agent = Agent(FunctionModel(function), deps_type=chat.QueryRun, tools=[chat.discover_dashboard])
    run = chat.QueryRun(None, chat.ChatScope(), "你支持哪些功能？")
    result = asyncio.run(chat.answer_question(agent, run.question, "运营看板中心", [], run))
    assert result["status"] == "answered"
    assert "自动加预算" not in result["answer"]
    assert "规则" in result["answer"] and "销售" in result["answer"]


def test_empty_definitions_cannot_authorize_a_threshold_answer(monkeypatch):
    empty = definition()
    empty["tables"][0]["rows"] = []
    empty["tables"][0]["total"] = 0
    monkeypatch.setattr(catalog, "read", lambda *args: empty)
    run = chat.QueryRun(None, chat.ChatScope(domain="products"), "阈值是多少")
    with pytest.raises(chat.Clarify, match="没有找到"):
        asyncio.run(dashboard.read(run, dashboard.DashboardRequest(capability="rule_definitions")))
    assert not run.reports


def test_rule_reference_and_operating_rule_version_reach_the_model():
    value = definition()
    value["scope"]["rule_version"] = "canonical-v1"
    value["tables"][0]["columns"].append(
        {"key": "rule_version", "label": "规则版本", "unit": "text"}
    )
    value["tables"][0]["rows"][0]["rule_version"] = "canonical-v1"
    short = dashboard.model_view(value, "为什么命中")
    assert short["rule_reference"]["rule_version"] == "canonical-v1"
    assert "rule_version" in {c["key"] for c in short["tables"][0]["columns"]}


def test_explanation_preserves_the_selected_report_without_changing_operating_filters(monkeypatch):
    captured = []

    def read(engine, capability, params):
        captured.append((capability, params.copy()))
        value = definition()
        value["scope"] = {"view": "aftersales", "report_id": params.get("report_id", "old-report")}
        return value

    monkeypatch.setattr(catalog, "read", read)
    run = chat.QueryRun(
        None, chat.ChatScope(domain="aftersales", report_id="new-report"), "解释旧报告"
    )
    asyncio.run(
        dashboard.read(
            run,
            dashboard.DashboardRequest(
                capability="rule_definitions", view="aftersales", report_id="old-report"
            ),
        )
    )
    context = run.query_context()
    assert context["report_id"] == "new-report"
    assert context["explanation"] == {"view": "aftersales", "report_id": "old-report"}
    follow = chat.QueryRun(None, chat.ChatScope.model_validate(context), "它的阈值呢")
    asyncio.run(dashboard.read(follow, dashboard.DashboardRequest(capability="rule_definitions")))
    assert captured[-1][1]["report_id"] == "old-report"


def test_report_rule_versions_stay_bounded_across_many_reports():
    scope = chat.ChatScope()
    for index in range(30):
        run = chat.QueryRun(None, scope, "解释这个报告")
        value = definition()
        value["scope"] = {"view": "aftersales", "report_id": f"report-{index}"}
        value["versions"] = {f"rules_aftersales_report-{index}": str(index)}
        run.report_cache[("rules", index)] = value
        scope = chat.ChatScope.model_validate(run.query_context())
        assert len(scope.versions) == 1
    assert scope.explanation.report_id == "report-29"


@pytest.mark.parametrize("page", ["运营看板中心", "商品数据", "推广分析", "店铺销售", "售后分析"])
def test_all_pages_can_use_the_same_cross_domain_tools(page, rules):
    def function(messages, info):
        return (
            answer()
            if any(getattr(p, "part_kind", None) == "tool-return" for p in messages[-1].parts)
            else tool_call()
        )

    agent = Agent(FunctionModel(function), deps_type=chat.QueryRun, tools=[chat.get_dashboard_data])
    run = chat.QueryRun(None, chat.ChatScope(domain="sales"), "商品风险怎么算")
    result = asyncio.run(chat.answer_question(agent, run.question, page, [], run))
    assert result["status"] == "answered"
    assert rules[-1]["view"] == "products"


def test_operating_aftersales_followup_uses_the_explained_report(monkeypatch):
    captured = []
    monkeypatch.setattr(catalog, "describe", lambda *args: {"data_version": "af-v1"})

    def read(engine, capability, params):
        captured.append(params)
        return report(
            "aftersales",
            capability,
            "报告",
            "本地",
            "af-v1",
            scope={"report_id": params["report_id"]},
        )

    monkeypatch.setattr(catalog, "read", read)
    run = chat.QueryRun(
        None,
        chat.ChatScope(
            domain="aftersales",
            report_id="new-report",
            explanation={"view": "aftersales", "report_id": "old-report"},
        ),
        "它为什么命中",
    )
    asyncio.run(dashboard.read(run, dashboard.DashboardRequest(capability="aftersales_overview")))
    assert captured[-1]["report_id"] == "old-report"
    context = run.query_context()
    assert context["report_id"] == "old-report" and context["explanation"] is None


def test_new_subject_failure_explicitly_clears_the_old_reference(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(chat, "search_products", lambda *a, **kw: [])
    run = chat.QueryRun(
        None,
        chat.ChatScope(
            listing_id="old",
            shop_id="old-shop",
            listing_ids=["old"],
            explanation={"view": "products"},
        ),
        "查新商品",
    )
    with pytest.raises(chat.Clarify):
        asyncio.run(chat.find_product(SimpleNamespace(deps=run), "新商品"))
    result = run.response("没有找到新商品", "no_evidence")
    assert result["subject_cleared"] is True
    assert run.scope.listing_id is None and run.scope.listing_ids == []
    assert run.scope.shop_id is None and run.scope.explanation is None


def test_unavailable_rule_reports_the_actual_boundary(monkeypatch):
    value = definition()
    value["tables"][0]["rows"] = [{"state": "not_defined", "condition": "店铺模式不适用商品SOP"}]
    monkeypatch.setattr(catalog, "read", lambda *a: value)
    run = chat.QueryRun(None, chat.ChatScope(), "店铺推广的阈值")
    with pytest.raises(chat.Clarify, match="店铺模式不适用"):
        asyncio.run(dashboard.read(run, dashboard.DashboardRequest(capability="rule_definitions")))
    assert not run.reports


def test_missing_observation_can_be_explained_in_one_round_without_a_report():
    from app.core.config import Settings

    def function(messages, info):
        if not any(tool.name == "explain_observation" for tool in info.output_tools):
            return answer("有可能。")
        return ModelResponse(
            parts=[ToolCallPart("explain_observation", {"topic": "missing_record"})]
        )

    agent = chat.build_agent(
        Settings(
            _env_file=None,
            assistant_model="bailian:deepseek-v4.1-flash-1017",
            bailian_api_key="unused",
        )
    )
    run = chat.QueryRun(
        None, chat.ChatScope(domain="products"), "同店其他款这天有记录，独缺这款，说明什么？"
    )
    with agent.override(model=FunctionModel(function)):
        result = asyncio.run(chat.answer_question(agent, run.question, "商品数据", [], run))
    assert result["status"] == "answered" and not result["reports"]
    assert "同店" in result["answer"] and "在售" in result["answer"]
    assert result["usage"]["requests"] == 1


def test_identical_tool_rounds_stop_early_but_distinct_scopes_stay_available(rules, caplog):
    from app.modules.assistant.answer_policy import AnswerPolicy

    caplog.set_level("INFO", logger="uvicorn.error.assistant")
    calls = []

    def function(messages, info):
        if not info.function_tools:
            return answer()
        calls.append(True)
        response = tool_call()
        if len(calls) == 1:
            response.parts[0].args["request"]["keyword"] = "流量"
        return response

    agent = Agent(
        FunctionModel(function),
        deps_type=chat.QueryRun,
        tools=[chat.get_dashboard_data],
        capabilities=[AnswerPolicy()],
    )
    run = chat.QueryRun(None, chat.ChatScope(), "看看两组规则")
    result = asyncio.run(chat.answer_question(agent, run.question, "店铺销售", [], run))
    assert result["status"] == "answered"
    assert result["usage"]["requests"] == 4
    assert len(calls) == 3 and len(rules) == 2
    assert caplog.text.count("assistant_model request_id=") == 4
    assert caplog.text.count("tool=get_dashboard_data ") == 3


def test_changed_result_of_identical_calls_is_still_progress():
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from app.modules.assistant.answer_policy import repeated_tool_round

    def result(value):
        return ModelRequest(parts=[ToolReturnPart("get_dashboard_data", {"value": value})])

    assert not repeated_tool_round([tool_call(), result(10), tool_call(), result(12)])


def test_discovering_capabilities_after_failed_read_preserves_the_missing_evidence():
    def function(messages, info):
        num_results = sum(
            part.part_kind == "tool-return" for message in messages for part in message.parts
        )
        if num_results == 0:
            return tool_call()
        if num_results == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("discover_dashboard", {"domain": "products", "explain_only": True})
                ]
            )
        return answer("支持很多功能")

    def unavailable(*args, **kwargs):
        raise chat.BoardError("RULE_DEFINITION_UNAVAILABLE", 503)

    run = chat.QueryRun(None, chat.ChatScope(domain="products"), "核实商品风险的实际阈值")
    run.query = unavailable
    agent = Agent(
        FunctionModel(function),
        deps_type=chat.QueryRun,
        tools=[chat.get_dashboard_data, chat.discover_dashboard],
    )
    result = asyncio.run(chat.answer_question(agent, run.question, "商品数据", [], run))
    assert result["status"] == "no_evidence"
    assert "阈值" in result["answer"] and "当前支持" not in result["answer"]
