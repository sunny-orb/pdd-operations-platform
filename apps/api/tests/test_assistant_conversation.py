"""同一问答入口的换题、开放回答和经营事实边界。"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.api.assistant import PriorTurn
from app.core.config import DIRECT_DEEPSEEK_MODEL, Settings
from app.main import create_app
from app.modules.assistant import chat
from tests.test_assistant import BODY, DEEPSEEK, URL


def settings(model=DIRECT_DEEPSEEK_MODEL):
    return Settings(
        _env_file=None,
        assistant_model=model,
        deepseek_api_key="test",
        bailian_api_key="test",
        assistant_business_data_enabled=True,
        assistant_business_providers=[model.split(":")[0]],
    )


@pytest.mark.parametrize(
    ("model", "question"),
    [
        (DIRECT_DEEPSEEK_MODEL, "你是哪个模型？"),
        (DEEPSEEK, "我问你是啥模型？"),
    ],
)
def test_switch_from_forecast_to_model_identity_reads_only_active_service_configuration(
    model, question, monkeypatch
):
    from app.api import assistant

    def unexpected(*args, **kwargs):
        raise AssertionError("询问已配置的模型名时不需要启动模型或读库")

    monkeypatch.setattr(assistant, "build_agent", unexpected)
    app = create_app(settings(model))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        response = client.post(
            URL,
            json={
                **BODY,
                "question": question,
                "context": {"domain": "all", "forecast": {"month": "2026-11"}},
                "history": [
                    {
                        "page": "运营看板中心",
                        "question": "预测一下11月GMV",
                        "answer": "预测示例为 56 万元。",
                    }
                ],
            },
        )
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered" and model.split(":")[1] in answer["answer"]
    assert (
        "万元" not in answer["answer"]
        and not answer["reports"]
        and answer["usage"]["requests"] == 0
    )


def ask(question, function, *, history=(), scope=None, emit=None):
    agent = chat.build_agent(settings())
    run = chat.QueryRun(None, chat.ChatScope(**(scope or {})), question, emit=emit)

    async def stream(messages, info):
        for part in function(messages, info).parts:
            yield part.content

    with agent.override(model=FunctionModel(function, stream_function=stream)):
        return asyncio.run(chat.answer_question(agent, question, "商品数据", history, run))


@pytest.mark.parametrize("scope", [{"domain": "all"}, {"domain": "products", "listing_id": "hat"}])
def test_open_copywriting_is_a_one_round_answer_in_both_modes(scope):
    draft = "小手一握，把热乎带去春游。"
    result = ask(
        "帮我写两句保温杯主图文案，语气自然点。",
        lambda *_: ModelResponse(parts=[TextPart(draft)]),
        scope=scope,
    )
    assert result["status"] == "answered" and result["answer"] == draft
    assert result["usage"]["requests"] == 1 and not result["reports"] and not result["evidence"]


def test_model_can_choose_a_natural_answer_for_an_unanticipated_open_question():
    reply = "一间好逛的店，首先得让人找得到需要的东西。"

    def agent(messages, info):
        if any(t.name == "answer_general" for t in info.output_tools):
            return ModelResponse(parts=[ToolCallPart("answer_general", {"answer": reply})])
        return ModelResponse(parts=[TextPart(reply)])

    result = ask("你心里那种让人逛着舒服的店长什么样？", agent)
    assert result["status"] == "answered" and result["answer"] == reply
    assert result["usage"]["requests"] == 1 and not result["reports"]


@pytest.mark.parametrize(
    ("question", "domain", "topic", "expected"),
    [
        ("你能干啥", "all", "overview", "文案"),
        ("日常哪几件活儿能交给你？", "products", "overview", "销售"),
        ("光聊聊能帮我把词儿捋顺么？", "sales", "writing", "写"),
        ("那我自己得进后台操作吗？", "promotion_store", "actions", "预算"),
        ("款式退货这种事你也能看吗？", "aftersales", "aftersales_analysis", "款式"),
        ("跟你搭话有什么小技巧？", "products", "usage", "例如"),
    ],
)
def test_assistant_help_is_a_semantic_one_step_answer(question, domain, topic, expected):
    def model(messages, info):
        if any(t.name == "describe_assistant" for t in info.output_tools):
            return ModelResponse(parts=[ToolCallPart("describe_assistant", {"topic": topic})])
        return ModelResponse(parts=[TextPart("我得先查报告。")])

    result = ask(question, model, scope={"domain": domain})
    assert result["status"] == "answered" and expected in result["answer"]
    assert result["usage"]["requests"] == 1 and not result["reports"]


@pytest.mark.parametrize("typed", [False, True])
def test_safe_plain_and_structured_replies_have_the_same_delivery_contract(typed):
    body = "先听顾客觉得哪里不值，再决定是讲使用场景，还是推荐更合适的一款。"
    part = ToolCallPart("answer_general", {"answer": body}) if typed else TextPart(body)
    result = ask("人家看完就嫌贵，你会怎么接这个话茬？", lambda *_: ModelResponse(parts=[part]))
    assert result["status"] == "answered" and result["answer"] == body
    assert result["usage"]["requests"] == 1 and not result["reports"]


@pytest.mark.parametrize("typed", [False, True])
def test_an_open_reply_cannot_establish_company_policy_or_execute_a_write(typed):
    for draft in ("按我们公司的规定，所有退款必须由主管审批。", "我已经替你调高了投放预算。"):
        part = ToolCallPart("answer_general", {"answer": draft}) if typed else TextPart(draft)
        result = ask("那你怎么看这事儿？", lambda *_, part=part: ModelResponse(parts=[part]))
        assert draft not in result["answer"] and not result["reports"]


def test_directory_is_not_a_trigger_to_replace_a_harmless_plain_answer():
    def model(messages, info):
        if not any(p.part_kind == "tool-return" for msg in messages for p in msg.parts):
            return ModelResponse(
                parts=[ToolCallPart("discover_dashboard", {"domain": "all", "explain_only": True})]
            )
        return ModelResponse(parts=[TextPart("先问顾客在意什么，再决定怎么开口。")])

    result = ask("接待新客你最看重的是什么？", model)
    assert result["status"] == "answered" and "先问顾客" in result["answer"]
    assert result["usage"]["requests"] == 2


def test_help_and_discovery_only_advertise_enabled_sources():
    from app.modules.assistant import catalog
    from app.modules.assistant.conversation import AssistantHelp

    hidden = {"sales_forecast", "business_knowledge"}
    entries = catalog.catalog(public_sops_enabled=False)
    assert hidden.isdisjoint(row["capability"] for row in entries)
    for key in hidden:
        assert "尚未接入" in AssistantHelp(topic=key).answer(
            model_id=None, public_sops_enabled=False
        )


@pytest.mark.parametrize(
    "sentence",
    [
        "如果要举例，也得依据本公司的规定，所有退款必须由主管审批。",
        "根据本店数据，顾客变少就是缺货导致的。",
    ],
)
def test_plain_general_answer_cannot_establish_rules_or_specific_causes(sentence):
    result = ask(
        "要是有新同事问起这事，你会怎么跟他聊？",
        lambda *_: ModelResponse(parts=[TextPart(sentence)]),
    )
    assert result["status"] == "no_evidence" and sentence not in result["answer"]


@pytest.mark.parametrize(
    "question",
    [
        "80位访客，4人付款，付款的人占多大比例？",
        "假设本店80人进来、4人付款，转化率是多少？",
    ],
)
def test_ratio_from_user_supplied_inputs_does_not_require_a_dashboard_query(question):
    reply = "按你给的数字，4 ÷ 80 = 5%。"
    result = ask(question, lambda *_: ModelResponse(parts=[TextPart(reply)]))
    assert result["answer"] == reply and result["status"] == "answered"
    assert result["usage"]["requests"] == 1 and not result["reports"]


def test_commercial_copy_replaces_unsupported_specifications_before_delivery():
    drafts = iter(["316 不锈钢，24 小时不漏水。", "开学路上，带上你喜欢的那一杯。"])
    emitted = []
    result = ask(
        "给保温杯写一条开学季主图文案。",
        lambda *_: ModelResponse(parts=[TextPart(next(drafts))]),
        emit=emitted.append,
    )
    assert result["status"] == "answered" and "开学路上" in result["answer"]
    assert result["usage"]["requests"] == 2
    assert all("316" not in e["text"] and "不漏水" not in e["text"] for e in emitted)


def test_product_copy_can_use_an_explicitly_supplied_specification():
    reply = "316 不锈钢内胆，陪你走进新学期。"
    result = ask(
        "已确认卖点为 316 不锈钢内胆，请写一条主图文案。",
        lambda *_: ModelResponse(parts=[TextPart(reply)]),
    )
    assert result["status"] == "answered" and result["answer"] == reply
    assert result["usage"]["requests"] == 1


def test_request_for_one_title_delivers_a_single_supported_line_after_a_sales_turn():
    old = PriorTurn(page="商品数据", question="六店一共卖了多少？", answer="约 100 万元。")
    draft = "两条备选：\n1. 秋游不怕晒，防晒帽出发。\n2. 戴上帽子，去和秋天碰个面。"
    result = ask(
        "换个话题，帮我想一句秋游帽子的标题。",
        lambda *_: ModelResponse(parts=[TextPart(draft)]),
        history=[old],
    )
    assert result["status"] == "answered"
    assert result["answer"] == "戴上帽子，去和秋天碰个面。" and not result["reports"]
    assert result["usage"]["requests"] == 1


def test_drafting_for_this_product_uses_its_verified_name_only(monkeypatch):
    product = dict(listing_id="selected", shop_id="one", name="云朵雨伞", shop_name="一店")
    calls = []

    def lookup(engine, listing, *, shop_id):
        calls.append((listing, shop_id))
        return [product]

    monkeypatch.setattr(chat, "search_products", lookup)

    def model(messages, info):
        prompt = messages[-1].parts[-1].content
        assert "云朵雨伞" in prompt
        assert not info.function_tools
        return ModelResponse(parts=[TextPart("云朵雨伞，陪你走过下雨天。")])

    answer = ask(
        "帮我给这款商品写一句主图文案。", model, scope={"listing_id": "selected", "shop_id": "one"}
    )
    assert answer["status"] == "answered" and calls == [("selected", "one")]
    assert answer["subject"]["name"] == "云朵雨伞" and not answer["reports"]


def test_general_method_with_metric_words_accepts_an_explicit_no_lookup_request():
    q = "碰到本周销量下滑，一般怎么排查？先只讲思路，不用查我店的数据。"
    body = "先拆流量和转化，再核对价格、库存或活动是否同时有变化。"

    def model(messages, info):
        assert not info.function_tools
        return ModelResponse(parts=[TextPart(body)])

    answer = ask(q, model)
    assert answer["status"] == "answered" and answer["answer"] == body


def test_a_general_method_plus_sales_request_does_not_advertise_unrelated_sops():
    from app.modules.assistant.knowledge_answers import directory_prompt

    prompt = directory_prompt("先只讲排查思路，再看看六店本月的销售表现。")
    assert "可选的公共 SOP" not in prompt and "实绩" in prompt
    assert "可选的公共 SOP" in directory_prompt("按公司的 SOP 讲排查思路，再看看六店销售表现。")


def test_rewriting_must_preserve_the_whole_amount():
    history = [PriorTurn(page="店铺销售", question="这笔是多少？", answer="这笔成交额是 199 元。")]
    replies = iter(["这笔成交额是 99 元。", "这笔成交额是 199 元。"])
    result = ask(
        "把上文缩成一句话。",
        lambda *_: ModelResponse(parts=[TextPart(next(replies))]),
        history=history,
    )
    assert result["answer"] == "这笔成交额是 199 元。"


def test_rewriting_keeps_equivalent_decimal_formats_without_requesting_a_retry():
    old = PriorTurn(
        page="店铺销售", question="预测11月GMV", answer="预计 1,350.00 万元，近3日日均下降 12.60%。"
    )
    reply = "预计月末 1350 万元，近三日日均下降 12.6%。"
    result = ask(
        "把刚才的预测总结成一句话。",
        lambda *_: ModelResponse(parts=[TextPart(reply)]),
        history=[old],
    )
    assert result["answer"] == reply and result["usage"]["requests"] == 1


def test_if_a_rewrite_twice_changes_the_figures_offer_a_source_sentence():
    old = PriorTurn(
        page="店铺销售",
        question="预测11月GMV",
        answer="**月末中心估计 1,350.00 万元**，仍需观察剩余几天。",
    )
    aside = PriorTurn(page="店铺销售", question="你是哪个模型？", answer="当前使用 DeepSeek。")
    result = ask(
        "把刚才的预测总结成一句话。",
        lambda *_: ModelResponse(parts=[TextPart("月末预计 1111 万元。")]),
        history=[old, aside],
    )
    assert result["status"] == "answered" and result["usage"]["requests"] == 2
    assert "1,350.00" in result["answer"] and "1111" not in result["answer"]


def test_a_missed_open_question_cannot_be_replaced_with_a_capability_list():
    from types import SimpleNamespace

    from app.modules.assistant.chat import discover_dashboard

    class Model:
        async def run(self, prompt, *, deps, **kw):
            await discover_dashboard(SimpleNamespace(deps=deps), "sales", explain_only=True)
            return SimpleNamespace(
                output=chat.GeneralAnswer(answer="试着从客户最常问的问题入手。"),
                usage=chat.RunUsage(requests=2),
            )

    q = "新人最容易忽略哪一步？"
    answer = asyncio.run(
        chat.answer_question(Model(), q, "店铺销售", [], chat.QueryRun(None, chat.ChatScope(), q))
    )
    assert "客户" in answer["answer"] and "当前支持" not in answer["answer"]


def test_actual_forecast_mentioned_in_a_rewrite_does_not_need_a_forecast_tool():
    old = PriorTurn(
        page="店铺销售",
        question="预测11月GMV",
        answer="月末中心预计 85.60 万元，库存影响仍待核实。",
    )
    reply = "按刚才的预测，本月预计 85.60 万元，仍需关注库存。"
    result = ask(
        "把刚才六店11月GMV预测改写成一句晨会用语。",
        lambda *_: ModelResponse(parts=[TextPart(reply)]),
        history=[old],
    )
    assert result["status"] == "answered" and result["answer"] == reply and not result["reports"]


def test_open_answer_cannot_present_invented_shop_results():
    q = "你心里那种让人逛着舒服的店长什么样？"

    def model(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "answer_general", {"answer": "本月六店实际成交额为 991182 万元，已经超过目标。"}
                )
            ]
        )

    result = ask(q, model)
    assert "991182" not in result["answer"] and result["status"] == "no_evidence"


def test_tool_discovery_does_not_replace_an_unrelated_reply_with_a_menu():
    from types import SimpleNamespace

    class Model:
        async def run(self, prompt, *, deps, **kwargs):
            await chat.discover_dashboard(SimpleNamespace(deps=deps), "sales", explain_only=True)
            return SimpleNamespace(output="无法确定。", usage=chat.RunUsage(requests=1))

    q = "说说你心目中留住顾客的诀窍。"
    result = asyncio.run(
        chat.answer_question(Model(), q, "推广分析", [], chat.QueryRun(None, chat.ChatScope(), q))
    )
    assert "当前支持" not in result["answer"]


def test_forecast_method_is_distinct_from_assistant_identity():
    from app.modules.assistant.conversation import plan_turn

    plan = plan_turn("刚才的11月GMV预测用的是哪种模型？", "预测11月GMV", has_forecast=True)
    assert plan.forecast == "knowledge" and plan.mode != "identity"


@pytest.mark.parametrize(
    "question",
    [
        "先说一般做法，再告诉我昨天六店实际GMV是多少",
        "先只讲排查思路，再看看我们店本周的销售表现。",
        "帮我写个简报，附上本月真实成交额",
        "我店最近退货变多是商品质量问题吗？",
        "照我们公司 SOP，售后多少元必须主管审批？",
        "这款商品最近卖得怎么样？",
        "把本月六店真实销量编成一首诗",
        "第一条文案带来的实际成交额是多少？",
        "哪条主图文案的成交额最高？",
    ],
)
def test_natural_language_cannot_establish_unverified_board_or_policy_facts(question):
    invented = "已经确认这家店退货由质量问题导致，金额为 991182 元。"

    def agent(messages, info):
        return ModelResponse(parts=[TextPart(invented)])

    result = ask(question, agent)
    assert result["status"] == "no_evidence" and "991182" not in result["answer"]
    assert "给我一项可以确认的商品卖点" not in result["answer"]


def test_explicit_rewrite_of_a_prior_reply_uses_the_text_without_a_new_query():
    old = PriorTurn(
        page="店铺销售", question="预测一下11月GMV", answer="月末是估计值，仍需关注剩余几天的销售。"
    )
    reply = "本月预测尚待剩余几天的实际销售验证。"
    result = ask(
        "把刚才的预测改写成一句话，发晨会用。",
        lambda *_: ModelResponse(parts=[TextPart(reply)]),
        history=[old],
        scope={"domain": "sales", "forecast": {"month": "2026-11"}},
    )
    assert result["status"] == "answered" and result["answer"] == reply
    assert result["usage"]["requests"] == 1 and not result["reports"]


def test_rewrite_can_refer_past_model_identity_to_the_verified_forecast():
    history = [
        PriorTurn(
            page="店铺销售", question="预测11月GMV", answer="六店月末中心预计 1,345.86 万元。"
        ),
        PriorTurn(page="店铺销售", question="你是哪个模型？", answer="当前使用 DeepSeek。"),
    ]
    reply = "六店月末成交额预计为 1,345.86 万元。"
    result = ask(
        "把刚才的预测总结成一句晨会用语。",
        lambda *_: ModelResponse(parts=[TextPart(reply)]),
        history=history,
    )
    assert result["status"] == "answered" and result["answer"] == reply and not result["reports"]
