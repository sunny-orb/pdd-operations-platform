import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

DEEPSEEK = "bailian:deepseek-v4.1-flash-1017"
URL = "/api/v1/assistant/chat"
BODY = {
    "generation": "b95fc3c0-85c4-4f5d-911c-d32a64dcc7ad",
    "question": "近七天表现如何？",
    "page": "商品数据",
}


def test_local_model_is_opt_in():
    app = create_app(Settings(_env_file=None, assistant_model=""))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        assert client.post(URL, json=BODY).status_code == 503


def test_assistant_is_inaccessible_outside_local_preview():
    app = create_app(Settings(_env_file=None, app_env="production", assistant_model=DEEPSEEK))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        assert client.post(URL, json=BODY).status_code == 403


def test_untrusted_browser_origin_cannot_use_model():
    app = create_app(Settings(_env_file=None, assistant_model=DEEPSEEK))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        assert (
            client.post(URL, headers={"Origin": "https://example.com"}, json=BODY).status_code
            == 403
        )


def test_business_data_never_reaches_a_provider_without_approval(monkeypatch):
    from app.api import assistant

    def unexpected_model(*args):
        raise AssertionError("未授权时不能创建模型或发送对话")

    monkeypatch.setattr(assistant, "build_agent", unexpected_model)
    for enabled, providers in [(False, ["bailian"]), (True, [])]:
        app = create_app(
            Settings(
                _env_file=None,
                assistant_model=DEEPSEEK,
                assistant_business_data_enabled=enabled,
                assistant_business_providers=providers,
            )
        )
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
            assert client.get("/api/v1/assistant/models").json()["models"] == []
            assert (
                client.post(URL, json={**BODY, "history": [{**BODY, "answer": "历史"}]}).status_code
                == 403
            )


def test_obsolete_model_settings_disable_only_the_assistant():
    app = create_app(
        Settings(
            _env_file=None, assistant_model="external:retired", assistant_business_data_enabled=True
        )
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        assert client.get("/api/v1/assistant/models").json()["models"] == []
        assert client.post(URL, json=BODY).status_code == 503


def test_stop_cancels_only_matching_generation_and_releases_slot(monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import AsyncExitStack
    from threading import Event

    from app.api import assistant

    started = Event()
    stopped = Event()

    async def generate(agent, question, page, history, deps):
        if question == "重试":
            return {
                "answer": "已重新回答",
                "status": "no_evidence",
                "evidence": "",
                "reports": [],
                "subject": None,
                "candidates": [],
            }
        started.set()
        try:
            await asyncio.sleep(5)
        finally:
            stopped.set()
        return "未取消"

    monkeypatch.setattr(assistant, "build_agent", lambda _: AsyncExitStack())
    monkeypatch.setattr(assistant, "answer_question", generate)
    app = create_app(
        Settings(
            _env_file=None,
            assistant_business_data_enabled=True,
            assistant_business_providers=["bailian"],
            assistant_model=DEEPSEEK,
        )
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.post, URL, json=BODY)
            assert started.wait(3)
            path = "/api/v1/assistant/stop"
            assert client.post(
                path, json={"generation": "00000000-0000-4000-8000-000000000001"}
            ).is_success
            assert not stopped.is_set()
            assert client.post(path, json={"generation": BODY["generation"]}).is_success
            assert first.result(timeout=3).status_code == 499
            assert stopped.is_set()
        assert client.post(URL, json={**BODY, "question": "重试"}).json()["answer"] == "已重新回答"


def test_deepseek_is_the_only_server_model_and_default(monkeypatch):
    from contextlib import asynccontextmanager

    from app.api import assistant

    monkeypatch.setenv("ASSISTANT_MODEL_CHOICES", '["bailian:retired-model"]')
    app = create_app(
        Settings(
            _env_file=None,
            assistant_business_data_enabled=True,
            assistant_business_providers=["bailian"],
        )
    )
    opened = []

    @asynccontextmanager
    async def build(settings):
        opened.append(settings.assistant_model)
        yield settings.assistant_model

    async def echo(agent, question, page, history, deps):
        return {
            "answer": agent,
            "status": "no_evidence",
            "evidence": "",
            "reports": [],
            "subject": None,
            "candidates": [],
        }

    monkeypatch.setattr(assistant, "build_agent", build)
    monkeypatch.setattr(assistant, "answer_question", echo)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        assert client.get("/api/v1/assistant/models").json() == {
            "models": [DEEPSEEK],
            "business_data_enabled": True,
        }
        for removed in (
            "bailian:qwen3.8-flash",
            "unlisted",
            "bailian:retired-model",
            "external:retired",
        ):
            assert client.post(URL, json={**BODY, "model": removed}).status_code == 422
        for name in (None, DEEPSEEK):
            assert client.post(URL, json={**BODY, "model": name}).json()["answer"] == DEEPSEEK
        assert opened == [DEEPSEEK]


@pytest.mark.parametrize("official,thinking", [(False, False), (True, False), (True, True)])
def test_readonly_tools_pin_identity_and_publish_server_evidence(monkeypatch, official, thinking):
    import json
    from functools import partial
    from threading import get_ident

    import httpx2
    from pydantic_ai.providers import deepseek, openai

    from app.modules.assistant import chat

    product = {
        "listing_id": "link-1",
        "product_id": "sku-08",
        "name": "青色帽子",
        "shop_id": "s1",
        "shop_name": "甲店",
    }
    other = {**product, "listing_id": "link-2", "shop_id": "s2", "shop_name": "乙店"}
    report = {
        "kind": "product_performance",
        "product": product,
        "data_version": "local-42",
        "versions": {"product_version": "local-42", "promotion_version": "promo-2"},
        "period": {
            "date_from": "2026-10-01",
            "date_to": "2026-10-07",
            "previous_from": "2026-09-24",
            "previous_to": "2026-09-30",
        },
        "coverage": {
            "current": {"observed_days": 7, "expected_days": 7, "missing_dates": []},
            "previous": {"observed_days": 6, "expected_days": 7, "missing_dates": ["2026-09-29"]},
        },
        "metrics": [
            {
                "key": "amount",
                "label": "成交额",
                "unit": "currency",
                "current": "40.00",
                "previous": "20.00",
                "change": None,
                "status": "partial",
                "reason": "前期缺日",
            }
        ],
        "evidence": {"source": "商品数据 · 商品日经营", "rules": []},
        "series": [],
    }
    mode = "lookup"
    model_thread_ids = []
    lookups = []
    reads = []
    calls = []

    def search(engine, keyword, *, shop_id=None):
        lookups.append((keyword, shop_id, get_ident()))
        if keyword == "帽子":
            return [product, other] if mode in {"multiple", "confirmed"} else [product]
        return [product] if keyword == "link-1" else []

    def performance(engine, listing_id, *, date_from=None, date_to=None, recent_days=7):
        reads.append((listing_id, str(date_from), str(date_to), get_ident()))
        return report

    def endpoint(request):
        payload = json.loads(request.content)
        assert payload["model"] == (
            "deepseek-flash" if official else DEEPSEEK.removeprefix("bailian:")
        )
        if official:
            assert payload["thinking"] == {"type": "enabled" if thinking else "disabled"}
            assert "enable_thinking" not in payload
            if thinking:
                assert payload["reasoning_effort"] == "high"
                assert "temperature" not in payload
        else:
            assert payload["enable_thinking"] is False
        assert request.url.host == ("deepseek.test" if official else "bailian.test")
        assert request.headers["Authorization"] == "Bearer test-only"
        model_thread_ids.append(get_ident())
        messages = payload["messages"]
        if thinking:
            for message in messages:
                if previous_tools := message.get("tool_calls"):
                    expected = f"internal-test-{previous_tools[0]['id']}"
                    assert message.get("reasoning_content") == expected
        calls.append(messages)
        names = {t["function"]["name"] for t in payload["tools"]}
        assert ("explain_observation" in names) == (
            not any(m.get("tool_call_id") == "facts" for m in messages)
        )
        assert names - {"explain_observation", "answer_general", "describe_assistant"} == {
            "find_product",
            "get_product_performance",
            "get_product_promotion",
            "discover_dashboard",
            "get_dashboard_data",
        }
        assert "DEMO-01" not in str(messages)
        if mode == "no_tool":
            content = {"content": "没有查数也说成交额555"}
        elif mode in {"lookup", "multiple", "confirmed"} and messages[-1]["role"] == "user":
            content = {
                "tool_calls": [
                    {
                        "id": "look",
                        "type": "function",
                        "function": {"name": "find_product", "arguments": '{"keyword":"帽子"}'},
                    }
                ]
            }
        elif mode != "multiple" and not any(m.get("tool_call_id") == "facts" for m in messages):
            content = {
                "tool_calls": [
                    {
                        "id": "facts",
                        "type": "function",
                        "function": {"name": "get_product_performance", "arguments": "{}"},
                    }
                ]
            }
        else:
            if mode != "multiple":
                assert "40.00" in str(messages[-1]["content"])
                assert "local-42" not in str(messages[-1]["content"])
            content = {"content": "本期成交额40.00元。"}
        if thinking:
            tag = content["tool_calls"][0]["id"] if content.get("tool_calls") else "answer"
            content["reasoning_content"] = f"internal-test-{tag}"
        return httpx2.Response(
            200,
            json={
                "id": "chat-local",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if "tool_calls" in content else "stop",
                        "message": {"role": "assistant", **content},
                    }
                ],
            },
        )

    client_transport = httpx2.AsyncClient(transport=httpx2.MockTransport(endpoint))
    monkeypatch.setattr(
        openai,
        "OpenAIProvider",
        partial(openai.OpenAIProvider, http_client=client_transport),
    )
    monkeypatch.setattr(
        deepseek,
        "DeepSeekProvider",
        partial(deepseek.DeepSeekProvider, http_client=client_transport),
    )
    monkeypatch.setattr(chat, "search_products", search)
    monkeypatch.setattr(chat, "product_performance", performance)
    app = create_app(
        Settings(
            _env_file=None,
            assistant_model="deepseek:deepseek-flash" if official else DEEPSEEK,
            deepseek_api_key="test-only",
            deepseek_base_url="https://deepseek.test",
            deepseek_thinking_enabled=thinking,
            bailian_api_key="test-only",
            bailian_base_url="https://bailian.test/compatible-mode/v1",
            assistant_business_data_enabled=True,
            assistant_business_providers=["deepseek" if official else "bailian"],
        )
    )
    scope = {"shop_id": "s1", "date_from": "2026-10-01", "date_to": "2026-10-07"}
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:

        def ask(question, context):
            return client.post(
                URL,
                json={
                    **BODY,
                    "question": question,
                    "context": context,
                    "history": [{**BODY, "answer": "上一轮回答"}],
                },
            )

        answer = ask("查帽子表现", scope)
        assert answer.is_success, answer.text
        result = answer.json()
        assert result["status"] == "answered" and result["reports"] == [report]
        assert result["subject"] == product and result["candidates"] == []
        assert "local-42" in result["evidence"] and "2026-10-07" in result["evidence"]
        assert result["answer"] == "本期成交额40.00元。"
        assert "internal-test-" not in answer.text
        assert reads[0][:3] == ("link-1", "2026-10-01", "2026-10-07")
        assert lookups[0][:2] == ("帽子", "s1")
        assert any(m.get("content") == "上一轮回答" for m in calls[0])
        assert reads[0][3] not in model_thread_ids and lookups[0][2] not in model_thread_ids

        mode = "pinned"
        result = ask("本期如何？", {**scope, "listing_id": "link-1"}).json()
        assert result["reports"] == [report] and lookups[-1][:2] == ("link-1", "s1")

        count = len(calls)
        wrong_version = ask(
            "本期如何？", {**scope, "listing_id": "link-1", "product_version": "old"}
        )
        assert wrong_version.status_code == 409
        assert wrong_version.json()["error"]["code"] == "DATA_VERSION_CHANGED"
        assert all("40.00" not in str(m) for m in calls[count:])

        mode = "multiple"
        result = ask("换个商品，查帽子表现", {**scope, "listing_id": "link-1"}).json()
        assert lookups[-1][:2] == ("帽子", None)
        assert result["status"] == "need_selection" and result["candidates"] == [product, other]
        assert result["subject"] is None and result["reports"] == []
        assert not any("link-2" in str(messages) for messages in calls)

        result = ask(
            "换个商品，查帽子表现", {**scope, "listing_id": "link-1", "filter_shop_id": "s1"}
        )
        assert result.is_success
        assert lookups[-1][:2] == ("帽子", "s1")

        mode = "confirmed"
        confirmed = ask(
            "查帽子表现", {**scope, "listing_id": "link-1", "candidate_confirmed": True}
        )
        assert confirmed.is_success, confirmed.text
        assert confirmed.json()["reports"] == [report]
        assert lookups[-1][:2] == ("link-1", "s1")

        mode = "pinned"
        wrong_pair = ask(
            "本期如何？", {**scope, "listing_id": "link-1", "promotion_version": "promo-old"}
        )
        assert wrong_pair.status_code == 409

        mode = "no_tool"
        result = ask("本期如何？", {}).json()
        assert result["status"] == "no_evidence" and "555" not in result["answer"]


def test_rule_followup_keeps_observations_with_their_dates_and_skips_period_totals(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.modules.assistant import chat

    product = dict(listing_id="cap", shop_id="shop", name="帽子", shop_name="测试店")
    rule = dict(label="严重流量下滑", current="60", baseline="120", trend=["80", "60"])
    source = dict(
        kind="product_performance",
        product=product,
        period=dict(date_from="2026-11-18", date_to="2026-11-24"),
        metrics=[{"key": "visitors", "current": "840"}],
        coverage={},
        evidence={
            "rules": {
                "date_to": "2026-11-24",
                "baseline_from": "2026-11-17",
                "baseline_to": "2026-11-23",
                "findings": [rule],
            }
        },
        series=[{"date": "2026-11-24", "values": {"visitors": "60"}}],
    )
    monkeypatch.setattr(chat, "product_performance", lambda *a, **kw: source)
    run = chat.QueryRun(None, chat.ChatScope(), "这款触发那天的访客和基准是多少？", subject=product)
    result = asyncio.run(chat.get_product_performance(SimpleNamespace(deps=run), focus="risk"))
    assert result["rules"]["findings"] == [{k: v for k, v in rule.items() if k != "trend"}]
    assert result["rules"]["baseline_to"] == "2026-11-23" and "metrics" not in result
    assert "trend" in run.reports[0]["evidence"]["rules"]["findings"][0]
    full = asyncio.run(
        chat.get_product_performance(SimpleNamespace(deps=run), daily_metrics=["visitors"])
    )
    assert full["metrics"] == source["metrics"] and full["series"] == source["series"]


def test_daily_evidence_reuses_report_and_only_sends_requested_values(monkeypatch):
    import asyncio
    from datetime import date

    from app.modules.assistant import chat

    selected = {"listing_id": "link-1", "shop_id": "s1", "name": "帽子", "shop_name": "甲店"}
    dates = []

    def promotion(engine, listing_id, **kwargs):
        dates.append(kwargs)
        return {
            "kind": "product_promotion",
            "data_version": "promo-1",
            "versions": {"product_version": "local-42", "promotion_version": "promo-1"},
            "product": selected,
            "period": {
                "date_from": "2026-10-01",
                "date_to": "2026-10-14",
                "previous_from": "2026-09-17",
                "previous_to": "2026-09-30",
            },
            "metrics": [],
            "coverage": {},
            "evidence": {"source": "商品推广", "rules": {}},
            "series": [{"date": "2026-10-01", "values": {"spend": "17.20", "clicks": "25"}}],
        }

    monkeypatch.setattr(chat, "product_promotion", promotion)
    scope = chat.ChatScope(date_from=date(2026, 9, 1), date_to=date(2026, 9, 10))
    run = chat.QueryRun(None, scope, "近两周哪一天花费最高？", subject=selected)

    async def read():
        summary = await run.read("product_promotion", "default", None, None, [])
        daily = await run.read("product_promotion", "default", None, None, ["spend"])
        return summary, daily

    summary, daily = asyncio.run(read())
    assert len(dates) == 1 and dates[0]["recent_days"] == 14 and dates[0]["date_from"] is None
    assert "series" not in summary
    assert daily["series"] == [{"date": "2026-10-01", "values": {"spend": "17.20"}}]
    assert len(run.reports) == 1 and "clicks" in run.reports[0]["series"][0]["values"]


def test_unavailable_causal_domains_have_explicit_answer(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.modules.assistant.chat import ChatScope, QueryRun, answer_question

    class NoTool:
        async def run(self, *args, **kwargs):
            return SimpleNamespace(
                output="是的，这款确定缺货",
                usage=SimpleNamespace(input_tokens=0, output_tokens=0, requests=1, tool_calls=0),
            )

    for word in ("库存", "退货"):
        question = f"{word}导致销量下滑？"
        response = asyncio.run(
            answer_question(
                NoTool(), question, "商品数据", [], QueryRun(None, ChatScope(), question)
            )
        )
        assert response["status"] == "no_evidence"
        assert word in response["answer"] and "确定缺货" not in response["answer"]


def test_new_generation_waits_for_inflight_read_after_stop(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import AsyncExitStack
    from threading import Event
    from uuid import uuid4

    from app.api import assistant
    from app.modules.assistant import chat

    first = Event()
    release = Event()
    second = Event()

    def prolonged_search(engine, keyword, *, shop_id):
        if keyword == "first-link":
            first.set()
            assert release.wait(4)
        else:
            second.set()
        return []

    async def resolve(agent, question, page, history, deps):
        try:
            await deps.resolve_pinned()
        except chat.Clarify:
            pass
        return deps.response("请重新选择商品", "no_evidence")

    monkeypatch.setattr(chat, "search_products", prolonged_search)
    monkeypatch.setattr(assistant, "build_agent", lambda _: AsyncExitStack())
    monkeypatch.setattr(assistant, "answer_question", resolve)
    app = create_app(
        Settings(
            _env_file=None,
            assistant_model=DEEPSEEK,
            assistant_business_data_enabled=True,
            assistant_business_providers=["bailian"],
        )
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        with ThreadPoolExecutor(2) as pool:
            previous = pool.submit(
                client.post, URL, json={**BODY, "context": {"listing_id": "first-link"}}
            )
            assert first.wait(3)
            assert client.post(
                "/api/v1/assistant/stop", json={"generation": BODY["generation"]}
            ).is_success
            assert previous.result(timeout=2).status_code == 499
            following = pool.submit(
                client.post,
                URL,
                json={**BODY, "generation": str(uuid4()), "context": {"listing_id": "second-link"}},
            )
            try:
                assert not second.wait(0.15)
            finally:
                release.set()
            assert following.result(timeout=2).is_success and second.is_set()


def test_anchored_rolling_window_and_mixed_date_requests():
    from datetime import date

    import pytest

    from app.modules.assistant.chat import ChatScope, Clarify, QueryRun

    scope = ChatScope(date_from=date(2026, 11, 1), date_to=date(2026, 11, 10))
    run = QueryRun(None, scope, "截至 2026-10-20 的近 7 天成交如何")
    assert run.dates("default", None, None) == (date(2026, 10, 14), date(2026, 10, 20), 7)
    for text in (
        "昨天和最近 7 天比",
        "近7天和过去14天",
        "从昨天到今天",
        "2026-10-20 和近7天相比",
        "截至10月20日的近7天",
        "上个月的近7天",
    ):
        run = QueryRun(None, scope, text)
        with pytest.raises(Clarify):
            run.dates("default", None, None)


def test_calendar_month_ranges_come_from_the_question_not_the_model():
    from datetime import date

    from app.modules.assistant.chat import ChatScope, Clarify, QueryRun

    for question, start, end in [
        ("算下2026年11月到27日合计成交额", date(2026, 11, 1), date(2026, 11, 27)),
        ("看2024年2月8号至29号的店铺销售", date(2024, 2, 8), date(2024, 2, 29)),
    ]:
        run = QueryRun(None, ChatScope(), question)
        assert run.dates("default", date(2020, 5, 5), date(2020, 5, 12))[:2] == (start, end)
    for question in [
        "2026年2月到29日成交额",
        "2026年11月9日到8日成交额",
        "2026年11月到15日和上月怎么比",
    ]:
        with pytest.raises(Clarify):
            QueryRun(None, ChatScope(), question).dates("default", None, None)


def test_stream_runs_tools_before_text_and_keeps_final_evidence(monkeypatch):
    import json
    from contextlib import asynccontextmanager

    from pydantic_ai import Agent
    from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

    from app.api import assistant
    from app.modules.assistant import chat

    product = {"listing_id": "link", "shop_id": "s", "name": "测试商品", "shop_name": "店"}
    report = {
        "kind": "product_performance",
        "product": product,
        "period": {
            "date_from": "2026-11-17",
            "date_to": "2026-11-23",
            "previous_from": "2026-11-10",
            "previous_to": "2026-11-16",
        },
        "metrics": [],
        "coverage": {},
        "series": [],
        "data_version": "pv",
        "versions": {"product_version": "pv", "promotion_version": "av"},
        "evidence": {"source": "商品经营", "rules": []},
    }

    async def stream(messages, info):
        if messages[-1].kind == "request" and any(
            getattr(part, "part_kind", None) == "tool-return" for part in messages[-1].parts
        ):
            yield {0: DeltaThinkingPart(content="internal-stream-test")}
            yield "访客"
            yield "减少，先看推广。"
        else:
            yield {0: DeltaToolCall(name="get_product_performance", json_args="{}")}

    @asynccontextmanager
    async def build(_):
        yield Agent(
            FunctionModel(stream_function=stream),
            deps_type=chat.QueryRun,
            tools=[chat.get_product_performance],
        )

    monkeypatch.setattr(assistant, "build_agent", build)
    monkeypatch.setattr(chat, "search_products", lambda *a, **kw: [product])
    monkeypatch.setattr(chat, "product_performance", lambda *a, **kw: report)
    app = create_app(
        Settings(
            _env_file=None,
            assistant_business_data_enabled=True,
            assistant_business_providers=["bailian"],
            assistant_model=DEEPSEEK,
        )
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        response = client.post(
            URL,
            headers={"Accept": "text/event-stream"},
            json={**BODY, "context": {"listing_id": "link"}},
        )
        assert response.is_success
        assert "internal-stream-test" not in response.text
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        deltas = [event["text"] for event in events if event["type"] == "delta"]
        assert "".join(deltas) == "访客减少，先看推广。"
        assert events[-1]["type"] == "done"
        assert events[-1]["result"]["reports"] == [report]
        assert events[-1]["result"]["answer"] == "".join(deltas)
        before = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "status" and e["text"] == "正在查询商品经营…"
        )
        assert before < next(i for i, e in enumerate(events) if e["type"] == "delta")

        denied = client.post(
            URL, headers={"Accept": "text/event-stream"}, json={**BODY, "model": "unapproved"}
        )
        assert '"type": "error"' in denied.text
        assert '"type": "done"' not in denied.text
        assert not app.state.assistant_lock.locked()


def test_bailian_requires_its_own_key(monkeypatch):
    from app.modules.assistant.chat import build_agent

    monkeypatch.setenv("OPENAI_API_KEY", "other-provider-key")
    with pytest.raises(ValueError, match="百炼 API Key"):
        build_agent(Settings(_env_file=None, assistant_model=DEEPSEEK))


def test_production_assistant_requires_enablement_and_approved_same_origin():
    settings = Settings(
        _env_file=None,
        app_env="production",
        assistant_production_enabled=True,
        assistant_business_data_enabled=True,
        assistant_business_providers=["bailian"],
        cors_origins=["https://pdd-operations.kktree.cn"],
    )
    with TestClient(create_app(settings), base_url="https://pdd-operations.kktree.cn") as client:
        assert client.get("/api/v1/assistant/models").json()["models"] == [DEEPSEEK]
        denied = client.get("/api/v1/assistant/models", headers={"Origin": "https://other.example"})
        assert denied.status_code == 403
    with TestClient(create_app(settings), base_url="https://other.example") as client:
        assert client.get("/api/v1/assistant/models").status_code == 403


def test_production_model_requests_are_rate_limited_before_calling_model(monkeypatch):
    from app.api import assistant

    monkeypatch.setattr(assistant, "build_agent", lambda _: (_ for _ in ()).throw(RuntimeError()))
    settings = Settings(
        _env_file=None,
        app_env="production",
        assistant_production_enabled=True,
        assistant_business_data_enabled=True,
        assistant_business_providers=["bailian"],
        assistant_requests_per_minute=1,
        cors_origins=["https://pdd-operations.kktree.cn"],
    )
    with TestClient(create_app(settings), base_url="https://pdd-operations.kktree.cn") as client:
        assert client.post(URL, json=BODY).status_code == 502
        assert client.post(URL, json=BODY).status_code == 429


def test_two_users_can_generate_and_stop_only_their_own_generation(monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import AsyncExitStack
    from threading import Event

    from app.api import assistant

    started = {name: Event() for name in ("one", "two")}
    finish = Event()

    async def generate(agent, question, page, history, deps):
        started[question].set()
        while not finish.is_set():
            await asyncio.sleep(0.01)
        return {
            "answer": question,
            "status": "answered",
            "evidence": "",
            "reports": [],
            "subject": None,
            "candidates": [],
        }

    monkeypatch.setattr(assistant, "build_agent", lambda _: AsyncExitStack())
    monkeypatch.setattr(assistant, "answer_question", generate)
    settings = Settings(
        _env_file=None,
        assistant_max_concurrent=2,
        assistant_business_data_enabled=True,
        assistant_business_providers=["bailian"],
    )
    second = "36b63ec7-14c0-424c-b3bc-9cd9b57c2490"
    with TestClient(
        create_app(settings), base_url="http://localhost", client=("127.0.0.1", 5000)
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(client.post, URL, json={**BODY, "question": "one"})
            two = pool.submit(
                client.post, URL, json={**BODY, "generation": second, "question": "two"}
            )
            try:
                assert started["one"].wait(3)
                assert started["two"].wait(3)
                client.post("/api/v1/assistant/stop", json={"generation": BODY["generation"]})
                assert one.result(timeout=3).status_code == 499
                assert not two.done()
            finally:
                finish.set()
            assert two.result(timeout=3).json()["answer"] == "two"


def test_http_assistant_uses_the_page_report_input_cache(monkeypatch):
    from contextlib import AsyncExitStack

    from app.api import assistant

    app = create_app(
        Settings(
            _env_file=None,
            assistant_business_data_enabled=True,
            assistant_business_providers=["bailian"],
        )
    )

    async def generate(agent, question, page, history, deps):
        assert deps.aftersales_rows_cache is app.state.aftersales_overview_cache.rows
        return {
            "answer": "已共享输入",
            "status": "answered",
            "evidence": "",
            "reports": [],
            "subject": None,
            "candidates": [],
        }

    monkeypatch.setattr(assistant, "build_agent", lambda _: AsyncExitStack())
    monkeypatch.setattr(assistant, "answer_question", generate)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
        assert client.post(URL, json=BODY).json()["answer"] == "已共享输入"


def test_direct_deepseek_is_enabled_only_with_matching_business_approval():
    model = "deepseek:deepseek-flash"
    for providers, expected in [(["deepseek"], [model]), (["bailian"], [])]:
        app = create_app(
            Settings(
                _env_file=None,
                assistant_model=model,
                assistant_business_data_enabled=True,
                assistant_business_providers=providers,
            )
        )
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as client:
            assert client.get("/api/v1/assistant/models").json()["models"] == expected
