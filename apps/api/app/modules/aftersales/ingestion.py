"""已授权JST加工必要投影；Q有限对账、A未通过门禁，未知不补零。"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.modules.sales.datasets import REFUND_SHOPS

TABLES = dict(
    jst_returns="dwd_trd_sys_aftersale_order_detail_f",
    jst_shipped_sales="ud_1_dsyyxskb",
    jst_cancelled_sales="ud_1_dsyyxskb",
)
COMMON = (
    "__id",
    "shop_code",
    "shop_name",
    "product_code",
    "spec_code",
    "product_name",
    "spec_name",
)
MASTER_TABLE = "std_api_jstbz_product_spec_info_f"
MASTER_COLUMNS = (
    "__id",
    "data_source",
    "product_code",
    "spec_code",
    "product_type",
    "suite_code",
    "first_category",
    "second_category",
    "dt",
    "start_date",
    "end_date",
)
COLUMNS = {
    "jst_returns": COMMON
    + (
        "confirm_date",
        "aftersale_num",
        "apply_aftersale_num",
        "actual_receive_amount",
        "platform_subsidy_amount",
        "reason_type",
        "aftersale_status",
        "aftersale_type",
        "product_aftersale_type",
    ),
    "jst_shipped_sales": COMMON + ("pay_time", "sfje", "outstock_num"),
    "jst_cancelled_sales": COMMON + ("pay_time", "sale_amount", "sale_num"),
}
NUMBERS = {
    "__id",
    "aftersale_num",
    "apply_aftersale_num",
    "actual_receive_amount",
    "platform_subsidy_amount",
    "sfje",
    "outstock_num",
    "sale_amount",
    "sale_num",
}
NOTE = (
    "实退数量经有限历史样本对账，仅作观察参考；实际入库金额因补贴及倍额反例未通过门禁。"
    "product_code为JST款式编码，spec_code为SKU；按当前JST普通SKU资料核对款/SKU并取得一二分类，"
    "非历史分类，不混入旧手工或虚拟分类。组合/未知保留事实及空分类；重复或跨款SKU资料拒绝。"
    "原售后分类中的拒收仅作来源标记，签收阶段未核；当前明细不是原商品/原因汇总行。"
)
REVISION = "m5-jst-current-sku-v2"


def number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (float, bool)):
        raise ValueError("SOURCE_DECIMAL_REQUIRED")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("SOURCE_NUMBER_INVALID")
    return result


def source_code(value):
    """保留来源编码及前导零；缺失不借款号、规格名补SKU。"""
    if value is None or value == "":
        return None
    if isinstance(value, (float, bool)):
        raise ValueError("SOURCE_STRING_ID_REQUIRED")
    return str(value)


def enrich_current_master(raw, master, snapshot):
    """事实与当前普通SKU资料1:1核验；返回含原资料证据的raw，不展开组合或改写度量。"""
    snapshot = str(snapshot)
    requested = {source_code(row.get("spec_code")) for row in raw} - {None}
    by_sku = {}
    for item in master:
        sku = source_code(item.get("spec_code"))
        if (
            sku not in requested
            or item.get("product_type") != "普通商品"
            or source_code(item.get("suite_code")) is not None
        ):
            continue
        if str(item.get("dt")) != snapshot:
            raise ValueError("SOURCE_CURRENT_MASTER_SNAPSHOT_MISMATCH")
        style = source_code(item.get("product_code"))
        if style is None:
            raise ValueError("SOURCE_CURRENT_MASTER_KEY_MISSING")
        if sku in by_sku:
            if source_code(by_sku[sku].get("product_code")) != style:
                raise ValueError("SOURCE_CURRENT_MASTER_SKU_CONFLICT")
            raise ValueError("SOURCE_CURRENT_MASTER_DUPLICATE_KEY")
        by_sku[sku] = {column: item.get(column) for column in MASTER_COLUMNS}
    enriched = []
    for item in raw:
        style, sku = source_code(item.get("product_code")), source_code(item.get("spec_code"))
        record = by_sku.get(sku)
        if style is None or sku is None:
            status = "source_key_missing"
        elif record is None:
            status = "not_found"
        elif source_code(record["product_code"]) != style:
            status = "identity_mismatch"
        else:
            status = "matched"
        enriched.append(
            dict(
                item,
                jst_current_master_source="cubeappdata." + MASTER_TABLE,
                jst_current_snapshot_date=snapshot,
                jst_current_mapping_status=status,
                jst_current_master_record=record,
            )
        )
    return enriched


def current_categories(item):
    """只消费同一款/SKU的当前普通资料；未匹配行保留空分类。"""
    if item.get("jst_current_mapping_status") != "matched":
        return None, None
    master = item.get("jst_current_master_record") or {}
    if (
        source_code(master.get("product_code")) != source_code(item.get("product_code"))
        or source_code(master.get("spec_code")) != source_code(item.get("spec_code"))
        or master.get("product_type") != "普通商品"
        or source_code(master.get("suite_code")) is not None
        or str(master.get("dt")) != item.get("jst_current_snapshot_date")
    ):
        raise ValueError("SOURCE_CURRENT_MASTER_IDENTITY_MISMATCH")
    return master.get("first_category") or None, master.get("second_category") or None


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
        category, subcategory = current_categories(item)
        row = dict(
            source_row_key=key,
            source_record_id=key,
            row_kind="detail",
            source_row_kind=None,
            shop_id=REFUND_SHOPS[item["shop_name"]],
            shop_match_status="matched",
            source_shop_id=str(item["shop_code"]) if item.get("shop_code") is not None else None,
            source_shop_name=item["shop_name"],
            source_style_code=source_code(item.get("product_code")),
            source_product_code=source_code(item.get("spec_code")),
            source_product_name=item.get("product_name") or None,
            source_color_specification=item.get("spec_name") or None,
            source_product_category=category,
            source_subcategory=subcategory,
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
                source_aftersale_type=item.get("aftersale_type") or None,
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
