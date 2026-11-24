"""右侧运营问答协议，仅向本机开发看板开放模型试用。"""

import asyncio
import json
import logging
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from pydantic_ai.exceptions import UsageLimitExceeded

from app.modules.assistant.chat import ChatScope, Product, QueryRun, answer_question, build_agent
from app.modules.product_board.repository import BoardError

router = APIRouter(prefix="/api/v1/assistant")
logger = logging.getLogger(__name__)
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


def local_preview(request: Request) -> bool:
    settings = request.app.state.assistant_settings
    origin = request.headers.get("origin")
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


def approved_models(settings) -> list[str]:
    if not settings.assistant_business_data_enabled:
        return []
    return [
        name
        for name in settings.assistant_models
        if name.partition(":")[0] in settings.assistant_business_providers
    ]


@router.get("/models")
async def models(request: Request):
    if not local_preview(request):
        return unavailable(403, "模型试用仅在本机开发看板开放")
    settings = request.app.state.assistant_settings
    return {
        "models": approved_models(settings),
        "business_data_enabled": settings.assistant_business_data_enabled,
    }


@router.post("/stop")
async def stop(request: Request, body: Generation):
    if not local_preview(request):
        return unavailable(403, "模型试用仅在本机开发看板开放")
    active = request.app.state.assistant_active
    if active is not None and active[0] == body.generation:
        active[1].cancel()
    return {"stopped": True}


@router.post("/chat", response_model=ChatAnswer)
async def chat(request: Request, body: ChatInput):
    if "text/event-stream" not in request.headers.get("accept", ""):
        return await chat_response(request, body)
    # 本机访问边界在发送响应头前验证；流中错误通过 error 事件结束。
    if not local_preview(request):
        return unavailable(403, "模型试用仅在本机开发看板开放")

    async def events():
        queue = asyncio.Queue()
        task = asyncio.create_task(chat_response(request, body, queue.put_nowait))
        try:
            yield 'data: {"type":"status","text":"正在理解问题…"}\n\n'
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
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
    if not local_preview(request):
        return unavailable(403, "模型试用仅在本机开发看板开放")
    state = request.app.state
    settings = state.assistant_settings
    if not settings.assistant_model:
        return unavailable(503, "本机尚未配置问答模型")
    allowed = approved_models(settings)
    name = body.model or next(iter(allowed), settings.assistant_model)
    if name not in settings.assistant_models:
        return unavailable(422, "此模型未启用，请重新选择模型")
    if name not in allowed:
        return unavailable(403, "本机尚未授权此模型接收经营问题和查询摘要")
    try:
        await asyncio.wait_for(state.assistant_lock.acquire(), timeout=1)
    except TimeoutError:
        return unavailable(429, "上一条请求正在结束，请稍后再试")
    try:
        if name not in state.assistant_agents:
            state.assistant_agents[name] = await state.assistant_resources.enter_async_context(
                build_agent(settings.model_copy(update={"assistant_model": name}))
            )
        task = asyncio.create_task(
            answer_question(
                state.assistant_agents[name],
                body.question,
                body.page,
                body.history,
                QueryRun(
                    state.engine,
                    body.context,
                    body.question,
                    executor=state.assistant_query_executor,
                    emit=emit,
                    request_id=str(body.generation),
                ),
            )
        )
        state.assistant_active = (body.generation, task)
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
            state.assistant_active = None
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
        return unavailable(502, "模型暂时不可用，请检查本机模型登录或配置后重试")
    finally:
        state.assistant_lock.release()
