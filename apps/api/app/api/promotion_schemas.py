"""M2双模式协议；金额、比率及广告计数为十进制字符串，NULL附状态。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.api.product_board_schemas import ErrorEnvelope


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")


class Metric(Record):
    value: str | None
    status: str
    reason: str | None
    label: str
    type: str
    previous: str | None = None
    change: str | None = None
    comparison_status: (
        Literal[
            "available",
            "reference",
            "no_current",
            "no_previous",
            "zero_base",
            "invalid_base",
            "history_short",
        ]
        | None
    ) = None
    business_date_basis: Literal["payment_date"] | None = None
    attribution_state: Literal["source_reported_payment_date"] | None = None


class MetaData(Record):
    mode: Literal["store", "product"]
    date_from: str | None
    date_to: str | None
    recommended_from: str | None
    recommended_to: str | None
    observed_rows: str
    complete_through: str | None
    completeness: str
    business_date_basis: Literal["payment_date"]
    attribution_state: Literal["source_reported_payment_date"]
    attribution_window_days: None
    sources: list[Record]


class OptionsData(Record):
    shops: list[Record]
    channels: list[Record]
    products: list[Record]
    selected_products: list[Record]
    categories: list[str]
    levels: list[str]
    operators: list[str]
    total: int


class SelectedOptionsData(Record):
    products: list[Record]


class RoiExclusions(Record):
    missing_spend_rows: int
    missing_revenue_rows: int
    revenue_without_spend: str


class TopDate(Record):
    date: str
    roi: str
    spend: str
    revenue: str
    roi_status: str
    roi_exclusions: RoiExclusions


class OverviewData(Record):
    roi_exclusions: RoiExclusions | None = None
    metrics: dict[str, Metric]
    net: dict[str, Metric]
    series: list[Record]
    funnel: list[Record]
    top5: list[TopDate]
    coverage: Record
    current_record_count: str
    dataset_record_count: str
    windows: list[Record]
    channel_summaries: list[Record]


class DaysData(Record):
    items: list[Record]
    total: int
    page: int
    size: int
    total_pages: int
    row_grain: str


class DiagnosisData(Record):
    current_window: Record
    previous_window: Record
    overall: Record | None
    products: list[Record]
    flagged_products: list[Record]
    focused_products: list[Record]
    shops: list[Record]
    categories: list[Record]
    counts: Record
    coverage: Record
    rule_version: str
    rule_states: Record
    status: str


class DiagnosisObjectsData(Record):
    items: list[Record]
    total: int
    group_total: int
    page: int
    size: int
    total_pages: int


class PromotionPublicationMeta(BaseModel):
    data_version: str
    request_id: str


class Envelope[T](BaseModel):
    data: T
    meta: PromotionPublicationMeta


__all__ = [
    "ErrorEnvelope",
    "Envelope",
    "MetaData",
    "OptionsData",
    "SelectedOptionsData",
    "OverviewData",
    "DaysData",
    "DiagnosisData",
    "DiagnosisObjectsData",
]
