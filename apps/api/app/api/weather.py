"""全国天气本地只读接口；日期选择不触发来源接收。"""

from fastapi import APIRouter, Query, Request

from app.modules.weather.contracts import MetaEnvelope, PublicationEnvelope
from app.modules.weather.repository import snapshot

router = APIRouter(prefix="/api/v1/weather", tags=["全国天气"])


@router.get("/meta", response_model=MetaEnvelope)
def get_meta(request: Request):
    with snapshot(request.app.state.engine) as repo:
        return dict(data=repo.meta, meta=repo.meta)


@router.get("/publication", response_model=PublicationEnvelope)
def get_publication(
    request: Request,
    publication_id: str | None = Query(None, min_length=1, max_length=128),
    data_version: str | None = Query(None, min_length=1, max_length=100),
):
    with snapshot(request.app.state.engine, publication_id, data_version) as repo:
        return dict(data=repo.data, meta=repo.meta)
