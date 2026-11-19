"""M1新版协议：金额/比率用十进制字符串；宽展字段保留领域返回的覆盖证据。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorInfo(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorInfo


class PublicationMeta(BaseModel):
    data_version: str
    request_id: str
    score_version: str | None = None


class PeriodComparison(BaseModel):
    value: str | None
    previous: str | None
    change: str | None
    reason: str | None
    message: str | None
    current_rows: int
    previous_rows: int


class OperatingMetric(Record):
    value: str | None
    status: str
    reason: str | None
    observation_note: str | None
    label: str
    type: str
    previous: str | None
    previous_period: str | None
    yoy: str | None
    mom: str | None
    yoy_comparison: PeriodComparison
    mom_comparison: PeriodComparison


class Signal(Record):
    code: str
    label: str
    weight: int
    type: str
    value: str | None
    basic: str | None
    good: str | None
    excellent: str | None
    outstanding: str | None
    points: str | None
    status: str
    reason: str


class ScoreDetail(Record):
    listing_id: str
    product_id: str
    shop_id: str
    product_name: str | None
    category: str | None
    operator: str | None
    signals: list[Signal]
    decision: str
    score: str | None
    reason: str


class MetaData(Record):
    data_version: str
    date_from: str
    date_to: str
    observed_rows: str
    product_count: str
    complete_through: str | None
    completeness: str
    profile_basis: str


class ProductAction(BaseModel):
    kind: Literal["risk", "opportunity"]
    listing_ids: list[str] = Field(max_length=3)
    text: str


class ProductActions(BaseModel):
    generation_method: Literal["rules"]
    ai_status: Literal["not_connected"]
    status: Literal["empty", "observed_reference", "insufficient_data", "no_match"]
    as_of_date: str
    message: str
    items: list[ProductAction] = Field(max_length=2)
    notices: list[str]


class BriefData(Record):
    actions: ProductActions


class HealthMetric(BaseModel):
    value: str | None
    previous: str | None
    yoy: str | None
    reason: str | None


class HealthMetrics(BaseModel):
    revenue: HealthMetric
    conversion: HealthMetric
    aov: HealthMetric


class GrowthHealth(BaseModel):
    title: str
    tone: Literal["neutral", "watch"]
    detail: str
    matched_product_days: int = Field(ge=0)
    matched_products: int = Field(ge=0)
    metrics: HealthMetrics


class ComparisonPeriod(BaseModel):
    start: date
    end: date


class ComparisonPeriods(BaseModel):
    current: ComparisonPeriod
    yoy: ComparisonPeriod | None
    mom: ComparisonPeriod


class ProductRiskAlert(BaseModel):
    label: str
    priority: Literal["P0", "P1", "P2"]
    score: int
    current: str
    baseline: str | None
    change: str | None
    type: Literal["number", "percent"]
    metric: Literal["visitors", "orders", "conversion"]
    baseline_method: Literal["daily_average", "ratio_of_totals"]
    trend: list[str | None] = Field(min_length=7, max_length=7)
    expected_loss: str | None
    reasons: list[str]
    action: str
    deadline: None
    status: Literal["未启用"]
    basis: str


class ProductRiskItem(BaseModel):
    listing_id: str
    shop_id: str
    product_id: str
    shop_name: str | None
    product_name: str | None
    category: str | None
    level: str | None
    operator: str | None
    period_revenue: str | None
    alert: ProductRiskAlert


class ProductRiskCounts(BaseModel):
    selected: int = Field(ge=0)
    excluded: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    unevaluated: int = Field(ge=0)
    flagged: int = Field(ge=0)
    unflagged: int = Field(ge=0)


class RisksData(BaseModel):
    risks: list[ProductRiskItem]
    total: int = Field(ge=0)
    counts: ProductRiskCounts
    status: Literal["empty", "not_applicable", "insufficient_data", "partial", "evaluated"]
    period: ComparisonPeriod
    evaluation_date: date
    latest_data_date: date
    baseline_period: ComparisonPeriod
    trend_period: ComparisonPeriod
    generation_method: Literal["rules"]
    rule_version: Literal["product-risk-traffic-v2"]
    impact_kind: Literal["estimated"]


class OperatingDiagnosis(BaseModel):
    generation_method: Literal["rules"]
    growth_title: str
    traffic_title: str
    action: str


class OverviewData(Record):
    comparison_periods: ComparisonPeriods
    metrics: dict[str, OperatingMetric]
    series: list[Record]
    windows: list[Record]
    health: GrowthHealth
    diagnosis: OperatingDiagnosis
    risks: list[Record]
    brief: BriefData
    funnel: list[Record]
    observed_rows: str
    product_count: str
    basis: str


class ScoresData(Record):
    total: int
    total_count: int
    scored_count: int
    items: list[ScoreDetail]
    page: int
    size: int
    status: str


class PageData(Record):
    items: list[Record]
    total: int
    page: int
    size: int


class CatalogItem(BaseModel):
    product_name: str
    value: str
    label: str
    shop: str
    operator: str | None
    category: str | None
    level: str | None
    level_group: str
    level_details: list[str]
    search_text: str


class CatalogData(BaseModel):
    items: list[CatalogItem]
    shops: list[Record]
    total: int


class OptionsData(Record):
    selected_products: list[Record]
    shops: list[Record]
    operators: list[str]
    categories: list[str]
    levels: list[str]
    products: list[Record]
    total: int


class Envelope[T](BaseModel):
    data: T
    meta: PublicationMeta
