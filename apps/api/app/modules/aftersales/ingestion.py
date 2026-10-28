"""已授权JST加工必要投影；Q有限对账、A未通过门禁，未知不补零。"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.modules.sales.datasets import REFUND_SHOPS

TABLES = dict(
    jst_returns="dwd_trd_sys_aftersale_order_detail_f",
    jst_shipped_sales="ud_1_dsyyxskb",
    jst_cancelled_sales="ud_1_dsyyxskb",
)
COMMON = ("__id", "shop_code", "shop_name", "product_code", "product_name", "spec_name")
COLUMNS = {
    "jst_returns": COMMON
    + (
        "confirm_date",
        "aftersale_num",
        "actual_receive_amount",
        "platform_subsidy_amount",
        "reason_type",
        "aftersale_status",
        "product_aftersale_type",
    ),
    "jst_shipped_sales": COMMON + ("pay_time", "sfje", "outstock_num"),
    "jst_cancelled_sales": COMMON + ("pay_time", "sale_amount", "sale_num"),
}
NUMBERS = {
    "__id",
    "aftersale_num",
    "actual_receive_amount",
    "platform_subsidy_amount",
    "sfje",
    "outstock_num",
    "sale_amount",
    "sale_num",
}
NOTE = (
    "实退数量经有限历史样本对账，仅作观察参考；实际入库金额因补贴及倍额反例未通过门禁。"
    "当前明细不是原商品/原因汇总行；产品分类、款式编码与物流阶段未核。"
)
REVISION = "m5-jst-partial-v1"


def number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (float, bool)):
        raise ValueError("SOURCE_DECIMAL_REQUIRED")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("SOURCE_NUMBER_INVALID")
    return result


def normalize(code, raw, end):
    """必要原行→既有事实表字段；原时间取日标签，绝不复制报告期间。"""
    rows, seen = [], set()
    for item in raw:
        key = str(item["__id"])
        if not key or key in seen:
            raise ValueError("SOURCE_DUPLICATE_KEY")
        seen.add(key)
        if item["shop_name"] not in REFUND_SHOPS:
            raise ValueError("SOURCE_SHOP_OUTSIDE_SCOPE")
        day = datetime.fromisoformat(
            str(item["confirm_date" if code == "jst_returns" else "pay_time"])
        ).date()
        if not end - timedelta(days=59) <= day <= end:
            raise ValueError("SOURCE_DATE_OUTSIDE_SCOPE")
        row = dict(
            source_row_key=key,
            source_record_id=key,
            row_kind="detail",
            source_row_kind=None,
            shop_id=REFUND_SHOPS[item["shop_name"]],
            shop_match_status="matched",
            source_shop_id=str(item["shop_code"]) if item.get("shop_code") is not None else None,
            source_shop_name=item["shop_name"],
            source_style_code=None,
            source_product_code=item.get("product_code") or None,
            source_product_name=item.get("product_name") or None,
            source_color_specification=item.get("spec_name") or None,
            source_product_category=None,
            source_subcategory=None,
            source_virtual_category=None,
            source_period_label=None,
            period_start=day,
            period_end=day,
            date_status="parsed_label",
        )
        if code == "jst_returns":
            row.update(
                source_return_reason=item.get("reason_type") or None,
                source_analysis_fields=None,
                source_aftersale_type=item.get("product_aftersale_type") or None,
                source_aftersale_status=item.get("aftersale_status") or None,
                actual_returned_qty=number(item.get("aftersale_num")),
                actual_inbound_amount=None,
            )
        else:
            row.update(
                sales_amount=number(
                    item.get("sfje" if code == "jst_shipped_sales" else "sale_amount")
                ),
                source_sales_qty=number(
                    item.get("outstock_num" if code == "jst_shipped_sales" else "sale_num")
                ),
            )
        rows.append(row)
    if not rows:
        raise ValueError("SOURCE_EMPTY_REQUIRES_REVIEW")
    return rows
