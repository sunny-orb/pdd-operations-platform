"""M5固定报告GET协议；普通读取不接上游、不登记任务。"""

import json
from dataclasses import replace
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


def overview_payload(repo, q, cache):
    """发布版本校验通过后缓存经营结果，实时来源状态仍由当前事务读取。"""
    default_id = repo.defaults[0]["id"] if repo.defaults else None
    scope = replace(
        q,
        report_id=None if q.report_id == default_id else q.report_id,
        view="category",
        page=1,
        size=25,
    )
    key = (repo.token, scope)
    payload = cache.get(key)
    if payload is None:
        data = add_target_ranges(repo, scope, overview(repo, scope))
        payload = Overview.model_validate(decimal_wire(data)).model_dump_json().encode()
        cache.put(key, payload)
    return payload


def prewarm_aftersales_overview(engine, cache, previous_version):
    """发布变化时串行准备默认报告与最近三个可选周期。"""
    with snapshot(engine, rows_cache=getattr(cache, "rows", None)) as repo:
        version = repo.token
        if version == previous_version or not repo.report:
            return version, None
        report_ids = [report["id"] for report in repo.defaults[1:4]]
        size = len(overview_payload(repo, AfterQuery(), cache))
    for report_id in report_ids:
        with snapshot(engine, report_id, version, rows_cache=getattr(cache, "rows", None)) as repo:
            overview_payload(repo, AfterQuery(report_id=report_id), cache)
    return version, size


@router.get("/meta", response_model=Envelope[Meta])
def get_meta(request: Request, report_id: str | None = Query(None, min_length=1, max_length=128)):
    with snapshot(
        request.app.state.engine,
        report_id,
        rows_cache=request.app.state.aftersales_overview_cache.rows,
    ) as repo:
        metadata = repo.metadata()
        return dict(data=metadata, meta=metadata)


@router.get("/options", response_model=Envelope[Options])
def get_options(
    request: Request,
    q: Annotated[AfterQuery, Depends(query)],
    data_version: str | None = Query(None, min_length=1, max_length=100),
):
    with snapshot(
        request.app.state.engine,
        q.report_id,
        data_version,
        rows_cache=request.app.state.aftersales_overview_cache.rows,
    ) as repo:
        return response(repo, options(repo, q))


@router.get("/overview", response_model=Envelope[Overview])
def get_overview(
    request: Request,
    q: Annotated[AfterQuery, Depends(query)],
    data_version: str | None = Query(None, min_length=1, max_length=100),
    detail: Literal["full", "summary"] = "full",
):
    with snapshot(
        request.app.state.engine,
        q.report_id,
        data_version,
        rows_cache=request.app.state.aftersales_overview_cache.rows,
    ) as repo:
        data = json.loads(overview_payload(repo, q, request.app.state.aftersales_overview_cache))
        if q.report_id:
            # 最新默认模式与点选最新一期共用指标，趋势仍严格按当前模式展示。
            data["trend"] = [point for point in data["trend"] if point["id"] == q.report_id]
        if detail == "summary":
            data["actions"] = []
            data["category_actions"] = data["category_actions"][:3]
            data["style_actions"] = data["style_actions"][:3]
        return dict(data=data, meta=repo.metadata())


@router.get("/analysis", response_model=Envelope[Analysis], response_model_exclude_none=False)
def get_analysis(
    request: Request,
    q: Annotated[AfterQuery, Depends(query)],
    data_version: str | None = Query(None, min_length=1, max_length=100),
):
    with snapshot(
        request.app.state.engine,
        q.report_id,
        data_version,
        rows_cache=request.app.state.aftersales_overview_cache.rows,
    ) as repo:
        return response(repo, analysis(repo, q))
