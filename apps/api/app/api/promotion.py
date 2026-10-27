"""M2双模式GET协议；严格过滤、版本409及本地只读快照。"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.promotion_schemas import (
    DaysData,
    DiagnosisData,
    Envelope,
    ErrorEnvelope,
    MetaData,
    OptionsData,
    OverviewData,
)
from app.modules.product_board.repository import wire
from app.modules.promotion.diagnosis import diagnosis
from app.modules.promotion.query import PromotionQuery, days, options, overview
from app.modules.promotion.repository import snapshot

router = APIRouter(
    prefix="/api/v1/promotion",
    tags=["推广分析"],
    responses={s: {"model": ErrorEnvelope} for s in (409, 422, 503)},
)


def response(request, data, repo):
    return wire(
        dict(data=data, meta=dict(data_version=repo.token, request_id=request.state.request_id))
    )


def query(
    date_from: date,
    date_to: date,
    mode: Literal["store", "product"] = "store",
    shop_id: str = Query("", max_length=128),
    channel: Literal["", "product_promotion", "star_shop"] = "",
    category: str = Query("", max_length=200),
    level: str = Query("", max_length=200),
    operator: str = Query("", max_length=200),
    keyword: str = Query("", max_length=120),
    listing_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = 25,
):
    return PromotionQuery(
        date_from,
        date_to,
        mode,
        shop_id,
        channel,
        category,
        level,
        operator,
        keyword,
        tuple(listing_ids or ()),
        page,
        page_size,
    )


@router.get("/meta", response_model=Envelope[MetaData])
def get_meta(request: Request, mode: Literal["store", "product"] = "store"):
    with snapshot(request.app.state.engine, mode) as repo:
        return response(request, repo.metadata(), repo)


@router.get("/options", response_model=Envelope[OptionsData])
def get_options(
    request: Request,
    q: Annotated[PromotionQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
    selected_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
    selected_listing_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(
            request,
            options(
                repo,
                q,
                tuple(dict.fromkeys([*(selected_ids or ()), *(selected_listing_ids or ())])),
            ),
            repo,
        )


@router.get("/overview", response_model=Envelope[OverviewData])
def get_overview(
    request: Request,
    q: Annotated[PromotionQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(request, overview(repo, q), repo)


@router.get("/days", response_model=Envelope[DaysData])
def get_days(
    request: Request,
    q: Annotated[PromotionQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(request, days(repo, q), repo)


@router.get("/diagnosis", response_model=Envelope[DiagnosisData])
def get_diagnosis(
    request: Request,
    q: Annotated[PromotionQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(request, diagnosis(repo, q), repo)
