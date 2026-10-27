"""M1经营GET协议；前端只读PG已发布数据，不触发接数或评分任务。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.api.product_schemas import ERROR_RESPONSES, RESPONSE_MODELS
from app.modules.products.query import ProductQuery, QueryError, ScoreQuery
from app.modules.products.repository import ProductRepository
from app.modules.products.score_repository import ScoreRepository
from app.modules.products.service import ProductService, safe_json

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def endpoint(name):
    """为七个经营查询复用同一只读协议，领域行为仍由服务分别处理。"""

    def handle(request: Request, query: Annotated[ProductQuery, Query()]):
        if query.scope == "scores":
            if name != "filters":
                raise QueryError(422, "INVALID_SCOPE", "评分筛选仅用于评分区")
            if query.date_from or query.date_to:
                raise QueryError(
                    422, "INVALID_SCORE_DATE_FILTER", "评分使用自身发布日期，不接收总览日期"
                )
            return score_response(request, ScoreQuery.model_validate(query.model_dump()), "filters")
        with ProductRepository(request.app.state.engine).snapshot(query.data_version) as view:
            service = ProductService(view, query)
            data = getattr(service, name)()
            coverage = service.period(service.current)[1]
            meta = view.metadata(service.query, coverage)
            meta["request_id"] = request.state.request_id
            return JSONResponse(
                content=safe_json(
                    dict(
                        data=data,
                        availability="ready" if coverage["observed_days"] else "empty",
                        computation_state="ready",
                        meta=meta,
                    )
                )
            )

    handle.__name__ = f"products_{name}"
    return handle


for name in ("filters", "overview", "trend", "days", "brief", "alerts", "diagnosis"):
    router.add_api_route(
        "/" + name,
        endpoint(name),
        methods=["GET"],
        response_model=RESPONSE_MODELS[name],
        responses=ERROR_RESPONSES,
        summary={
            "filters": "独立范围及分页商品目录",
            "overview": "七主卡、周期及流量节点",
            "trend": "同比/商品/相对走势",
            "days": "商品日明细分页",
            "brief": "经营核心结论四榜",
            "alerts": "规则异常及缺项",
            "diagnosis": "证据型规则诊断",
        }[name],
        description="只读PostgreSQL相容发布；不拉取上游或触发计算。金额/比率用十进制字符串，缺失为null。参数及口径见数据契约第7节。",
    )


def score_response(request, query, method, score_id=None):
    with ScoreRepository(request.app.state.engine).snapshot(query) as view:
        data = getattr(view, method)(score_id) if score_id else getattr(view, method)()
        meta = view.metadata()
        meta["request_id"] = request.state.request_id
        return JSONResponse(
            content=safe_json(
                dict(
                    data=data,
                    meta=meta,
                    availability=(
                        "not_connected"
                        if method == "list" and not view.compatible
                        else ("empty" if method == "list" and not data["items"] else "ready")
                    ),
                    computation_state=meta["computation_state"],
                )
            )
        )


@router.get(
    "/scores",
    response_model=RESPONSE_MODELS["scores"],
    responses=ERROR_RESPONSES,
    summary="新品评分卡片及分页",
)
def scores(request: Request, query: Annotated[ScoreQuery, Query()]):
    return score_response(request, query, "list")


@router.get(
    "/scores/{score_id}",
    response_model=RESPONSE_MODELS["score_detail"],
    responses=ERROR_RESPONSES,
    summary="单款十指标、四线及评分证据",
)
def score_detail(score_id: UUID, request: Request, query: Annotated[ScoreQuery, Query()]):
    return score_response(request, query, "detail", score_id)
