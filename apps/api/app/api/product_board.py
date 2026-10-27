"""M1只读协议；GET不触发上游访问或任务。"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.product_board_schemas import (
    Envelope,
    ErrorEnvelope,
    MetaData,
    OptionsData,
    OverviewData,
    PageData,
    ScoreDetail,
    ScoresData,
)
from app.modules.product_board.query import BoardQuery, days, options, overview, score_cards
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


@router.get("/overview", response_model=Envelope[OverviewData])
def get_overview(
    request: Request,
    q: Annotated[BoardQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, data_version) as repo:
        return response(request, overview(repo, q), repo)


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
