"""API 应用工厂与统一错误处理。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.products import router as products_router
from app.api.sales import router as sales_router
from app.api.system import router
from app.core.config import Settings
from app.db.connection import build_engine
from app.modules.products.query import QueryError
from app.modules.sales.contracts import SalesError

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造服务；输入可选配置，返回独立应用，生命周期负责释放连接池。"""
    configured = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.engine = build_engine(configured)
        logger.info("API 启动；数据库配置=%s", application.state.engine is not None)
        try:
            yield
        finally:
            if application.state.engine is not None:
                application.state.engine.dispose()
            logger.info("API 已停止")

    application = FastAPI(title="拼多多运营平台 API", version="0.1.0", lifespan=lifespan)
    application.state.engine = None
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
        response = await call_next(request)
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

    @application.exception_handler(SalesError)
    async def handle_sales_error(request: Request, error: SalesError):
        return error_response(
            request, error.status, error.code, "店铺销售暂不可用，请检查来源状态或重试"
        )

    @application.exception_handler(QueryError)
    async def handle_query_error(request: Request, error: QueryError):
        return error_response(request, error.status, error.code, error.message)

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception):
        logger.error(
            "请求处理失败 request_id=%s type=%s",
            getattr(request.state, "request_id", "unavailable"),
            type(error).__name__,
        )
        return error_response(request, 500, "INTERNAL_ERROR", "服务暂时不可用")

    application.include_router(router)
    application.include_router(products_router)
    application.include_router(sales_router)
    return application


app = create_app()
