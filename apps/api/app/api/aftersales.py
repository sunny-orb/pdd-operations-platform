"""M5固定报告GET协议；普通读取不接上游、不登记任务。"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.aftersales_schemas import Analysis, Envelope, Meta, Options, Overview
from app.modules.aftersales.analysis import analysis
from app.modules.aftersales.query import AfterQuery, options, overview
from app.modules.aftersales.repository import snapshot
from app.modules.aftersales.targets import add_target_ranges
from app.modules.product_board.repository import BoardError, wire

router = APIRouter(prefix="/api/v1/aftersales", tags=["售后分析"])


def query(
    report_id: str | None = Query(None, min_length=1, max_length=128),
    category: str = Query("", max_length=200),
    keyword: str = Query("", max_length=120),
    focus: Literal["all", "size", "quality"] = "all",
    view: Literal["category", "style", "mom", "yoy", "quality", "history"] = "category",
    page: int = Query(1, ge=1, le=100000),
    size: int = 25,
):
    if size not in (25, 50, 100):
        raise BoardError("PAGE_SIZE_INVALID", 422)
    return AfterQuery(report_id, category, keyword.strip(), focus, view, page, size)


def decimal_wire(value):
    """API只输出普通十进制字符串；真实零及极小比率不转指数记法。"""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: decimal_wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [decimal_wire(v) for v in value]
    return wire(value)


def response(repo, data):
    return decimal_wire(dict(data=data, meta=repo.metadata()))


@router.get("/meta", response_model=Envelope[Meta])
def get_meta(request: Request, report_id: str | None = Query(None, min_length=1, max_length=128)):
    with snapshot(request.app.state.engine, report_id) as repo:
        return response(repo, repo.metadata())


@router.get("/options", response_model=Envelope[Options])
def get_options(
    request: Request,
    q: Annotated[AfterQuery, Depends(query)],
    data_version: str | None = Query(None, min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, q.report_id, data_version) as repo:
        return response(repo, options(repo, q))


@router.get("/overview", response_model=Envelope[Overview])
def get_overview(
    request: Request,
    q: Annotated[AfterQuery, Depends(query)],
    data_version: str | None = Query(None, min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, q.report_id, data_version) as repo:
        return response(repo, add_target_ranges(repo, q, overview(repo, q)))


@router.get("/analysis", response_model=Envelope[Analysis], response_model_exclude_none=False)
def get_analysis(
    request: Request,
    q: Annotated[AfterQuery, Depends(query)],
    data_version: str | None = Query(None, min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, q.report_id, data_version) as repo:
        return response(repo, analysis(repo, q))
