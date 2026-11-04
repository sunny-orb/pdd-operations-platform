"""封闭天气文档协议；普通文本，未识别不表示没有天气。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeatherBlock(Closed):
    regions: list[str]
    detail: str = Field(min_length=1)
    status: Literal["parsed", "unidentified", "boundary", "not_mentioned"]


class WeatherProcess(WeatherBlock):
    period: str = Field(min_length=1)


class WeatherSource(Closed):
    name: Literal["天气公报", "中期预报"]
    url: str
    issue_time: str
    text: str = Field(min_length=1)


class WeatherDocument(Closed):
    title: str = Field(min_length=1)
    issue_time: str
    focus_regions: list[str]
    today_rain: WeatherBlock
    today_temperature: WeatherBlock
    next_7_days: list[WeatherProcess]
    sources: list[WeatherSource] = Field(min_length=2, max_length=2)
    forecast_url: str | None
    reference_url: str | None
    group_card_link: None
    note: str


class PublicationSummary(Closed):
    id: str
    issue_date: str
    revision_no: int
    issue_time: str


class WeatherMeta(Closed):
    data_version: str
    status: Literal["ready", "partial", "source_unavailable"]
    default_publication_id: str | None
    publications: list[PublicationSummary]
    last_success_at: str | None
    last_attempt_at: str | None
    last_attempt_status: str | None
    error_code: str | None


class Publication(Closed):
    id: str
    issue_date: str
    revision_no: int
    document: WeatherDocument


class MetaEnvelope(Closed):
    data: WeatherMeta
    meta: WeatherMeta


class PublicationEnvelope(Closed):
    data: Publication | None
    meta: WeatherMeta
