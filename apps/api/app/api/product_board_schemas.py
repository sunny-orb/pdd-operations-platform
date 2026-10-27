"""M1新版协议：金额/比率用十进制字符串；宽展字段保留领域返回的覆盖证据。"""

from pydantic import BaseModel, ConfigDict


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


class OperatingMetric(Record):
    value: str | None
    status: str
    reason: str | None
    label: str
    type: str
    previous: str | None
    previous_period: str | None
    yoy: str | None
    mom: str | None


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


class OverviewData(Record):
    metrics: dict[str, OperatingMetric]
    series: list[Record]
    windows: list[Record]
    health: Record
    risks: list[Record]
    brief: Record
    funnel: list[Record]
    observed_rows: str
    product_count: str
    basis: str


class ScoresData(Record):
    total: int
    items: list[ScoreDetail]
    page: int
    size: int
    status: str


class PageData(Record):
    items: list[Record]
    total: int
    page: int
    size: int


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
