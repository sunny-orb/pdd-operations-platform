"""M1只读协议；GET不触发上游访问或任务。"""

import json
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.product_board_schemas import (
    CatalogData,
    Envelope,
    ErrorEnvelope,
    MetaData,
    OptionsData,
    OverviewData,
    PageData,
    RisksData,
    ScoreDetail,
    ScoresData,
)
from app.core.overview_cache import DefaultOverviewCache
from app.modules.product_board.query import (
    BoardQuery,
    catalog,
    days,
    options,
    overview,
    risk_feed,
    score_cards,
)
from app.modules.product_board.repository import snapshot, wire

router = APIRouter(
    prefix="/api/v1/products",
    tags=["商品经营"],
    responses={s: {"model": ErrorEnvelope} for s in (409, 422, 503)},
)


def response(request, data, repo):
    return wire(
        dict(data=data, meta=dict(data_version=repo.token, request_id=request.state.request_id))
    )


@router.get("/meta", response_model=Envelope[MetaData])
def meta(request: Request):
    with snapshot(request.app.state.engine) as repo:
        return response(request, repo.meta(), repo)


def query(
    date_from: date,
    date_to: date,
    shop_id: str = "",
    operator: str = "",
    category: str = "",
    level: str = "",
    listing_ids: Annotated[list[str] | None, Query()] = None,
    keyword: str = Query("", max_length=120),
    page: int = Query(1, ge=1, le=100000),
    page_size: int = 25,
    judgement: str = "",
):
    return BoardQuery(
        date_from,
        date_to,
        shop_id,
        operator,
        category,
        level,
        tuple(listing_ids or ()),
        keyword,
        page,
        page_size,
        judgement,
    )


def default_overview_query(repo):
    return BoardQuery(max(repo.start, repo.end - timedelta(days=30)), repo.end)


def daily_overview_query(repo):
    return BoardQuery(repo.end, repo.end)


def cached_default_overview(engine, cache: DefaultOverviewCache, q: BoardQuery, version: str):
    """后台预热和请求共用一次版本校验后的计算。"""

    def compute():
        with snapshot(engine, version) as repo:
            data = OverviewData.model_validate(wire(overview(repo, q)))
            return data.model_dump_json().encode()

    return cache.get_or_compute(version, compute)


def prewarm_default_overview(
    engine, cache: DefaultOverviewCache, previous_version: str | None, daily: bool = False
):
    """只检查当前发布版本，换版时预计算，等待缓存期间不占连接。"""
    with snapshot(engine) as repo:
        version = repo.token
        if version == previous_version:
            return version, None
        q = default_overview_query(repo)
        if daily:
            latest = daily_overview_query(repo)
            if latest == q:
                return version, None
            q = latest
    return version, len(cached_default_overview(engine, cache, q, version))


@router.get("/overview", response_model=Envelope[OverviewData])
def get_overview(
    request: Request,
    q: Annotated[BoardQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    engine = request.app.state.engine
    with snapshot(engine, data_version) as repo:
        if q == default_overview_query(repo):
            cache = request.app.state.product_overview_cache
        elif q == daily_overview_query(repo):
            cache = request.app.state.product_daily_overview_cache
        else:
            return response(request, overview(repo, q), repo)
    payload = cached_default_overview(engine, cache, q, data_version)
    return dict(
        data=json.loads(payload),
        meta=dict(data_version=data_version, request_id=request.state.request_id),
    )


@router.get("/risks", response_model=Envelope[RisksData])
def get_risks(
    request: Request,
    q: Annotated[BoardQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, data_version) as repo:
        return response(request, risk_feed(repo, q), repo)


@router.get("/options", response_model=Envelope[OptionsData])
def get_options(
    request: Request,
    q: Annotated[BoardQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
    scope: Literal["main", "score", "detail"] = "main",
    selected_listing_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
):
    with snapshot(request.app.state.engine, data_version) as repo:
        return response(
            request, options(repo, q, tuple(selected_listing_ids or ()), scope == "score"), repo
        )


@router.get("/catalog", response_model=Envelope[CatalogData])
def get_catalog(
    request: Request,
    date_from: date,
    date_to: date,
    data_version: str = Query(min_length=1, max_length=100),
    scope: Literal["main", "score", "detail"] = "main",
):
    with snapshot(request.app.state.engine, data_version) as repo:
        return response(
            request, catalog(repo, BoardQuery(date_from, date_to), scope == "score"), repo
        )


@router.get("/scores", response_model=Envelope[ScoresData])
def get_scores(
    request: Request,
    q: Annotated[BoardQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, data_version) as repo:
        return response(request, score_cards(repo, q), repo)


@router.get("/scores/{listing_id}", response_model=Envelope[ScoreDetail | None])
def get_score_detail(
    request: Request, listing_id: str, data_version: str = Query(min_length=1, max_length=100)
):
    with snapshot(request.app.state.engine, data_version) as repo:
        result = score_cards(repo, BoardQuery(repo.start, repo.end, size=100), listing_id)
        return response(request, result["items"][0] if result["items"] else None, repo)


@router.get("/days", response_model=Envelope[PageData])
def get_days(
    request: Request,
    q: Annotated[BoardQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, data_version) as repo:
        return response(request, days(repo, q), repo)
