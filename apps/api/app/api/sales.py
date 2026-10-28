"""新版M3只读协议处理；来源接收永不在GET内运行。"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.sales_schemas import (
    DetailResponse,
    FiltersResponse,
    PerformanceResponse,
    ShopsResponse,
)
from app.modules.sales.contracts import SalesError
from app.modules.sales.query import SalesQuery, SalesService

router = APIRouter(
    prefix="/api/v1/sales",
    tags=["店铺销售"],
    responses={
        409: {"description": "版本已变化"},
        422: {"description": "筛选无效"},
        503: {"description": "未接入或数据库不可用"},
    },
)


def finish(request, result):
    result["meta"]["request_id"] = request.state.request_id
    return result


@router.get("/filters", response_model=FiltersResponse)
def filters(request: Request):
    return finish(request, SalesService(request.app.state.engine).filters())


@router.get("/performance", response_model=PerformanceResponse)
def performance(
    request: Request,
    date_from: date,
    date_to: date,
    data_version: str = Query(min_length=1, max_length=100),
    shop_id: str | None = None,
):
    query = SalesQuery(date_from, date_to, data_version, shop_id)
    return finish(request, SalesService(request.app.state.engine).performance(query))


@router.get("/shops", response_model=ShopsResponse)
def shops(
    request: Request,
    date_from: date,
    date_to: date,
    data_version: str = Query(min_length=1, max_length=100),
):
    if "shop_id" in request.query_params:
        raise SalesError("SHOP_FILTER_NOT_ALLOWED", 422)
    return finish(
        request,
        SalesService(request.app.state.engine).shops(SalesQuery(date_from, date_to, data_version)),
    )


@router.get("/targets", response_model=DetailResponse)
def targets(
    request: Request,
    month: str,
    data_version: str = Query(min_length=1, max_length=100),
    target_basis: str = Query(default="base", pattern="^(base|stretch)$"),
):
    from app.modules.sales.detail_queries import SalesDetails

    return finish(
        request, SalesDetails(request.app.state.engine).targets(month, data_version, target_basis)
    )


@router.get("/ratings", response_model=DetailResponse)
def ratings(
    request: Request,
    date_from: date,
    date_to: date,
    data_version: str = Query(min_length=1, max_length=100),
    shop_ids: Annotated[list[str] | None, Query()] = None,
):
    from app.modules.sales.detail_queries import SalesDetails

    return finish(
        request,
        SalesDetails(request.app.state.engine).ratings(
            SalesQuery(date_from, date_to, data_version), shop_ids
        ),
    )


@router.get("/regions", response_model=DetailResponse)
def regions(
    request: Request,
    date_from: date,
    date_to: date,
    data_version: str = Query(min_length=1, max_length=100),
    shop_id: str | None = None,
):
    from app.modules.sales.region_queries import RegionQueries

    return finish(
        request,
        RegionQueries(request.app.state.engine).regions(
            SalesQuery(date_from, date_to, data_version, shop_id)
        ),
    )


@router.get("/product-regions/filters", response_model=DetailResponse)
def product_filters(
    request: Request,
    data_version: str = Query(min_length=1, max_length=100),
    category: str = "",
    search: str = "",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    from app.modules.sales.region_queries import RegionQueries

    return finish(
        request,
        RegionQueries(request.app.state.engine).filters(
            data_version, category, search, offset, limit
        ),
    )


@router.get("/product-regions", response_model=DetailResponse)
def product_regions(
    request: Request,
    date_from: date,
    date_to: date,
    data_version: str = Query(min_length=1, max_length=100),
    shop_id: str | None = None,
    category: str = "",
    product_key: str = "",
):
    from app.modules.sales.region_queries import RegionQueries

    return finish(
        request,
        RegionQueries(request.app.state.engine).regions(
            SalesQuery(date_from, date_to, data_version, shop_id),
            product=True,
            category=category,
            product_key=product_key,
        ),
    )
