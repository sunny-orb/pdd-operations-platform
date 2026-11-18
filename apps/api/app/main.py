"""API 应用工厂与统一错误处理。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from time import perf_counter
from uuid import uuid4

import psycopg
from anyio import CapacityLimiter, create_task_group, sleep, to_thread
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.api.aftersales import prewarm_aftersales_overview
from app.api.aftersales import router as aftersales_router
from app.api.data_status import router as data_status_router
from app.api.product_board import prewarm_default_overview
from app.api.product_board import router as products_router
from app.api.promotion import prewarm_product_display
from app.api.promotion import router as promotion_router
from app.api.published_versions import router as published_versions_router
from app.api.sales import router as sales_router
from app.api.system import router
from app.api.weather import router as weather_router
from app.core.config import Settings
from app.core.database_diagnostics import (
    classify_database_failure,
    install_sql_timing,
    request_database_metrics,
)
from app.core.overview_cache import DefaultOverviewCache
from app.db.connection import build_engine
from app.modules.aftersales.read_cache import AftersalesCache
from app.modules.product_board.repository import BoardError
from app.modules.sales.contracts import SalesError

logger = logging.getLogger(__name__)
prewarm_logger = logging.getLogger("uvicorn.error.overview_prewarm")
OVERVIEW_PREWARM_INTERVAL = 30


async def run_overview_prewarm(engine, cache, prepare, label, limiter):
    """各总览独立跟踪版本，共用一个后台计算名额。"""
    version = None
    while True:
        started = perf_counter()
        try:
            version, size = await to_thread.run_sync(
                prepare, engine, cache, version, limiter=limiter
            )
            if size is not None:
                prewarm_logger.info(
                    "%s预热 version=%s cached=%s bytes=%d elapsed_ms=%.2f",
                    label,
                    version,
                    size <= cache.max_bytes,
                    size,
                    (perf_counter() - started) * 1000,
                )
        except Exception as exc:
            reason = exc.code if isinstance(exc, BoardError) else type(exc).__name__
            prewarm_logger.warning("%s预热未完成 reason=%s", label, reason)
        await sleep(OVERVIEW_PREWARM_INTERVAL)


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造服务；输入可选配置，返回独立应用，生命周期负责释放连接池。"""
    configured = settings if settings is not None else Settings()
    install_sql_timing()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.engine = build_engine(configured)
        logger.info("API 启动；数据库配置=%s", application.state.engine is not None)
        try:
            if configured.app_env == "production" and application.state.engine is not None:
                async with create_task_group() as tasks:
                    limiter = CapacityLimiter(1)
                    tasks.start_soon(
                        run_overview_prewarm,
                        application.state.engine,
                        application.state.product_overview_cache,
                        prewarm_default_overview,
                        "商品总览",
                        limiter,
                    )
                    tasks.start_soon(
                        run_overview_prewarm,
                        application.state.engine,
                        application.state.product_daily_overview_cache,
                        partial(prewarm_default_overview, daily=True),
                        "商品单日总览",
                        limiter,
                    )
                    tasks.start_soon(
                        run_overview_prewarm,
                        application.state.engine,
                        application.state.promotion_product_display_cache,
                        prewarm_product_display,
                        "商品推广默认总览",
                        limiter,
                    )
                    tasks.start_soon(
                        run_overview_prewarm,
                        application.state.engine,
                        application.state.aftersales_overview_cache,
                        prewarm_aftersales_overview,
                        "售后默认报告",
                        limiter,
                    )
                    try:
                        yield
                    finally:
                        tasks.cancel_scope.cancel()
            else:
                yield
        finally:
            if application.state.engine is not None:
                application.state.engine.dispose()
            logger.info("API 已停止")

    application = FastAPI(title="拼多多运营平台 API", version="0.1.0", lifespan=lifespan)
    application.state.engine = None
    application.state.product_overview_cache = DefaultOverviewCache()
    application.state.product_daily_overview_cache = DefaultOverviewCache()
    application.state.promotion_product_display_cache = DefaultOverviewCache()
    application.state.aftersales_overview_cache = AftersalesCache()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.cors_origins,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @application.middleware("http")
    async def identify_request(request: Request, call_next):
        """生成内部请求标识；输入请求和下游调用，输出附带标识的响应。"""
        request.state.request_id = str(uuid4())
        request.state.started_at = perf_counter()
        request.state.database_metrics = {}
        metric_token = request_database_metrics.set(request.state.database_metrics)
        try:
            response = await call_next(request)
        finally:
            request_database_metrics.reset(metric_token)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request: Request, status: int, code: str, message: str):
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": getattr(request.state, "request_id", "unavailable"),
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException):
        message = "接口不存在" if error.status_code == 404 else "请求无法处理"
        return error_response(request, error.status_code, "HTTP_ERROR", message)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError):
        return error_response(request, 422, "VALIDATION_ERROR", "请求参数不符合接口要求")

    @application.exception_handler(BoardError)
    async def handle_board_error(request: Request, error: BoardError):
        database_response = handle_database_error(request, error)
        if database_response is not None:
            return database_response
        return error_response(
            request, error.status, error.code, "经营数据暂不可用，请核对数据状态或重试"
        )

    @application.exception_handler(SalesError)
    async def handle_sales_error(request: Request, error: SalesError):
        database_response = handle_database_error(request, error)
        if database_response is not None:
            return database_response
        return error_response(
            request, error.status, error.code, "店铺销售暂不可用，请检查来源状态或重试"
        )

    @application.exception_handler(SQLAlchemyError)
    @application.exception_handler(psycopg.Error)
    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception):
        database_response = handle_database_error(request, error)
        if database_response is not None:
            return database_response
        logger.error(
            "请求处理失败 request_id=%s type=%s",
            getattr(request.state, "request_id", "unavailable"),
            type(error).__name__,
        )
        return error_response(request, 500, "INTERNAL_ERROR", "服务暂时不可用")

    def handle_database_error(request: Request, error: Exception):
        """分类已识别的数据库异常；日志仅含请求/路由模板、异常类型及实测耗时。"""
        if isinstance(error, (BoardError, SalesError)) and error.code != "DATABASE_UNAVAILABLE":
            return None
        failure = classify_database_failure(error)
        if failure is None:
            return None
        metrics = getattr(request.state, "database_metrics", {})
        elapsed = (perf_counter() - getattr(request.state, "started_at", perf_counter())) * 1000
        logger.warning(
            "数据库请求失败 request_id=%s path=%s code=%s type=%s stage=%s "
            "request_elapsed_ms=%.2f sql_elapsed_ms=%s sql_total_ms=%s",
            getattr(request.state, "request_id", "unavailable"),
            getattr(request.scope.get("route"), "path", "unmatched"),
            failure.code,
            failure.exception_types,
            failure.stage,
            elapsed,
            round(metrics["sql_elapsed_ms"], 2) if "sql_elapsed_ms" in metrics else "unavailable",
            round(metrics["sql_total_ms"], 2) if "sql_total_ms" in metrics else "unavailable",
        )
        return error_response(request, failure.status, failure.code, "经营数据暂不可用，请稍后重试")

    application.include_router(router)
    application.include_router(products_router)
    application.include_router(promotion_router)
    application.include_router(sales_router)
    application.include_router(aftersales_router)
    application.include_router(weather_router)
    application.include_router(data_status_router)
    application.include_router(published_versions_router)
    return application


app = create_app()
