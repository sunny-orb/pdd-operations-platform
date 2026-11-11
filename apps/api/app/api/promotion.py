"""M2双模式GET协议；严格过滤、版本409及本地只读快照。"""

import json
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.promotion_schemas import (
    DaysData,
    DiagnosisData,
    DiagnosisObjectsData,
    Envelope,
    ErrorEnvelope,
    MetaData,
    Metric,
    OptionsData,
    OverviewData,
    SelectedOptionsData,
)
from app.core.overview_cache import DefaultOverviewCache
from app.modules.product_board.repository import wire
from app.modules.promotion.diagnosis import diagnosis, diagnosis_objects
from app.modules.promotion.query import PromotionQuery, days, options, overview, selected_options
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


def display_overview(data):
    """展示投影只去掉前端不消费的逐日覆盖及指标审计字段，保留完整计算结果。"""

    def project(item, *fields):
        return {
            **item,
            **{
                field: {
                    key: {
                        name: value for name, value in metric.items() if name in Metric.model_fields
                    }
                    for key, metric in item[field].items()
                }
                for field in fields
            },
        }

    return {
        **project(data, "metrics", "net"),
        "series": [
            project(
                {key: value for key, value in point.items() if key != "coverage"},
                "metrics",
                "previous",
            )
            for point in data["series"]
        ],
        "channel_summaries": [
            project(item, "metrics", "net") for item in data["channel_summaries"]
        ],
    }


def default_product_display_query(repo):
    start, end = repo.recommended_from, repo.end
    if start is None or end is None or (end - start).days >= 366:
        return None
    return PromotionQuery(start, end, mode="product")


def cached_product_display(engine, cache: DefaultOverviewCache, q: PromotionQuery, version: str):
    def compute():
        with snapshot(engine, "product", version) as repo:
            data = OverviewData.model_validate(wire(display_overview(overview(repo, q))))
            return data.model_dump_json().encode()

    return cache.get_or_compute(version, compute)


def prewarm_product_display(engine, cache: DefaultOverviewCache, previous_version: str | None):
    """发布后预计算商品推广默认展示，后台和首次请求复用相同结果。"""
    with snapshot(engine, "product") as repo:
        version = repo.token
        if version == previous_version:
            return version, None
        q = default_product_display_query(repo)
    if q is None:
        return version, None
    return version, len(cached_product_display(engine, cache, q, version))


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
    catalog: bool = False,
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(
            request,
            options(
                repo,
                q,
                tuple(dict.fromkeys([*(selected_ids or ()), *(selected_listing_ids or ())])),
                catalog=catalog,
            ),
            repo,
        )


@router.get("/overview", response_model=Envelope[OverviewData])
def get_overview(
    request: Request,
    q: Annotated[PromotionQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
    detail: Literal["full", "display"] = "full",
):
    engine = request.app.state.engine
    with snapshot(engine, q.mode, data_version) as repo:
        if q.mode != "product" or detail != "display" or q != default_product_display_query(repo):
            data = overview(repo, q)
            return response(request, display_overview(data) if detail == "display" else data, repo)
    # 等待同版本的计算时，释放上面的只读连接。
    payload = cached_product_display(
        engine, request.app.state.promotion_product_display_cache, q, data_version
    )
    return dict(
        data=json.loads(payload),
        meta=dict(data_version=data_version, request_id=request.state.request_id),
    )


@router.get("/options/selected", response_model=Envelope[SelectedOptionsData])
def get_selected_options(
    request: Request,
    data_version: str = Query(min_length=1, max_length=100),
    selected_ids: Annotated[list[str] | None, Query(max_length=20)] = None,
    mode: Literal["product"] = "product",
):
    with snapshot(request.app.state.engine, mode, data_version) as repo:
        return response(request, selected_options(repo, tuple(selected_ids or ())), repo)


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
    detail: Literal["full", "summary", "alerts"] = "full",
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(request, diagnosis(repo, q, detail=detail), repo)


@router.get("/diagnosis/objects", response_model=Envelope[DiagnosisObjectsData])
def get_diagnosis_objects(
    request: Request,
    q: Annotated[PromotionQuery, Depends(query)],
    data_version: str = Query(min_length=1, max_length=100),
    group: Literal[
        "products", "flagged_products", "focused_products", "shops", "categories"
    ] = "products",
    search: str = Query("", max_length=200),
):
    with snapshot(request.app.state.engine, q.mode, data_version) as repo:
        return response(request, diagnosis_objects(repo, q, group, search), repo)
