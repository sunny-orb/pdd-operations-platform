"""浏览器定时检查发布标识；版本变化后才重取该域元信息及事实。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.modules.published_versions import read_published_versions

router = APIRouter(prefix="/api/v1/published-versions", tags=["发布版本"])


class DomainVersions(BaseModel):
    products: str
    promotion: str
    sales: str
    aftersales: str


class PublishedVersionsResponse(BaseModel):
    data: DomainVersions


@router.get("", response_model=PublishedVersionsResponse)
def get_published_versions(request: Request):
    return read_published_versions(request.app.state.engine)
