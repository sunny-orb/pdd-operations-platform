"""M5精确十进制字符串协议；空值独立于真实零。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

DecimalText = Annotated[str, StringConstraints(pattern=r"^-?\d+(?:\.\d+)?$")] | None


class Source(BaseModel):
    code: str
    label: str
    locator: str | None
    status: str
    observed_from: str | None
    observed_to: str | None
    last_success_at: str | None
    last_attempt_at: str | None
    error_code: str | None
    note: str


class Meta(BaseModel):
    data_version: str | None
    status: Literal["ready", "partial", "source_unavailable"]
    default_report_id: str | None
    observed_to: str | None
    complete_through: str | None
    last_success_at: str | None
    last_attempt_at: str | None
    sources: list[Source]


class Report(BaseModel):
    id: str
    report_type: Literal["weekly", "monthly", "custom"]
    label: str
    period_start: str
    period_end: str
    generated_at: str | None
    document_url: str | None
    sheet_url: str | None


class Options(BaseModel):
    reports: list[Report]
    categories: list[str]
    note: str


class Issue(BaseModel):
    name: str
    qty: DecimalText
    share: DecimalText


class Metric(BaseModel):
    key: str
    label: str
    type: Literal["number", "currency", "percent", "points"]
    value: DecimalText
    detail: str
    note: str
    status: str


class Priority(BaseModel):
    id: str
    kind: str
    title: str
    detail: str
    verification: str


class Risk(BaseModel):
    id: str
    name: str
    amount: DecimalText
    amount_share: DecimalText
    total_refund_rate: DecimalText
    change_pp: DecimalText
    risk_score: DecimalText
    note: str


class Action(BaseModel):
    id: str
    status: str
    object: str
    amount: DecimalText
    amount_share: DecimalText
    risk_score: DecimalText
    hypothesis: str
    action: str
    verification: str
    previous_rate: DecimalText
    current_rate: DecimalText
    note: str


class Stage(BaseModel):
    name: str
    amount: DecimalText
    qty: DecimalText
    share: DecimalText
    trend: str
    status: str
    note: str


class Decision(BaseModel):
    title: str
    status: str
    rate: DecimalText
    amount: DecimalText
    amount_share: DecimalText
    change_pp: DecimalText
    note: str


class Trend(BaseModel):
    id: str
    label: str
    period_start: str
    period_end: str
    qty: DecimalText


class Insight(BaseModel):
    label: str
    title: str
    detail: str


class TargetRange(BaseModel):
    metric_code: Literal["total_refund_rate", "aftersale_refund_rate"]
    label: str
    target_id: str
    version_id: str
    lower_rate: DecimalText
    upper_rate: DecimalText
    current: DecimalText
    lower_gap_pp: DecimalText
    upper_gap_pp: DecimalText
    state: Literal[
        "metric_unavailable", "below_range", "within_range", "above_range", "scope_not_applicable"
    ]


class Overview(BaseModel):
    report: Report | None
    metrics: list[Metric]
    risk_metrics: list[Metric]
    decision: Decision
    priorities: list[Priority]
    actions: list[Action]
    stages: list[Stage]
    size_breakdown: list[Issue]
    reasons: list[Issue]
    trend: list[Trend]
    scope_note: str
    quality_note: str
    target_ranges: list[TargetRange] = Field(default_factory=list)
    core_insights: list[Insight]
    category_risks: list[Risk]
    style_risks: list[Risk]
    category_actions: list[Priority]
    style_actions: list[Priority]


class Column(BaseModel):
    key: str
    label: str
    type: Literal["text", "number", "currency", "percent", "points", "list"]


class Cell(BaseModel):
    value: str | None
    detail: str = ""
    items: list[Issue] = Field(default_factory=list)


class Row(BaseModel):
    id: str
    cells: dict[str, Cell]


class Analysis(BaseModel):
    status: str
    scope_note: str
    columns: list[Column]
    rows: list[Row]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1)


class Envelope[T](BaseModel):
    data: T
    meta: Meta
