"""对话里的商品引用。名称只用于在本轮风险证据中消歧，后续查数使用链接身份。"""

import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import board_tools

PRODUCT_CLARIFICATION = (
    "上轮没有唯一确定是哪一款。请告诉我商品名称或 ID，也可以从商品行点“分析这款”。"
)


class RiskReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: Annotated[str, Field(min_length=1, max_length=160)]
    shop_id: Annotated[str, Field(min_length=1, max_length=160)]
    data_version: Annotated[str, Field(min_length=1, max_length=160)]
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def ordered_period(self):
        if self.date_from > self.date_to:
            raise ValueError("商品引用的起止日期无效")
        return self


def refers_to_product(question: str) -> bool:
    """识别须承接一个对象的指代。带名称或 ID 的新商品仍由找商品工具处理。"""
    return bool(
        re.search(
            r"(?:刚才|刚刚|上面|前面|上[一轮条]|你(?:点名|推荐|提到))[^，。！？、]{0,16}?"
            r"(?:[这那](?:一)?款|的(?:商品|款))"
            r"|(?:这|那|该)(?:一?款|个商品|件商品)(?=的|具体|近|同期|触发|[，。！？?]|$)",
            question,
        )
    )


def needs_product_clarification(question: str) -> bool:
    """只提前截住明确追问上轮那一款的单一问题；新给的线索和其他子问题交给 Agent。"""
    return (
        bool(re.match(r"\s*(?:刚才|刚刚|上面|前面|上[一轮条]|你(?:点名|推荐|提到))", question))
        and refers_to_product(question)
        and not re.search(
            r"商品\s*(?:ID|编号)|[「“《]|另外|同时|顺便|还(?:想|要|查|看)|如果|假设",
            question,
            re.I,
        )
    )


def reference_from_answer(reports: list[dict], answer: str) -> RiskReference | None:
    risks = [r for r in reports if r.get("capability") == "products_risks" and r.get("tables")]
    if not risks:
        return None
    item = board_tools.risk_product_reference(reports, answer)
    if not item:
        return None
    # 同一款可在多个期间或翻页中出现。窗口不唯一时请用户重新指定。
    windows = {
        (r["data_version"], r["period"]["date_from"], r["period"]["date_to"])
        for r in risks
        if r.get("data_version")
        and r.get("period")
        and any(
            row.get("listing_id") == item["listing_id"] and row.get("shop_id") == item["shop_id"]
            for table in r["tables"]
            for row in table["rows"]
        )
    }
    if len(windows) != 1:
        return None
    version, start, end = windows.pop()
    return RiskReference(
        listing_id=item["listing_id"],
        shop_id=item["shop_id"],
        data_version=version,
        date_from=start,
        date_to=end,
    )
