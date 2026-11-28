"""通过锁定版本的 SDK 检查实际 HTTP 请求；端点只回合成规则的审阅结果。"""

import json
import traceback

import httpx2
import pytest

from app.core.config import DIRECT_DEEPSEEK_MODEL
from app.modules.knowledge.forecast_rules import ForecastRuleError
from tests.test_knowledge_forecast_rules import FORMULA, SOP

pytest_plugins = ["tests.sales_pg_support"]


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    from app.modules.knowledge import forecast_model

    values = {
        "APP_ENV": "test",
        "ASSISTANT_MODEL": DIRECT_DEEPSEEK_MODEL,
        "ASSISTANT_BUSINESS_DATA_ENABLED": "true",
        "ASSISTANT_BUSINESS_PROVIDERS": '["deepseek"]',
        "DEEPSEEK_API_KEY": "test-secret-only",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        "DEEPSEEK_THINKING_ENABLED": "true",
    }
    # 定时器在仓库根目录启动，API 配置位于子目录。只在临时路径读取合成配置。
    api = tmp_path / "apps" / "api"
    api.mkdir(parents=True)
    env_file = api / ".env"
    env_file.write_text("\n".join(f"{key}='{value}'" for key, value in values.items()))
    for key in values:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(forecast_model, "_API_ENV", env_file)


def endpoint(monkeypatch, respond):
    requests = []

    async def handle(self, request):
        requests.append(request)
        return respond(json.loads(request.content))

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle)
    return requests


def reply(payload, content, *, finish="stop"):
    return httpx2.Response(
        200,
        json={
            "id": "sop-test",
            "object": "chat.completion",
            "created": 0,
            "model": payload["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


def quoted_review(payload):
    (user,) = (m["content"] for m in payload["messages"] if m["role"] == "user")
    (clause,) = (c for c in json.loads(user)["clauses"] if c["quote"] == FORMULA)
    source_id = clause["id"]
    return {
        "center_clauses": [source_id],
        "weights": [
            {"clause": source_id, "days": 2, "weight": "0.75"},
            {"clause": source_id, "days": 4, "weight": "0.25"},
        ],
        "issues": [],
    }


@pytest.mark.parametrize(
    ("base_url", "path"),
    [
        ("https://api.deepseek.com/v1", "/v1/chat/completions"),
        ("https://api.deepseek.com:443", "/chat/completions"),
    ],
)
def test_official_json_request_compiles_only_sourced_rules(monkeypatch, base_url, path):
    from app.modules.knowledge.forecast_model import compile_with_deepseek

    monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)
    requests = endpoint(monkeypatch, lambda p: reply(p, json.dumps(quoted_review(p))))
    rule = compile_with_deepseek(SOP + "利润额为 987654 元。")

    assert rule["terms"] == [{"days": 2, "weight": "0.75"}, {"days": 4, "weight": "0.25"}]
    assert rule["interpretation"] == {
        "schema": 1,
        "method": "deepseek",
        "model": DIRECT_DEEPSEEK_MODEL,
    }
    (request,) = requests
    assert request.url.host == "api.deepseek.com" and request.url.path == path
    body = json.loads(request.content)
    assert body["model"] == "deepseek-flash" and body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"} and body["temperature"] == 0
    assert body.get("reasoning_effort") in (None, "none") and "tools" not in body
    assert 0 < body["max_tokens"] <= 4096
    assert "987654" not in str(body) and "test-secret-only" not in str(body)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ASSISTANT_BUSINESS_DATA_ENABLED", "false"),
        ("ASSISTANT_BUSINESS_PROVIDERS", '["bailian"]'),
        ("ASSISTANT_MODEL", "bailian:deepseek-v4.1-flash-1017"),
        ("DEEPSEEK_API_KEY", ""),
        ("DEEPSEEK_BASE_URL", "https://third-party.example/v1"),
    ],
)
def test_missing_official_model_or_approval_sends_no_request(monkeypatch, name, value):
    from app.modules.knowledge.forecast_model import compile_with_deepseek

    monkeypatch.setenv(name, value)
    requests = endpoint(monkeypatch, lambda _: pytest.fail("配置不允许发送正式条款"))
    with pytest.raises(RuntimeError):
        compile_with_deepseek(SOP)
    assert not requests


@pytest.mark.parametrize("failure", ["busy", "invalid_json", "wrong_citation", "truncated"])
def test_technical_failures_are_retryable_without_exposing_the_response(monkeypatch, failure):
    from app.modules.knowledge.forecast_model import compile_with_deepseek

    def respond(payload):
        if failure == "busy":
            return httpx2.Response(429, json={"error": {"message": "private-provider-response"}})
        if failure == "invalid_json":
            return reply(payload, "private-provider-response")
        review = quoted_review(payload)
        if failure == "wrong_citation":
            review["weights"][0]["clause"] = 988
        return reply(
            payload, json.dumps(review), finish="length" if failure == "truncated" else "stop"
        )

    requests = endpoint(monkeypatch, respond)
    with pytest.raises(RuntimeError) as error:
        compile_with_deepseek(SOP)
    assert len(requests) == 1
    diagnostic = "".join(traceback.format_exception(error.value))
    assert "private-provider-response" not in diagnostic
    assert "test-secret-only" not in diagnostic


def test_two_conflicting_source_formulas_are_a_business_rejection(monkeypatch):
    from app.modules.knowledge.forecast_model import compile_with_deepseek

    changed = FORMULA.replace("75%", "60%").replace("25%", "40%")

    def respond(payload):
        review = quoted_review(payload)
        (user,) = (m["content"] for m in payload["messages"] if m["role"] == "user")
        (conflict,) = (c["id"] for c in json.loads(user)["clauses"] if c["quote"] == changed)
        review["center_clauses"].append(conflict)
        review["weights"].extend(
            [
                {"clause": conflict, "days": 2, "weight": "0.6"},
                {"clause": conflict, "days": 4, "weight": "0.4"},
            ]
        )
        review["issues"] = [{"kind": "conflict", "clauses": review["center_clauses"]}]
        return reply(payload, json.dumps(review))

    requests = endpoint(monkeypatch, respond)
    with pytest.raises(ForecastRuleError):
        compile_with_deepseek(SOP.replace(FORMULA, f"{FORMULA}。{changed}"))
    assert len(requests) == 1


@pytest.mark.parametrize("outcome", ["accepted", "missing_usage", "busy", "configuration"])
def test_compiler_usage_counts_only_actual_http_and_never_invents_tokens(monkeypatch, outcome):
    from app.modules.knowledge.forecast_model import compile_with_deepseek

    def respond(payload):
        if outcome == "busy":
            return httpx2.Response(429, json={"error": {"message": "unavailable"}})
        response = reply(payload, json.dumps(quoted_review(payload)))
        body = response.json()
        if outcome == "accepted":
            body["usage"] = {"prompt_tokens": 123, "completion_tokens": 18, "total_tokens": 141}
        return httpx2.Response(200, json=body)

    sent = endpoint(monkeypatch, respond)
    usage = {}
    if outcome == "configuration":
        monkeypatch.setenv("ASSISTANT_BUSINESS_DATA_ENABLED", "false")
    if outcome in ("configuration", "busy"):
        with pytest.raises(RuntimeError):
            compile_with_deepseek(SOP, usage=usage)
    else:
        compile_with_deepseek(SOP, usage=usage)
    assert usage["requests"] == len(sent) == int(outcome != "configuration")
    expected = {"accepted": (123, 18), "configuration": (0, 0)}
    assert (usage["input_tokens"], usage["output_tokens"]) == expected.get(outcome, (None, None))


def test_verified_usage_survives_the_writer_receipt_and_is_zero_for_repeat_questions(
    sales_pg, monkeypatch
):
    from app.jobs.forecast_sop import run_check
    from app.modules.knowledge.forecast_access import ensure_forecast_current
    from tests.test_forecast_sop_store import Source

    def respond(payload):
        body = reply(payload, json.dumps(quoted_review(payload))).json()
        body["usage"] = {"prompt_tokens": 230, "completion_tokens": 61, "total_tokens": 291}
        return httpx2.Response(200, json=body)

    sent = endpoint(monkeypatch, respond)
    reader = Source(content=SOP)

    def check():
        return run_check(sales_pg, reader)

    first = ensure_forecast_current(sales_pg, refresh=check)
    second = ensure_forecast_current(sales_pg, refresh=check)
    assert first["review_usage"] == {"requests": 1, "input_tokens": 230, "output_tokens": 61}
    assert second["review_usage"] == {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    assert len(sent) == 1
    assert first["rule"] == second["rule"] and "review_usage" not in first["rule"]
