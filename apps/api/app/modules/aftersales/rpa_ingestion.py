"""RPA期间商品报表：保留原输入，数值空白规则只应用于已确认字段。"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

REVISION = "m5-rpa-period-v1"
SPECS = {
    "按退货原因": ("rpa_returns", "return_records", "j1_returns", "实退数量", "实际入库金额"),
    "销售已发货": (
        "rpa_shipped_sales",
        "product_sales_records",
        "j2_shipped_sales",
        "实发数量",
        "实发金额",
    ),
    "销售已取消": (
        "rpa_cancelled_sales",
        "product_sales_records",
        "j3_cancelled_sales",
        "销售数量",
        "销售金额",
    ),
}
IDENTITY = {
    "商品编码": "source_product_code",
    "款式编码": "source_style_code",
    "产品分类": "source_product_category",
    "虚拟分类": "source_virtual_category",
    "颜色规格": "source_color_specification",
}
TOTAL_FIELDS = (
    "实发数量",
    "实发金额",
    "销售数量",
    "销售金额",
    "实退数量",
    "实退金额",
    "退货数量",
    "退货金额",
)


@dataclass
class Group:
    kind: str
    start: date
    end: date
    source_class: str
    code: str
    table: str
    role: str
    raw: list
    rows: list


def text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("RPA_TEXT_TYPE")
    value = value.strip()
    return None if value.lower() in ("", "nan") else value


def number(row, field):
    if field not in row:
        raise ValueError("RPA_REQUIRED_COLUMN: " + field)
    value = row[field]
    if value is None or isinstance(value, str) and value.strip().lower() in ("", "nan"):
        return Decimal(0)
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError("RPA_INVALID_NUMBER: " + field) from exc
    if not result.is_finite() or len(result.as_tuple().digits) > 30 or abs(result.adjusted()) > 30:
        raise ValueError("RPA_INVALID_NUMBER: " + field)
    return result


def normalize(raw):
    """缺整个交付、非法值、重键或不相容身份阻断；不把缺源补成零。"""
    if not raw or len(raw) > 300000:
        raise ValueError("RPA_EMPTY_OR_LIMIT")
    batches = defaultdict(list)
    seen = set()
    for item in raw:
        key = str(item["id"])
        if key in seen:
            raise ValueError("RPA_DUPLICATE_SOURCE_ID")
        seen.add(key)
        start, end, kind, cl = (
            item[k] for k in ("start_date", "end_date", "time_granularity", "class")
        )
        if (
            cl not in SPECS
            or kind not in ("weekly", "monthly")
            or not isinstance(item["json_data"], dict)
        ):
            raise ValueError("RPA_SOURCE_SHAPE")
        month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if (
            start > end
            or (kind == "weekly" and (end - start).days != 29)
            or (kind == "monthly" and (start.day != 1 or end != month_end))
        ):
            raise ValueError("RPA_PERIOD")
        batches[(kind, start, end, cl)].append(item)
    periods = {k[:3] for k in batches}
    if any({k[3] for k in batches if k[:3] == period} != set(SPECS) for period in periods):
        raise ValueError("RPA_MISSING_ROLE")
    results, identities = [], {}
    for (kind, start, end, cl), originals in sorted(batches.items()):
        code, table, role, qty, amount = SPECS[cl]
        is_return = role == "j1_returns"
        name = "商品名称" if is_return else "商品简称"
        originals.sort(key=lambda r: int(r["id"]))
        rows, keys, totals = [], set(), []
        for item in originals:
            j = item["json_data"]
            if any(k not in j for k in (*IDENTITY, name)) or is_return and "退货原因" not in j:
                raise ValueError("RPA_REQUIRED_IDENTITY_COLUMN")
            dims = {target: text(j[src]) for src, target in IDENTITY.items()}
            dims["source_product_name"] = text(j[name])
            summary = not is_return and not any(dims.values())
            sku = dims["source_product_code"]
            business_key = (sku, text(j.get("退货原因"))) if is_return else sku
            if not summary and sku:
                if business_key in keys:
                    raise ValueError("RPA_DUPLICATE_BUSINESS_KEY")
                keys.add(business_key)
                identity_key = (kind, start, end, sku)
                pair = (dims["source_product_category"], dims["source_style_code"])
                previous = identities.setdefault(identity_key, pair)
                if pair != previous:
                    raise ValueError("RPA_SKU_IDENTITY_CONFLICT")
            row = dict(
                **dims,
                source_row_key=str(item["id"]),
                source_record_id=str(item["id"]),
                row_kind="total" if summary else "detail",
                shop_match_status="not_provided",
                period_start=start,
                period_end=end,
                date_status="verified_period",
            )
            if is_return:
                row.update(
                    actual_returned_qty=number(j, qty),
                    actual_inbound_amount=number(j, amount),
                    source_return_reason=text(j["退货原因"]),
                )
            else:
                row.update(source_sales_qty=number(j, qty), sales_amount=number(j, amount))
            if summary:
                totals.append(j)
            rows.append(row)
        if len(totals) > 1:
            raise ValueError("RPA_AMBIGUOUS_TOTAL")
        if totals:
            detail = [
                item["json_data"]
                for item, row in zip(originals, rows, strict=True)
                if row["row_kind"] == "detail"
            ]
            if not detail:
                raise ValueError("RPA_TOTAL_WITHOUT_DETAIL")
            with localcontext() as ctx:
                ctx.prec = 50
                for field in set(TOTAL_FIELDS) & set(totals[0]):
                    if number(totals[0], field) != sum(
                        (number(r, field) for r in detail), Decimal(0)
                    ):
                        raise ValueError("RPA_TOTAL_MISMATCH: " + field)
        results.append(Group(kind, start, end, cl, code, table, role, originals, rows))
    return results
