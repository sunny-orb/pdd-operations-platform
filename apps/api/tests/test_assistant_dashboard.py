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


@pytest.mark.parametrize("words", ["六店", "六个店铺", "6家店"])
def test_actual_sales_in_all_six_shops_uses_the_published_sales_scope(backend, words):
    q = f"这个月{words}实际已经卖了多少？"
    run = QueryRun(None, ChatScope(domain="sales", shop_id="spigf"), q)
    query(run, "sales_performance", all_shops=True)
    assert backend[0][-1][1]["shop_id"] == ""


def test_unfiltered_sales_total_needs_no_confirmation_but_preserves_selected_store(backend):
    q = "这个月已经卖了多少？"
    query(QueryRun(None, ChatScope(domain="sales"), q), "sales_performance", all_shops=True)
    assert backend[0][-1][1]["shop_id"] == ""
    run = QueryRun(None, ChatScope(domain="sales", shop_id="spigf"), q)
    with pytest.raises(Exception, match="当前店铺"):
        query(run, "sales_performance", all_shops=True)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("从推广这边帮我查查六家店11月到27日合计卖了多少钱，再写一句晨会用语。", True),
        ("我在推广页，看看6家店本月累计GMV，顺便写一句简报。", True),
        ("六家店推广成交金额合计多少？", False),
        ("六家店本月实际销售额和推广投产分别是多少？", False),
        ("六家店月末GMV预测有多少？", False),
        ("六家店合计GMV和目标还差多少？", False),
    ],
)
def test_sales_total_distinguishes_page_location_from_attributed_promotion(question, expected):
    from app.modules.assistant.conversation import asks_channel_revenue

    assert asks_channel_revenue(question) is expected


def test_channel_revenue_briefs_use_sales_evidence_on_the_promotion_page(backend):
    from types import SimpleNamespace

    from pydantic_ai.usage import RunUsage

    class Model:
        async def run(self, prompt, *, deps, **kwargs):
            assert deps.answer_from_reports
            assert deps.reports[0]["capability"] == "sales_shops"
            return SimpleNamespace(
                output="六店实际成交额可用于晨会汇报。", usage=RunUsage(requests=1)
            )

    question = "在推广分析页，帮我算下六家店11月到27日合计卖了多少钱，再写一句简报。"
    run = QueryRun(
        None, ChatScope(domain="promotion_store", date_from=START, date_to=END), question
    )
    answer = asyncio.run(answer_question(Model(), question, "推广分析", [], run))
    assert answer["status"] == "answered"
    assert backend[0][0][0] == "sales_shops"
    assert backend[0][0][1]["date_from"].day == 1 and backend[0][0][1]["date_to"].day == 27


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

    from app.modules.assistant import (
        aftersales_tools,
        board_tools,
        definitions,
        sales_tools,
    )

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

    from app.modules.assistant.answer_policy import AnswerPolicy

    prepare = AnswerPolicy().prepare_tools
    tools = [object()]
    deps = QueryRun(None, ChatScope(), "推广表现如何")
    available = SimpleNamespace(
        usage=SimpleNamespace(requests=4, tool_calls=4), messages=[], deps=deps
    )
    reserve = SimpleNamespace(usage=SimpleNamespace(requests=5, tool_calls=4), deps=deps)
    assert asyncio.run(prepare(available, tools)) == tools
    assert asyncio.run(prepare(reserve, tools)) == []
    deps.answer_from_reports = True
    assert asyncio.run(prepare(available, tools)) == []


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
    assert "成交金额" in result["answer"] and "100.01" in result["answer"]
    assert "2026-11-16" in result["answer"]


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


def test_discovery_defaults_to_scope_and_all_is_explicit(backend):
    from types import SimpleNamespace

    from app.modules.assistant.chat import discover_dashboard

    ctx = SimpleNamespace(deps=QueryRun(None, ChatScope(domain="sales"), "有哪些可选项"))
    local = asyncio.run(discover_dashboard(ctx))
    global_catalog = asyncio.run(discover_dashboard(ctx, "all"))
    assert local["scope"]["domain"] == "sales"
    assert len(local["capabilities"]) < len(global_catalog["capabilities"])
    assert backend[1] == ["sales"]


def test_unresolved_product_reference_does_not_use_totals_or_model_history():
    from app.api.assistant import PriorTurn

    history = [PriorTurn(page="运营看板中心", question="优先看什么？", answer="查到商品共1772款。")]
    run = QueryRun(None, ChatScope(domain="all"), "刚才你点名的那一款，触发那天的实际值是多少？")
    result = asyncio.run(answer_question(None, run.question, "运营看板中心", history, run))
    assert result["status"] == "no_evidence" and "商品名称" in result["answer"]
    assert result["usage"]["requests"] == result["usage"]["tool_calls"] == run.query_count == 0


@pytest.mark.parametrize(
    "question",
    [
        "如果这款的成交没记录，可能是哪种情况？",
        "刚才那一款，商品ID 12345，表现怎么样？",
        "看看这款的表现，我指的是「雪原帽」。",
        "刚才那款怎么样？另外查一下六店成交额。",
    ],
)
def test_missing_reference_does_not_block_another_answerable_request(question):
    from app.modules.assistant.references import needs_product_clarification

    assert not needs_product_clarification(question)


def test_wanyuan_display_rounds_half_up_and_keeps_exact_yuan():
    r = report(
        "sales",
        "sales_performance",
        "销售",
        "销售",
        "v1",
        facts=[fact("revenue", "成交额", "10050.00", "currency")],
    )
    available = dashboard.model_view(r, "成交额是多少？")["facts"][0]
    assert available["value"] == "10050.00" and available["display"] == "1.01 万元"


def test_verified_reference_applies_to_product_followup_only(backend, monkeypatch):
    from app.modules.assistant import chat

    item = {
        "listing_id": "one",
        "product_id": "sku",
        "shop_id": "663599807",
        "shop_name": "测试店",
        "name": "测试帽",
    }
    ref = {
        "listing_id": "one",
        "shop_id": "663599807",
        "data_version": "products-v1",
        "date_from": str(START),
        "date_to": str(END),
    }
    scope = ChatScope(domain="all", risk_reference=ref)
    monkeypatch.setattr(chat, "search_products", lambda engine, keyword, **kwargs: [item])
    follow = QueryRun(None, scope, "刚才那款具体命中什么规则？")
    query(follow, "products_risks")
    req = backend[0][-1][1]
    assert req["listing_ids"] == ["one"] and (req["date_from"], req["date_to"]) == (START, END)
    assert follow.subject is None
    assert follow.response("当日访客下降。", "answered")["query_context"]["risk_reference"] == ref
    assert not follow.query_context().get("shop_id")

    other = QueryRun(None, scope, "查看所有商品的风险")
    query(other, "products_risks")
    assert backend[0][-1][1]["listing_ids"] == []
    assert other.response("共三款。", "answered")["query_context"]["risk_reference"] is None


def test_reference_version_or_identity_change_never_falls_back_to_all_products(
    backend, monkeypatch
):
    from app.modules.assistant import chat

    ref = dict(
        listing_id="gone",
        shop_id="663599807",
        data_version="old",
        date_from=str(START),
        date_to=str(END),
    )
    run = QueryRun(None, ChatScope(domain="all", risk_reference=ref), "刚才那款的触发日呢？")
    with pytest.raises(BoardError, match="DATA_VERSION_CHANGED"):
        query(run, "products_risks")
    run.scope.risk_reference.data_version = "products-v1"
    monkeypatch.setattr(chat, "search_products", lambda *args, **kwargs: [])
    with pytest.raises(chat.Clarify, match="商品"):
        query(run, "products_risks")
    assert backend[0] == []


def test_rate_only_projection_keeps_period_and_three_rates_without_unasked_diagnosis():
    from app.modules.assistant.business import table

    r = report(
        "aftersales",
        "aftersales_overview",
        "最新周报",
        "售后",
        "v1",
        period={"date_from": str(START), "date_to": str(END)},
        facts=[
            fact(key, key, "0.4", "percent")
            for key in ("presale_rate", "aftersale_rate", "total_rate", "risk_target_gap")
        ],
        tables=[
            table("整体目标区间", [("state", "目标判定", "text")], [{"state": "区间内"}]),
            table("优先行动", [], []),
        ],
    )
    plain = dashboard.model_view(r, "这期售后报告的三率分别是多少？把报告期间也告诉我。")
    assert [f["key"] for f in plain["facts"]] == ["presale_rate", "aftersale_rate", "total_rate"]
    assert not plain["tables"] and plain["period"] == r["period"]
    with_target = dashboard.model_view(r, "三率分别是多少，有没有超出上限？")
    assert any(t["title"] == "整体目标区间" for t in with_target["tables"])


def test_today_budget_decision_uses_selected_period_and_store_channels(backend):
    question = "明星店铺的投产很高，今天要不要把商品推广的预算挪过去？"
    run = QueryRun(
        None, ChatScope(domain="promotion_store", date_from=START, date_to=END), question
    )
    query(run, "promotion_overview", mode="store")
    params = backend[0][-1][1]
    assert params["mode"] == "store"
    assert (params["date_from"], params["date_to"]) == (START, END)
    query(run, "promotion_overview", channel="product_promotion")
    params = backend[0][-1][1]
    assert params["mode"] == "store" and params["channel"] == "product_promotion"


def test_action_date_keeps_actual_today_or_yesterday_metrics():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    for question, expected in [
        ("今天的投产是多少，今天要不要停？", today),
        ("查看昨天的投产，今天要不要停？", today - timedelta(days=1)),
    ]:
        run = QueryRun(None, ChatScope(domain="promotion_store"), question)
        assert run.dates("default", None, None)[:2] == (expected, expected)


def test_explicit_channel_monthly_target_keeps_published_grain_from_multishop_page(backend):
    scope = ChatScope(domain="products", shop_ids=["one", "two"])
    run = QueryRun(None, scope, "六店合计本月GMV目标达成多少？")
    query(run, "sales_targets")
    assert not backend[0][-1][1].get("shop_id")


def test_budget_advice_distinguishes_observed_roi_from_unpublished_execution_limits():
    r = report("promotion_store", "promotion_overview", "推广", "测试来源", "v1")
    view = dashboard.model_view(r, "能把商品推广的预算挪过去吗？")
    assert any("预算上限" in note and "停投线" in note for note in view["notes"])
    assert not r["notes"]
    assert not dashboard.model_view(r, "上周的实际投产是多少？")["notes"]


def test_single_style_action_does_not_inherit_category_alerts_or_report_totals():
    from app.modules.assistant.business import table

    r = report(
        "aftersales",
        "aftersales_overview",
        "售后",
        "报告",
        "v1",
        scope={"focus": "size"},
        facts=[fact("decision", "品类预警", "P1")],
        tables=[
            table(t, [], [{"detail": t}])
            for t in ["规则建议", "优先行动", "单款风险排序", "单款优先核查建议", "尺码拆分"]
        ],
    )
    action = dashboard.model_view(r, "优先处理哪一款的尺码退货，先核查什么？")
    assert "规则建议" not in {t["title"] for t in action["tables"]}
    assert "单款优先核查建议" in {t["title"] for t in action["tables"]}
    assert not action["facts"]
    assert dashboard.model_view(r, "哪款优先核查尺码，另外总退款率是多少？")["facts"]
    mixed = dashboard.model_view(r, "优先处理哪一款？全报告有哪些预警？")
    assert mixed["facts"] and any(t["title"] == "规则建议" for t in mixed["tables"])


def test_latest_size_action_gets_style_evidence_on_category_page(backend):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    def model(messages, info):
        assert "单款" in messages[-1].parts[0].content
        return ModelResponse(parts=[TextPart("按该款的尺码记录复核。")])

    question = "最新报告里，优先处理哪一款的尺码退货？今天先核查什么？"
    run = QueryRun(None, ChatScope(domain="aftersales", view="category"), question)
    result = asyncio.run(
        answer_question(Agent(FunctionModel(model)), question, "售后分析", [], run)
    )
    assert result["status"] == "answered" and result["usage"]["requests"] == 1
    assert backend[0][0][0] == "aftersales_overview"
    assert backend[0][0][1]["view"] == "style" and backend[0][0][1]["focus"] == "size"


def test_percentage_projection_formats_rates_above_one_without_losing_units():
    r = report(
        "aftersales",
        "aftersales_overview",
        "退款分析",
        "已发布报告",
        "version",
        facts=[fact("rate", "退款率", "1.4141", "percent")],
    )
    r["tables"] = [
        {
            "title": "单款风险排序",
            "total": 1,
            "offset": 0,
            "columns": [{"key": "rate", "label": "总退款率", "unit": "percent"}],
            "rows": [{"rate": "1.4141"}],
        }
    ]
    result = dashboard.model_view(r, "退款率是多少")
    assert result["facts"][0]["display"] == "141.41%"
    assert result["tables"][0]["rows"] == [["141.41%"]]
    assert r["tables"][0]["rows"] == [{"rate": "1.4141"}]
