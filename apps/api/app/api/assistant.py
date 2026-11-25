"""右侧运营问答：限定站点、显式启用、全局速率与并发边界。"""

import asyncio
import json
import logging
import time
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from pydantic_ai.exceptions import UsageLimitExceeded

from app.core.config import Settings
from app.modules.assistant.chat import ChatScope, Product, QueryRun, answer_question, build_agent
from app.modules.product_board.repository import BoardError

router = APIRouter(prefix="/api/v1/assistant")
logger = logging.getLogger("uvicorn.error.assistant")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
Page = Literal["运营看板中心", "店铺销售", "商品数据", "推广分析", "售后分析"]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class PriorTurn(BaseModel):
    page: Page
    question: Question
    answer: str = Field(min_length=1, max_length=8000)
    status: Literal["answered", "no_evidence", "need_selection"] = "answered"


class Generation(BaseModel):
    generation: UUID


class ChatInput(Generation):
    model: str | None = Field(default=None, min_length=1, max_length=160)
    page: Page
    question: Question
    context: ChatScope = Field(default_factory=ChatScope)
    history: list[PriorTurn] = Field(default_factory=list, max_length=6)


class ChatAnswer(BaseModel):
    model: str
    answer: str
    evidence: str
    status: Literal["answered", "need_selection", "no_evidence"]
    subject: Product | None
    subject_cleared: bool = False
    candidates: list[Product]
    reports: list[dict]
    query_context: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)


def unavailable(status: int, message: str, code: str | None = None) -> JSONResponse:
    error = {"message": message}
    if code:
        error["code"] = code
    return JSONResponse({"error": error}, status_code=status)


def allowed_site(request: Request) -> bool:
    settings = request.app.state.assistant_settings
    origin = request.headers.get("origin")
    if settings.app_env == "production":
        sites = [urlsplit(site) for site in settings.cors_origins]
        return (
            settings.assistant_production_enabled
            and request.url.hostname in {site.hostname for site in sites if site.scheme == "https"}
            and (origin is None or origin in settings.cors_origins)
            and request.headers.get("sec-fetch-site") not in ("cross-site", "same-site")
        )
    return not (
        settings.app_env != "development"
        or request.url.hostname not in LOCAL_HOSTS
        or not request.client
        or request.client.host not in LOCAL_HOSTS
        or (
            origin
            and (
                origin not in settings.cors_origins or urlsplit(origin).hostname not in LOCAL_HOSTS
            )
        )
    )


def approved_models(settings: Settings) -> list[str]:
    if (
        not settings.assistant_business_data_enabled
        or "bailian" not in settings.assistant_business_providers
    ):
        return []
    return settings.assistant_models


@router.get("/models")
async def models(request: Request):
    if not allowed_site(request):
        return unavailable(403, "此站点尚未启用问答服务")
    settings = request.app.state.assistant_settings
    return {
        "models": approved_models(settings),
        "business_data_enabled": settings.assistant_business_data_enabled,
    }


@router.post("/stop")
async def stop(request: Request, body: Generation):
    if not allowed_site(request):
        return unavailable(403, "此站点尚未启用问答服务")
    active = request.app.state.assistant_active
    task = active.get(body.generation)
    if task is not None:
        task.cancel()
    return {"stopped": True}


@router.post("/chat", response_model=ChatAnswer)
async def chat(request: Request, body: ChatInput):
    if "text/event-stream" not in request.headers.get("accept", ""):
        return await chat_response(request, body)
    # 本机访问边界在发送响应头前验证；流中错误通过 error 事件结束。
    if not allowed_site(request):
        return unavailable(403, "此站点尚未启用问答服务")

    async def events():
        queue = asyncio.Queue()
        task = asyncio.create_task(chat_response(request, body, queue.put_nowait))
        task.add_done_callback(lambda _: queue.put_nowait(None))
        try:
            yield 'data: {"type":"status","text":"正在理解问题…"}\n\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            result = task.result()
            if isinstance(result, JSONResponse):
                payload = json.loads(result.body)
                event = {"type": "error", **payload}
            else:
                event = {"type": "done", "result": result.model_dump(mode="json")}
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            logger.warning("问答流中断 type=%s", type(exc).__name__)
            yield 'data: {"type":"error","error":{"message":"回答中断，请重试"}}\n\n'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def chat_response(request: Request, body: ChatInput, emit=None):
    if not allowed_site(request):
        return unavailable(403, "此站点尚未启用问答服务")
    state = request.app.state
    settings = state.assistant_settings
    if not settings.assistant_models:
        return unavailable(503, "本机尚未配置问答模型")
    name = settings.assistant_model
    if body.model is not None and body.model != name:
        return unavailable(422, "此模型未启用。请省略 model，使用看板默认的 DeepSeek。")
    if name not in approved_models(settings):
        return unavailable(403, "本机尚未授权此模型接收经营问题和查询摘要")
    if body.generation in state.assistant_active:
        return unavailable(409, "此问题正在回答，请等待或停止后重试")
    if settings.app_env == "production":
        now = time.monotonic()
        recent = state.assistant_requests
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= settings.assistant_requests_per_minute:
            return unavailable(429, "提问较多，请稍候再试")
        recent.append(now)
    try:
        await asyncio.wait_for(state.assistant_lock.acquire(), timeout=1)
    except TimeoutError:
        return unavailable(429, "当前提问较多，请稍后再试")
    try:
        async with state.assistant_agent_lock:
            if state.assistant_agent is None:
                state.assistant_agent = await state.assistant_resources.enter_async_context(
                    build_agent(settings)
                )
        if body.generation in state.assistant_active:
            return unavailable(409, "此问题正在回答，请等待或停止后重试")
        task = asyncio.create_task(
            answer_question(
                state.assistant_agent,
                body.question,
                body.page,
                body.history,
                QueryRun(
                    state.engine,
                    body.context,
                    body.question,
                    executor=state.assistant_query_executor,
                    emit=emit,
                    request_id=request.state.request_id,
                ),
            )
        )
        state.assistant_active[body.generation] = task
        try:
            async with asyncio.timeout(90):
                while not task.done():
                    await asyncio.wait({task}, timeout=0.25)
                    if emit is None and await request.is_disconnected():
                        return unavailable(499, "已停止回答")
                if task.cancelled():
                    return unavailable(499, "已停止回答")
                answer = task.result()
        finally:
            state.assistant_active.pop(body.generation, None)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not answer["answer"].strip():
            return unavailable(502, "模型返回空回答，请重试")
        return ChatAnswer(model=name, **answer)
    except BoardError as exc:
        if exc.code == "DATA_VERSION_CHANGED":
            return unavailable(409, "页面与查询的数据版本不同，请刷新页面后重新选择商品", exc.code)
        if exc.code == "INVALID_SCOPE":
            return unavailable(422, "请在已发布数据覆盖内选择不超过31天的连续期间", exc.code)
        raise
    except UsageLimitExceeded:
        return unavailable(
            422, "本轮查询已达次数上限，请缩小问题范围后重试", "ASSISTANT_LIMIT_REACHED"
        )
    except TimeoutError:
        return unavailable(504, "回答超时，请重试")
    except Exception as exc:
        logger.warning("问答生成失败 type=%s", type(exc).__name__)
        return unavailable(502, "模型暂时不可用，请稍后重试")
    finally:
        state.assistant_lock.release()
