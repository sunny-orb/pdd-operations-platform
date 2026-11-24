"""统一工具层的范围、版本与连续对话回归；领域取数另由真实PG套件验收。"""

import asyncio
from datetime import date

import pytest

from app.modules.assistant import catalog, dashboard
from app.modules.assistant.business import fact, report
from app.modules.assistant.chat import (
    ChatScope,
    QueryRun,
    answer_question,
    get_dashboard_data,
)
from app.modules.product_board.repository import BoardError

START, END = date(2026, 11, 10), date(2026, 11, 16)


@pytest.fixture
def backend(monkeypatch):
    calls, discoveries = [], []

    def describe(engine, domain):
        discoveries.append(domain)
        shop = {"id": "spigf", "name": "测试店", "shop_source_id": "663599807"}
        if domain in ("products", "promotion_store", "promotion_product"):
            shop = {"id": "663599807", "name": "测试店"}
        return {
            "domain": domain,
            "data_version": domain + "-v1",
            "date_from": "2026-10-01",
            "date_to": "2026-11-23",
            "shops": [shop],
            "reports": [{"id": "report-a", "label": "测试报告"}],
        }

    def read(engine, capability, params):
        calls.append((capability, dict(params)))
        domain = catalog.domain_for(capability, params.get("mode", "product"))
        period = {
            "date_from": str(params.get("date_from") or START),
            "date_to": str(params.get("date_to") or END),
        }
        return report(
            domain,
            capability,
            "测试查询",
            "本地发布",
            domain + "-v1",
            period=period,
            scope={
                key: params[key]
                for key in ("shop_id", "listing_ids", "category", "month", "report_id")
                if key in params
            },
            facts=[fact("revenue", "成交金额", "100.01", "currency")],
        )

    monkeypatch.setattr(catalog, "describe", describe)
    monkeypatch.setattr(catalog, "read", read)
    return calls, discoveries


def query(run, capability, **params):
    return asyncio.run(
        dashboard.read(run, dashboard.DashboardRequest(capability=capability, **params))
    )


def test_selected_store_overall_and_all_products_do_not_expand_to_all_stores(backend):
    for question in ("当前店铺整体表现怎么样", "这家店全部商品哪些有风险"):
        scope = ChatScope(domain="sales", shop_id="spigf", filter_shop_id="spigf")
        run = QueryRun(None, scope, question)
        cap = "sales_performance" if "整体" in question else "products_risks"
        query(run, cap)
        assert backend[0][-1][1]["shop_id"] == (
            "spigf" if cap == "sales_performance" else "663599807"
        )
    query(QueryRun(None, scope, "全部店铺销售贡献"), "sales_shops")
    assert backend[0][-1][1]["shop_id"] == ""


def test_multi_selected_product_dimensions_keep_original_intersections(backend):
    run = QueryRun(
        None,
        ChatScope(
            domain="products",
            shop_ids=["663599807", "799193231"],
            category_values=["帽子", "水杯"],
            operator_values=["甲", "乙"],
            level_values=["A", "B"],
            listing_ids=["listing-a", "listing-b"],
        ),
        "当前范围经营怎么样",
    )
    query(run, "products_overview")
    params = backend[0][-1][1]
    assert params["shop_id"] == ("663599807", "799193231")
    assert params["category"] == ("帽子", "水杯")
    assert params["operator"] == ("甲", "乙")
    assert params["level"] == ("A", "B")
    assert params["listing_ids"] == ["listing-a", "listing-b"]
    with pytest.raises(Exception, match="多家店铺"):
        query(run, "sales_performance")


def test_run_cache_and_version_gate_keep_fact_payload_local(backend):
    run = QueryRun(None, ChatScope(), "最近7天销售表现")
    first = query(run, "sales_performance")
    assert query(run, "sales_performance") == first
    assert len(backend[0]) == 1 and backend[1] == ["sales"]
    stale = QueryRun(None, ChatScope(versions={"sales": "old"}), "最近7天销售")
    with pytest.raises(BoardError, match="DATA_VERSION_CHANGED"):
        query(stale, "sales_performance")
    assert len(backend[0]) == 1


def test_business_period_survives_weather_and_scoring_window(backend):
    run = QueryRun(None, ChatScope(), "经营和天气")
    sales = report(
        "sales",
        "sales_performance",
        "经营",
        "销售",
        "s1",
        period={"date_from": str(START), "date_to": str(END)},
    )
    weather = report(
        "weather",
        "weather",
        "天气",
        "天气",
        "w1",
        period={"date_from": "2026-11-23", "date_to": "2026-11-23"},
    )
    run.report_cache = {("sales",): sales, ("weather",): weather}
    context = run.query_context()
    assert context["domain"] == "sales" and context["date_from"] == str(START)
    score = report(
        "products",
        "products_scores",
        "评分",
        "商品",
        "p1",
        period={"date_from": "2026-11-17", "date_to": "2026-11-23"},
        scope={"requested_period": {"date_from": str(START), "date_to": str(END)}},
    )
    run.report_cache[("score",)] = score
    assert run.query_context()["date_to"] == str(END)
    run.report_cache = {
        ("aftersales",): report(
            "aftersales",
            "aftersales_overview",
            "售后",
            "售后",
            "a1",
            period={"date_from": "2026-10-25", "date_to": "2026-11-23"},
            scope={"report_id": "report-a"},
        )
    }
    context = run.query_context()
    assert context["report_id"] == "report-a" and "date_from" not in context
    follow = QueryRun(
        None, ChatScope(domain="aftersales", date_from=START, date_to=END), "再看推广"
    )
    assert follow.dates("default", None, None, max_days=366) == (None, None, 7)


def test_aftersales_uses_report_without_operating_scope_and_lazy_product_lookup(
    backend, monkeypatch
):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from app.modules.assistant import chat

    def forbidden(*args, **kwargs):
        pytest.fail("天气查询不应预先访问已失效的商品")

    monkeypatch.setattr(chat, "search_products", forbidden)

    def function(messages, info):
        if any(getattr(part, "part_kind", None) == "tool-return" for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("天气以最新发布为准。")])
        return ModelResponse(
            parts=[ToolCallPart("get_dashboard_data", {"request": {"capability": "weather"}})]
        )

    agent = Agent(FunctionModel(function), deps_type=QueryRun, tools=[get_dashboard_data])
    run = QueryRun(None, ChatScope(listing_id="expired", shop_id="663599807"), "当前天气怎么样")
    result = asyncio.run(answer_question(agent, run.question, "运营看板中心", [], run))
    assert result["status"] == "answered"
    assert result["reports"][0]["domain"] == "weather"
    af = QueryRun(
        None,
        ChatScope(domain="aftersales", report_id="report-a", date_from=START, date_to=END),
        "当前报告退款情况",
    )
    query(af, "aftersales_overview")
    params = backend[0][-1][1]
    assert params["report_id"] == "report-a"
    assert not ({"date_from", "date_to", "shop_id", "listing_ids", "mode"} & params.keys())


def test_catalog_lists_every_implemented_capability_and_curated_formula():
    from typing import get_args

    from app.modules.assistant import aftersales_tools, board_tools, definitions, sales_tools

    implemented = (
        set(aftersales_tools.CAPABILITIES)
        | set(board_tools.PRODUCT_CAPABILITIES)
        | set(board_tools.PROMOTION_CAPABILITIES)
        | set(sales_tools.CAPABILITIES)
        | {"metric_definitions", "rule_definitions"}
    )
    assert set(get_args(catalog.Capability)) == set(catalog.CAPABILITIES) == implemented
    result = definitions.read(None, "metric_definitions", {"keyword": "转化率"})
    rows = result["tables"][0]["rows"]
    operating = next(item for item in rows if item["metric"] == "商品经营支付转化率")
    scoring = next(item for item in rows if item["metric"] == "新品评分支付转化率")
    assert operating["numerator"] == "支付买家数"
    assert scoring["numerator"] == "支付订单数"
    assert operating["denominator"] == scoring["denominator"] == "商品访客数"


def test_all_shop_context_roundtrips_and_model_payload_is_compact(backend):
    from types import SimpleNamespace

    from app.modules.assistant.chat import get_dashboard_data

    scope = ChatScope.model_validate(
        {
            "shop_id": "",
            "filter_shop_id": "",
            "domain": "sales",
            "date_from": str(START),
            "date_to": str(END),
        }
    )
    assert scope.shop_id is None and scope.filter_shop_id is None
    run = QueryRun(None, scope, "当前期间销售表现")
    request = dashboard.DashboardRequest(capability="sales_performance")
    first = asyncio.run(get_dashboard_data(SimpleNamespace(deps=run), request))
    second = asyncio.run(get_dashboard_data(SimpleNamespace(deps=run), request))
    assert first["facts"][0]["value"] == "100.01"
    assert "versions" not in first
    assert second["status"] == "already_queried"
    assert len(backend[0]) == 1


def test_tool_budget_reserves_final_response():
    from types import SimpleNamespace

    from app.modules.assistant.chat import budgeted_tool

    tool = object()
    assert (
        asyncio.run(
            budgeted_tool(SimpleNamespace(usage=SimpleNamespace(requests=4, tool_calls=4)), tool)
        )
        is tool
    )
    assert (
        asyncio.run(
            budgeted_tool(SimpleNamespace(usage=SimpleNamespace(requests=5, tool_calls=4)), tool)
        )
        is None
    )


def test_scalar_and_all_scope_replace_old_multiselect_on_followup(backend):
    scope = ChatScope(
        domain="products", shop_ids=["663599807", "799193231"], category_values=["帽子", "水杯"]
    )
    run = QueryRun(None, scope, "全部店铺的商品表现")
    query(run, "products_overview")
    context = run.query_context()
    assert context["shop_id"] == "" and context["shop_ids"] == []
    follow = QueryRun(None, ChatScope.model_validate(context), "再看商品风险")
    query(follow, "products_risks")
    assert backend[0][-1][1]["shop_id"] == ""
    run = QueryRun(None, scope, "新类的商品表现")
    query(run, "products_overview", category="新类")
    context = run.query_context()
    assert context["category"] == "新类" and context["category_values"] == []
    query(QueryRun(None, ChatScope.model_validate(context), "再看商品风险"), "products_risks")
    assert backend[0][-1][1]["category"] == "新类"


def test_explicit_all_scope_cannot_be_narrowed_by_model_parameters(backend):
    run = QueryRun(
        None,
        ChatScope(domain="products", shop_id="663599807", listing_ids=["selected"]),
        "所有店铺全部商品表现怎么样",
    )
    query(run, "products_overview", all_shops=True, shop_id="663599807", listing_ids=["selected"])
    params = backend[0][-1][1]
    assert params["shop_id"] == "" and params["listing_ids"] == []


def test_formula_question_is_grounded_without_unnecessary_model_tools():
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    def function(messages, info):
        assert "已取得本题口径" in messages[-1].parts[0].content
        assert "支付买家数" in messages[-1].parts[0].content
        return ModelResponse(parts=[TextPart("经营转化看买家，评分转化看订单。")])

    agent = Agent(FunctionModel(function), deps_type=QueryRun)
    run = QueryRun(None, ChatScope(), "商品经营和新品评分支付转化率口径有区别吗？")
    result = asyncio.run(answer_question(agent, run.question, "商品数据", [], run))
    assert result["status"] == "answered"
    assert result["reports"][0]["capability"] == "metric_definitions"
    assert result["usage"]["requests"] == 1 and result["usage"]["tool_calls"] == 0


def test_budget_exhaustion_retains_existing_evidence(backend):
    from pydantic_ai.exceptions import UsageLimitExceeded

    class ExhaustedAgent:
        async def run(self, *args, **kwargs):
            raise UsageLimitExceeded("本轮预算耗尽")

    run = QueryRun(None, ChatScope(), "近7天销售表现")
    query(run, "sales_performance")
    result = asyncio.run(answer_question(ExhaustedAgent(), run.question, "店铺销售", [], run))
    assert result["status"] == "answered"
    assert result["reports"][0]["capability"] == "sales_performance"
    assert "已取得部分查询结果" in result["answer"]


def test_documented_promotion_formulas_match_real_domain_calculation():
    from decimal import Decimal, localcontext

    from app.modules.assistant import definitions
    from app.modules.promotion.metrics import aggregate

    data = {
        "attributed_revenue": Decimal("173.50"),
        "net_attributed_revenue": Decimal("151.25"),
        "spend": Decimal("31.00"),
        "clicks": Decimal("17"),
        "impressions": Decimal("239"),
        "attributed_deals": Decimal("3"),
    }
    metrics = aggregate([data])
    for key, _label, numerator, denominator in definitions._PROMOTION:
        assert metrics[key]["numerator"] == data[numerator]
        assert metrics[key]["denominator"] == data[denominator]
        with localcontext() as ctx:
            ctx.prec = 50
            assert metrics[key]["value"] == data[numerator] / data[denominator]
