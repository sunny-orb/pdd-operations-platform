"""公共 SOP 的运营问答与正式预估共用来源，各自保留适用范围。"""

import asyncio
from datetime import date
from types import SimpleNamespace

from pydantic_ai import PartDeltaEvent
from pydantic_ai.messages import TextPartDelta
from pydantic_ai.usage import RunUsage

from app.modules.assistant import catalog, dashboard
from app.modules.assistant.business import report
from app.modules.assistant.chat import ChatScope, QueryRun, answer_question
from app.modules.assistant.forecast_answers import forecast_intent
from app.modules.assistant.forecast_reports import _knowledge
from app.modules.knowledge.passages import select_passages
from app.modules.product_board.repository import BoardError


def test_operational_advice_about_a_forecast_sop_does_not_trigger_an_unasked_gmv_number():
    question = "月度 GMV 预测 SOP 建议怎样核查投放效率？"
    assert forecast_intent(question) == "guidance"
    assert (
        forecast_intent(question + "结合近7天的实际推广数据，告诉我先看什么。")
        == "advice_with_data"
    )
    assert forecast_intent("预测11月六店GMV，顺便看推广花费") == "mixed"


def test_operational_guidance_matches_source_without_forecast_keyword():
    content = (
        "# 3 判断销售速度和投放效率\n对比近三日的花费和成交。\n"
        "# 5 补充经营证据\n核实天气和库存，再判断是否影响销售。\n"
        "# 历史算例\n## 投放效率\n样例 ROI 是 8。"
    )
    assert select_passages(content, "最近投放效率下降了，建议先看什么？") == [
        ("3 判断销售速度和投放效率", "对比近三日的花费和成交。")
    ]
    assert select_passages(content, "缺货和天气对本月有影响吗？") == [
        ("5 补充经营证据", "核实天气和库存，再判断是否影响销售。")
    ]
    assert not select_passages(content, "这次售后退款要怎么审批？")
    assert not select_passages(content, "售后预测 SOP 建议怎么核查售后转化率？")
    assert not select_passages(content, "有什么建议？")


def test_sop_excerpt_alone_cannot_substantiate_a_live_operating_number(monkeypatch):
    q = "月度 GMV 预测 SOP 建议怎样核查投放效率？结合近7天的实际推广明细回答。"
    excerpt = "请在同一期间对比花费与成交。"
    knowledge = dict(
        report("definitions", "business_knowledge", "公共 SOP", "预测 SOP", "4"),
        answer=excerpt,
        forecast_status="method",
    )
    monkeypatch.setattr(catalog, "read", lambda *_, **kwargs: knowledge)

    class NoData:
        async def run(self, message, **kwargs):
            assert excerpt in message
            return SimpleNamespace(output="花费上涨了 90%。", usage=RunUsage())

    result = asyncio.run(
        answer_question(NoData(), q, "店铺销售", [], QueryRun(None, ChatScope(), q))
    )
    assert result["status"] == "no_evidence"
    assert excerpt in result["answer"] and "90%" not in result["answer"]


def test_advice_stream_and_finished_answer_hide_provider_tool_markup(monkeypatch):
    q = "月度 GMV 预测 SOP 建议怎样核查投放效率？结合近7天的实际推广明细回答。"
    data = dict(
        report("definitions", "business_knowledge", "公共 SOP", "预测 SOP", "4"),
        answer="核对实际投产。",
        forecast_status="method",
    )
    monkeypatch.setattr(catalog, "read", lambda *_, **kwargs: data)
    fragments = [
        "<",
        "｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke>内部参数",
        "</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>",
        "先看投产。",
    ]

    class WithToolMarkup:
        async def run(self, message, *, deps, event_stream_handler, **options):
            deps.report_cache["promotion_overview",] = report(
                "promotion_store", "promotion_overview", "推广", "推广汇总", "v"
            )

            async def tokens():
                for fragment in fragments:
                    yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=fragment))

            await event_stream_handler(None, tokens())
            return SimpleNamespace(output="".join(fragments), usage=RunUsage())

    run = QueryRun(None, ChatScope(), q)
    events = []
    run.emit = events.append
    answer = asyncio.run(answer_question(WithToolMarkup(), q, "店铺销售", [], run))
    assert answer["answer"] == "先看投产。"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == answer["answer"]


def test_simple_channel_advice_synthesizes_the_sop_and_published_total_once(monkeypatch):
    q = "月度 GMV 预测 SOP 建议怎样核查投放效率？结合看板近7天的实际推广数据，告诉我先看什么。"
    calls = []

    async def read(run, request):
        calls.append(request.capability)
        if request.capability == "business_knowledge":
            value = dict(
                report("definitions", request.capability, "预测 SOP", "飞书", "4"),
                answer="对比同一期间的 ROI。",
                forecast_status="method",
            )
        else:
            assert request.mode == "store"
            value = report(
                "promotion_store",
                request.capability,
                "推广",
                "推广汇总",
                "p",
                facts=[dict(key="roi", label="投产比", unit="ratio", value="7.63")],
            )
        run.report_cache[request.capability,] = value
        return value

    monkeypatch.setattr(dashboard, "read", read)

    class Synthesis:
        async def run(self, prompt, *, deps, **kwargs):
            assert deps.answer_from_reports and "7.63" in prompt and "同一期间" in prompt
            return SimpleNamespace(output="先对比本期投产比 7.63 和上期表现。", usage=RunUsage())

    run = QueryRun(None, ChatScope(domain="sales"), q)
    result = asyncio.run(answer_question(Synthesis(), q, "店铺销售", [], run))
    assert result["status"] == "answered" and calls == ["business_knowledge", "promotion_overview"]


def test_missing_promotion_feed_can_still_answer_with_the_confirmed_check(monkeypatch):
    q = "月度 GMV 预测 SOP 建议怎样核查投放效率？结合看板近7天的实际推广数据，告诉我先看什么。"

    async def read(run, request):
        if request.capability == "promotion_overview":
            raise BoardError("PROMOTION_NOT_CONNECTED", 503)
        value = dict(
            report("definitions", "business_knowledge", "公共 SOP", "预测 SOP", "4"),
            answer="先对比推广成交和花费。",
            forecast_status="method",
        )
        run.report_cache[request.capability,] = value
        return value

    monkeypatch.setattr(dashboard, "read", read)
    result = asyncio.run(answer_question(None, q, "店铺销售", [], QueryRun(None, ChatScope(), q)))
    assert result["status"] == "no_evidence"
    assert "经营数据" in result["answer"] and "先对比推广成交和花费" in result["answer"]


def test_rejected_forecast_shows_current_conflicting_sentences_but_other_guidance_is_readable():
    from app.modules.assistant.forecast_reports import _source_unavailable

    source = dict(
        state="unsupported",
        revision=8,
        title="预测 SOP",
        source_url="https://example.test/docx/test",
        reason="interpretation_rejected",
        rule=None,
        content="# 5 经营证据\n天气和库存需分别核实。",
        issues=[
            dict(
                message="近 3 日权重有两种写法",
                citations=[
                    dict(section="4. 月末预测", quote="近三日占七成"),
                    dict(section="6. 说明", quote="近三日占四成"),
                ],
            )
        ],
    )
    for answer in (_source_unavailable(source), _knowledge(source, "预测公式哪里冲突？")[0]):
        assert "七成" in answer and "四成" in answer and "6. 说明" in answer
        assert "万元" not in answer and "近 3 日权重有两种写法" in answer
    answer, headings = _knowledge(source, "天气和库存要看什么？")
    assert "分别核实" in answer and headings == ["5 经营证据"] and "冲突" not in answer
    assert forecast_intent("哪两句矛盾？", has_context=True) == "knowledge"
    assert forecast_intent("哪两句矛盾？") is None


def test_different_user_assumptions_are_request_scoped(monkeypatch):
    calls = []

    def read(engine, capability, params):
        calls.append(params)
        return dict(
            report(
                "sales",
                capability,
                "预测",
                "测试",
                "v",
                scope={k: params[k] for k in ("month", "cutoff")},
            ),
            answer="预测结果",
            forecast_status="forecast",
        )

    monkeypatch.setattr(catalog, "read", read)
    scope = ChatScope()
    for question in (
        "截至11月25日，如果近3日占60%、近7日占40%，预计11月GMV多少？",
        "那正式预测呢？",
    ):
        run = QueryRun(None, scope, question)
        result = asyncio.run(answer_question(None, question, "运营看板中心", [], run))
        assert result["status"] == "answered"
        scope = ChatScope.model_validate(result["query_context"])
    assert calls[0]["scenario_weights"] == ((3, "0.6"), (7, "0.4"))
    assert calls[0]["cutoff"] == date(2026, 11, 25)
    assert calls[1]["cutoff"] == calls[0]["cutoff"]
    assert "scenario_weights" not in calls[1]


def test_incomplete_or_mixed_assumptions_do_not_produce_a_formal_amount():
    for terms in ("近3日权重改成60%", "近3日占70%、近7日占80%", "近3日占60%、近7日占40%再加投2万"):
        q = f"如果{terms}，预测11月六店GMV多少？"
        result = asyncio.run(
            answer_question(None, q, "商品数据", [], QueryRun(None, ChatScope(), q))
        )
        assert result["status"] == "no_evidence" and not result["reports"]
