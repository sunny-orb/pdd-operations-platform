"""M1只读查询边界：身份、闭区间、分页和资源上限。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

METRIC_VERSION = "m1-operating-v1"
METRICS = (
    "product_revenue",
    "visitors",
    "favorites",
    "buyers",
    "units",
    "revenue_per_buyer",
    "buyer_conversion_rate",
)


class QueryError(Exception):
    """只携带可向用户解释的安全错误，不包含SQL或连接信息。"""

    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class ProductQuery(BaseModel):
    """三套筛选各次请求独立；本轮只开放经营/明细，不伪造评分。"""

    scope: Literal["overview", "detail", "scores"] = "overview"
    date_from: date | None = None
    date_to: date | None = None
    shop_id: str | None = Field(default=None, min_length=1, max_length=128)
    product_id: str | None = Field(default=None, min_length=1, max_length=128)
    product_key: list[str] = Field(default_factory=list, max_length=20)
    operator: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    level: str | None = Field(default=None, max_length=200)
    keyword: str | None = Field(default=None, max_length=200)
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=25, ge=1, le=100)
    metric: Literal[
        "product_revenue",
        "visitors",
        "favorites",
        "buyers",
        "units",
        "revenue_per_buyer",
        "buyer_conversion_rate",
        "views",
    ] = "product_revenue"
    comparison: Literal["year", "products", "relative"] = "year"
    data_version: str | None = Field(default=None, max_length=200)
    score_version: str | None = Field(default=None, max_length=200)
    as_of_date: date | None = None
    decision: (
        Literal[
            "unscored", "pending_validation", "optimize", "hold", "fix_key_metrics", "small_scale"
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.scope != "scores" and any(
            x is not None for x in (self.score_version, self.as_of_date, self.decision)
        ):
            raise ValueError("评分专用参数不能用于经营或明细筛选")
        if self.date_from and self.date_to:
            span = (self.date_to - self.date_from).days
            if span < 0 or span >= 366:
                raise ValueError("日期需按先后顺序，单次最多366个日历日")
        if self.product_id and not self.shop_id:
            raise ValueError("商品ID必须同时携带店铺ID")
        for key in self.product_key:
            parts = key.split(":")
            if len(parts) != 3 or parts[0] != "pdd" or not all(parts):
                raise ValueError("商品复合键格式为pdd:店铺ID:商品ID")
            if self.shop_id and parts[1] != self.shop_id:
                raise ValueError("商品复合键与店铺筛选不一致")
        return self


class ScoreQuery(BaseModel):
    """评分有自身日期和结果版本；不接受总览日期驱动重算。"""

    shop_id: str | None = Field(default=None, min_length=1, max_length=128)
    product_id: str | None = Field(default=None, min_length=1, max_length=128)
    product_key: list[str] = Field(default_factory=list, max_length=20)
    operator: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    keyword: str | None = Field(default=None, max_length=200)
    decision: (
        Literal[
            "unscored", "pending_validation", "optimize", "hold", "fix_key_metrics", "small_scale"
        ]
        | None
    ) = None
    page: int = Field(default=1, ge=1, le=1000000)
    page_size: int = Field(default=25, ge=1, le=100)
    as_of_date: date | None = None
    data_version: str | None = Field(default=None, max_length=200)
    score_version: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_identity(self):
        ProductQuery(shop_id=self.shop_id, product_id=self.product_id, product_key=self.product_key)
        return self
