"""系统骨架状态；不伪造经营数据或同步成功。"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import database_available

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health/live", tags=["health"])
def liveness() -> dict:
    """报告 API 进程存活；无参数，返回进程状态，不代表数据库或业务就绪。"""
    return {"status": "ok", "service": "pdd-operations-api"}


@router.get("/health/ready", tags=["health"])
def readiness(request: Request):
    """检查依赖；输入请求中的连接池，数据库不可用时返回 503。"""
    engine = request.app.state.engine
    if engine is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "not_configured"},
        )
    try:
        database_available(engine)
    except SQLAlchemyError:
        logger.warning("数据库就绪检查失败 request_id=%s", request.state.request_id)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unavailable"},
        )
    return {"status": "ready", "database": "connected", "business_data": "not_integrated"}


@router.get("/api/v1/system/status", tags=["system"])
def system_status(request: Request) -> dict:
    """返回初始化状态；输入请求，输出无凭据的能力清单，不进行上游访问。"""
    return {
        "data": {
            "stage": "bootstrap",
            "business_data": "not_integrated",
            "database_configured": request.app.state.engine is not None,
            "modules": [
                {"key": key, "status": "planned"}
                for key in ["overview", "sales", "products", "promotion", "aftersales"]
            ],
        },
        "meta": {
            "request_id": request.state.request_id,
            "queried_at": datetime.now(UTC).isoformat(),
            "data_complete_through": None,
            "last_successful_sync_at": None,
            "published_batch_id": None,
        },
    }
