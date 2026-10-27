"""M1只读响应文档模型；十进制保持字符串，业务规则以数据契约为准。"""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

DecimalText = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$"),
    Field(description="十进制原串；金额单位元，比率为小数，ROI为倍数；不转浮点"),
]
Scalar = DecimalText | StrictInt | None


class Documented(BaseModel):
    model_config = ConfigDict(extra="allow")


class Window(Documented):
    start_date: date
    end_date: date


class Coverage(Documented):
    expected_days: int
    observed_days: int
    completeness: Literal["unknown", "partial", "complete"]
    not_imported_dates: list[date]
    missing_record_dates: list[date]
    missing_product_dates: list[dict[str, Any]] = Field(default_factory=list)


class PublicationMeta(Documented):
    """已观察日期、完整日和成功时间分开；版本不相容返回409。"""

    data_version: str
    input_batches: dict[str, str]
    profile_basis: Literal["current"]
    completeness: Literal["unknown", "partial", "complete"]
    complete_through: date | None
    missing_reasons: list[str]
    is_preview: Literal[False]
    request_id: str
    data_through: date | None = None
    last_success_at: str | None = None
    source_updated_at: str | None = None
    source_update_status: str | None = None
    sync_status: str | None = None
    last_error: dict[str, str | None] | None = None
    coverage: Coverage | None = None
    score_version: str | None = None
    model_status: str | None = None
    has_compatible_result: bool | None = None
    as_of_date: date | None = None
    computed_at: str | None = None
    last_attempt_at: str | None = None
    last_attempt_status: str | None = None


class Envelope[T](Documented):
    data: T | None
    availability: Literal["ready", "empty", "not_connected"]
    computation_state: Literal["ready", "pending", "running", "failed"]
    meta: PublicationMeta


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class ProductProfile(Documented):
    """跨店商品身份；资料缺失/冲突不删除经营事实。"""

    shop_id: str
    product_id: str
    product_name: str | None
    platform_code: str = "pdd"
    shop_name: str | None = None
    category: str | None = None
    level: str | None = None
    operator: str | None = None
    listed_date: date | None = None
    profile_match_status: str | None = None
    field_statuses: dict[str, str] = Field(default_factory=dict)


class Shop(Documented):
    shop_id: str
    shop_name: str | None


class FiltersData(Documented):
    shops: list[Shop]
    operators: list[str]
    categories: list[str]
    levels: list[str]
    products: list[ProductProfile]
    product_total: int
    page: int
    page_size: int
    recommended_window: Window
    profile_basis: str


class Decision(Documented):
    code: str
    label: str


class ScoreFiltersData(Documented):
    scope: Literal["scores"]
    as_of_date: date
    shops: list[str]
    operators: list[str]
    categories: list[str]
    decisions: list[Decision]
    products: list[ProductProfile]
    product_total: int
    page: int
    page_size: int


class OperatingMetric(Documented):
    key: str
    label: str
    value: DecimalText | None
    previous: DecimalText | None
    year_ago: DecimalText | None
    mom_change: DecimalText | None
    yoy_change: DecimalText | None
    unit: Literal["yuan", "count", "ratio"]
    status: str
    missing_reasons: list[str]
    missing_value_count: int
    observed_record_count: int


class Period(Documented):
    coverage: Coverage
    previous_coverage: Coverage
    days: int
    value: DecimalText | None
    mom_change: DecimalText | None
    window: Window
    previous_window: Window
    status: str
    missing_reasons: list[str]


class FunnelNode(Documented):
    key: str
    label: str
    value: Scalar
    status: str
    missing_reasons: list[str]


class Health(Documented):
    status: str
    title: str
    description: str


class OperatingWindows(Documented):
    current: Window
    previous: Window
    year_ago_dates: list[date | None]
    year_ago: Window | None


class OverviewData(Documented):
    metrics: list[OperatingMetric]
    periods: list[Period]
    funnel: list[FunnelNode]
    health: Health
    matched_record_count: int
    windows: OperatingWindows
    coverage: dict[str, Coverage]


class TrendPoint(Documented):
    date: date
    value: Scalar
    year_ago: Scalar = None
    year_ago_date: date | None = None


class TrendSeries(Documented):
    key: str
    product: ProductProfile
    baseline_date: date | None
    points: list[TrendPoint]


class TrendData(Documented):
    mode: Literal["year", "products", "relative"]
    metric_key: str
    points: list[TrendPoint] | None = None
    series: list[TrendSeries] | None = None
    note: str | None = None


class DayRow(ProductProfile):
    key: str
    business_date: date
    visitors: Scalar
    product_revenue: DecimalText | None
    buyer_conversion_rate: DecimalText | None
    yoy_change: DecimalText | None
    conversion_status: str


class DaysData(Documented):
    items: list[DayRow]
    total: int
    page: int
    page_size: int


class RiskItem(Documented):
    id: str
    product: ProductProfile
    priority: Literal["P0", "P1", "P2"]
    risk_title: str
    current_value: DecimalText | None
    baseline_value: DecimalText | None
    change_rate: DecimalText | None
    trend: list[Scalar]
    trend_metric: str
    period_revenue: DecimalText | None
    estimated_impact_amount: DecimalText | None
    impact_missing_reasons: list[str]
    reason_tags: list[str]
    suggested_action: str
    due_at: str | None
    handling_status: str
    priority_status: str


class AlertsData(Documented):
    items: list[RiskItem]
    total: int
    page: int
    page_size: int
    unassessed_count: int
    ranking_status: str


class CategoryRank(Documented):
    category: str | None
    label: str
    product_revenue: DecimalText | None
    change: DecimalText | None
    status: str
    missing_reasons: list[str]


class ProductRank(Documented):
    product: ProductProfile
    product_revenue: DecimalText | None
    previous_revenue: DecimalText | None
    change: DecimalText | None
    buyer_conversion_rate: DecimalText | None
    conversion_status: str
    status: str


class BriefData(Documented):
    categories: list[CategoryRank]
    unassigned_category: CategoryRank | None
    products: list[ProductRank]
    rising: list[ProductRank]
    declining: list[RiskItem]
    actions: list[str]
    action_generation_status: str
    window: Window
    previous_window: Window


class DiagnosisEvidence(Documented):
    metric_key: str
    label: str
    comparison: Literal["year_ago", "previous"]
    change: DecimalText | None
    status: str


class DiagnosisData(Documented):
    headline: str
    selected_metric: OperatingMetric
    evidence: list[DiagnosisEvidence]
    action: str
    missing_reasons: list[str]
    generation_method: Literal["rules"]
    generated_at: str
    is_causal_explanation: Literal[False]


class ScoreDecision(Decision):
    reasons: list[str]
    execution_note: str | None


class ScoreCard(ProductProfile):
    score_id: str
    as_of_date: date
    score_status: Literal["complete", "partial", "unscored"]
    verified_score: DecimalText | None
    complete_score: DecimalText | None
    verified_weight: int
    pending_weight: int
    active_signal_count: int
    pending_signal_count: int
    met_signal_count: int
    decision: ScoreDecision
    current_values: dict[str, Scalar]
    ad_impressions_7d: Scalar
    ad_spend_7d: Scalar
    model_category: str | None
    season: str | None
    lifecycle: str | None
    reference_context_basis: str
    threshold_status: str


class ScoreSummary(Documented):
    total_count: int
    filtered_count: int
    scored_count: int
    complete_count: int
    partial_count: int
    unscored_count: int


class ScoresData(Documented):
    items: list[ScoreCard]
    total: int
    page: int
    page_size: int
    as_of_date: date
    summary: ScoreSummary


class Thresholds(Documented):
    basic: DecimalText | None
    good: DecimalText | None
    excellent: DecimalText | None
    outstanding: DecimalText | None


class ScoreSignal(Documented):
    metric_key: str
    label: str
    unit: str
    weight: int
    current_value: DecimalText | None
    points: DecimalText | None
    tier: str | None
    status: str
    missing_reasons: list[str]
    reference_group: str | None
    reference_sample_count: int | None
    fallback_level: str | None
    thresholds: Thresholds


class ScoreContext(Documented):
    listed_date: date | None
    age_days: int | None
    first_product_observed_date: date | None
    age_basis: str
    lifecycle_status: str
    current_window: Window
    previous_window: Window
    coverage: dict[str, Any]
    threshold_precision: str
    evidence_reasons: list[str]


class ScoreDetail(ScoreCard):
    signals: list[ScoreSignal] = Field(min_length=10, max_length=10)
    model_id: str
    rule_version: str
    context: ScoreContext
    inputs: dict[str, Any] = Field(description="实际聚合输入/阻断原因；字段定义见数据契约3.5.1")
    threshold_provenance: dict[str, Any]


RESPONSE_MODELS = {
    "filters": Envelope[FiltersData | ScoreFiltersData],
    "overview": Envelope[OverviewData],
    "trend": Envelope[TrendData],
    "days": Envelope[DaysData],
    "brief": Envelope[BriefData],
    "alerts": Envelope[AlertsData],
    "diagnosis": Envelope[DiagnosisData],
    "scores": Envelope[ScoresData],
    "score_detail": Envelope[ScoreDetail],
}
ERROR_RESPONSES = {
    status: {"model": ErrorEnvelope, "description": description}
    for status, description in {
        404: "对象不存在",
        409: "数据/评分版本不相容；保留筛选，整组重新读取版本",
        422: "筛选参数非法或跨店商品身份不一致",
        503: "数据库/已发布来源/相容资料尚未就绪",
        500: "服务异常；按request_id排查，不返回敏感连接/SQL",
    }.items()
}
