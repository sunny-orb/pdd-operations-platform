"""M3成功模型；数字字符串、空值和缺失状态作为稳定查询协议。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

Numeric = Annotated[str, Field(pattern=r"^-?\d+(\.\d+)?$")]


class SalesMeta(BaseModel):
    data_version: str
    input_versions: dict[str, str] = Field(default_factory=dict)
    observed_from: str | None
    observed_through: str | None
    completeness_state: Literal["unknown"]
    complete_through: None
    source_updated_text: str | None
    source_updated_at: None
    last_attempt_status: str | None
    last_attempt_at: str | None
    last_success_at: str | None
    request_id: str


class Coverage(BaseModel):
    latest_observed_date: str | None
    observed_days: int
    missing_dates: list[str]
    field_null_counts: dict[str, int]
    reason_code: str
    window_start: str
    window_end: str


class SalesMetrics(BaseModel):
    shop_revenue: Numeric | None
    shop_visitors: Numeric | None
    buyers: Numeric | None
    orders: Numeric | None
    buyer_conversion: Numeric | None
    aov: Numeric | None
    refund_rate: Numeric | None
    promotion_spend: Numeric | None
    promotion_ctr: Numeric | None
    promotion_roi: Numeric | None
    refund_match: dict[str, Any] | None = None
    promotion_channels: list[dict[str, Any]] = Field(default_factory=list)
    coverage: Coverage
    field_states: dict[str, str]


class ShopItem(SalesMetrics):
    shop_id: str
    shop_name: str
    display_name: str
    rank: int | None
    sales_share: Numeric | None


class ShopOption(BaseModel):
    source_shop_id: str
    shop_id: str
    shop_name: str
    display_name: str


class MonthOption(BaseModel):
    month: str
    date_from: str
    date_to: str


class SalesFilters(BaseModel):
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    shops: list[ShopOption]
    months: list[MonthOption]
    default_month: str
    recommended_date_from: str
    recommended_date_to: str
    calendar_through: str
    observed_from: str | None
    observed_through: str | None
    capabilities: dict[str, bool]


class FiltersResponse(BaseModel):
    data: SalesFilters
    meta: SalesMeta


class PerformanceResponse(BaseModel):
    data: SalesMetrics
    meta: SalesMeta


class ShopList(BaseModel):
    items: list[ShopItem]
    missing_shop_days: int


class ShopsResponse(BaseModel):
    data: ShopList
    meta: SalesMeta


class DetailResponse(BaseModel):
    data: dict[str, Any]
    meta: SalesMeta
