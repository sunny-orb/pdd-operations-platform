"""三张授权数仓表的有限投影；只接受严格业务身份、day及批准窗口。"""

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from app.modules.sales.source_policy import SHOPS

TABLES = {
    "product_daily": "bwd_rpa_pdd_product_detail_f",
    "profile_records": "base_spsjb",
    "product_promotion": "bwd_rpa_pdd_goods_report_promotion_detail_f",
}
PROFILE = dict(
    source_product_name_raw="spmc",
    source_category_raw="pl",
    source_level_raw="cpdj",
    source_operator_raw="fzyy",
    source_listed_at_raw="sjsjsj",
    source_listing_status_raw="zt1",
    source_style_codes_raw="spkh",
    source_listing_ids_official_raw="spigf",
    source_listing_ids_apparel_raw="spifs",
    source_listing_ids_outdoor_raw="spihw",
    source_listing_ids_raingear_raw="spiyj",
    source_listing_ids_luggage_raw="spixb",
    source_listing_ids_kids_footwear_raw="spitztx",
)
SHOP_PROFILE = dict(
    zip(
        ("663599807", "191467219", "799193231", "343339448", "990012831", "271470254"),
        (k for k in PROFILE if k.startswith("source_listing_ids_")),
        strict=True,
    )
)
PRODUCT_NUM = dict(
    views="product_pv",
    visitors="product_uv",
    favorites="favorite_product_users",
    buyers="transaction_users",
    orders="transaction_orders",
    units="transaction_products",
    product_revenue="transaction_amount",
    source_conversion_rate="transaction_conversion_rate",
)
AD_NUM = dict(
    spend="total_cost",
    transaction_spend="transaction_cost",
    attributed_revenue="transaction_amount",
    attributed_deals="transaction_count",
    net_attributed_revenue="net_transaction_amount",
    net_attributed_deals="net_transaction_count",
    settled_attributed_revenue="settlement_transaction_amount",
    settled_attributed_deals="settlement_transaction_count",
    impressions="impression_count",
    clicks="click_count",
    source_roi="actual_roi",
    source_net_roi="net_actual_roi",
    source_settlement_roi="settlement_roi",
    source_ctr="click_through_rate",
    source_cvr="click_conversion_rate",
)
EXTRA = dict(
    source_grouping="spu_grouping",
    promotion_name="promotion_name",
    promotion_type="promotion_type",
    bidding_method="bidding_method",
    source_deleted_label="is_deleted",
)
COMMON = ("shop_id", "shop_name", "spu_id", "spu_name", "dt", "time_granularity", "create_time")
COLUMNS = {
    "profile_records": ("__id", *PROFILE.values()),
    "product_daily": (
        "z_id",
        *COMMON,
        "relevant_dt",
        *PRODUCT_NUM.values(),
        "first_level_category",
        "second_level_category",
        "third_level_category",
    ),
    "product_promotion": ("id", *COMMON, "start_dt", *AD_NUM.values(), *EXTRA.values()),
}


def identity(shop, product):
    return str(uuid5(NAMESPACE_URL, f"pdd/{shop}/{product}"))


def text(value):
    return None if value is None else str(value)


def numeric(value, *, integer=False):
    if value is None or value == "":
        return None
    number = Decimal(str(value))
    if not number.is_finite() or integer and number != number.to_integral_value():
        raise ValueError("INVALID_NUMERIC")
    return int(number) if integer else number


def parse_ids(value):
    """纯完整数字ID或明确分隔列表；混合状态文字、逗号及科学记数不猜ID。"""
    value = str(value or "").strip()
    if not re.fullmatch(r"[0-9]+(?:\s*[/;；\n\r]\s*[0-9]+)*", value):
        return ()
    return tuple(dict.fromkeys(re.split(r"\s*[/;；\n\r]\s*", value)))


def previous_year(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return None


def windows(end):
    start = end - timedelta(days=59)
    # Feb29 has no same-calendar comparison: query broad dates, comparison maps exact dates.
    return (
        (start, end),
        (
            previous_year(start) or date(start.year - 1, 3, 1),
            previous_year(end) or date(end.year - 1, 2, 28),
        ),
    )


def fingerprint(rows):
    payload = json.dumps(
        rows, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize(code, records, end):
    """必要来源原行映射为既有v2列，拒绝源键重复/商品日双行/窗口外或非day输入。"""
    result, seen, days = [], set(), set()
    for raw in records:
        key = text(
            raw.get(
                "__id" if code == "profile_records" else "z_id" if code == "product_daily" else "id"
            )
        )
        if not key or key != key.strip() or key in seen:
            raise ValueError("SOURCE_KEY_INVALID_OR_DUPLICATE")
        seen.add(key)
        item = dict(source_row_key=key, source_record_id=key)
        if code == "profile_records":
            item.update({target: text(raw.get(source)) for target, source in PROFILE.items()})
        else:
            shop, product = text(raw.get("shop_id")), text(raw.get("spu_id"))
            if shop not in SHOPS or not product or not re.fullmatch(r"[0-9]+", product):
                raise ValueError("SOURCE_ID_INVALID")
            dt = raw.get("dt")
            dt = dt if isinstance(dt, datetime) else datetime.fromisoformat(str(dt))
            day = dt.date()
            if (
                raw.get("time_granularity") != "day"
                or dt.time() != time()
                or not any(a <= day <= b for a, b in windows(end))
            ):
                raise ValueError("SOURCE_DATE_SCOPE_INVALID")
            if code == "product_daily":
                bk = (shop, product, day)
                if bk in days:
                    raise ValueError("PRODUCT_DAY_DUPLICATE")
                days.add(bk)
            item.update(
                listing_id=identity(shop, product),
                listing_match_status="matched",
                source_shop_id=shop,
                source_shop_name=text(raw.get("shop_name")),
                source_product_id=product,
                source_product_name=text(raw.get("spu_name")),
                source_dt_raw=text(raw.get("dt")),
                date_status="parsed_label",
                source_created_time_raw=text(raw.get("create_time")),
            )
            if code == "product_daily":
                item.update(business_date=day, source_related_date_raw=text(raw.get("relevant_dt")))
                for level in ("first", "second", "third"):
                    item[f"source_{level}_category"] = text(raw.get(f"{level}_level_category"))
                item.update(
                    {
                        k: numeric(
                            raw.get(v),
                            integer=k
                            in ("views", "visitors", "favorites", "buyers", "orders", "units"),
                        )
                        for k, v in PRODUCT_NUM.items()
                    }
                )
            else:
                start = raw.get("start_dt")
                if start is None or str(start) != str(raw.get("dt")):
                    raise ValueError("AD_DAY_START_MISMATCH")
                item.update(
                    period_start=day,
                    period_end=day,
                    source_granularity="day",
                    source_start_dt_raw=text(start),
                )
                item.update({k: text(raw.get(v)) for k, v in EXTRA.items()})
                item.update({k: numeric(raw.get(v)) for k, v in AD_NUM.items()})
        result.append(item)
    return sorted(result, key=lambda row: row["source_row_key"])
