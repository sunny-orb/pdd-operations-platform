"""首链值对象；日期为来源标签，金额不经过二进制浮点。"""

from dataclasses import dataclass
from datetime import date
from typing import Any


class SalesError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class ReadWindow:
    start: date
    end: date
    shop_ids: tuple[str, ...]

    def __post_init__(self):
        from .source_policy import APPROVED_START

        self._validate(APPROVED_START)

    def _validate(self, earliest):
        from .source_policy import FISCAL_END, SHOPS

        if not earliest <= self.start <= self.end <= FISCAL_END:
            raise SalesError("INVALID_WINDOW")
        if self.shop_ids != tuple(SHOPS):
            raise SalesError("INVALID_SHOP_SCOPE")


class PromotionReadWindow(ReadWindow):
    """仅两张店铺推广源获准读取上一财年；读取入口另核验来源身份。"""

    def __post_init__(self):
        from .source_policy import PROMOTION_START

        self._validate(PROMOTION_START)


@dataclass(frozen=True)
class ShopDailyCandidate:
    window: ReadWindow
    rows: tuple[dict[str, Any], ...]
    raw_rows: tuple[dict[str, Any], ...]
    fingerprint: str
    source_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PublishResult:
    run_id: str
    version_id: str | None
    reused: bool
    status: str
