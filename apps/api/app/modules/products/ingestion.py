"""M1来源原值规范化；不裁决退款、资料历史归属或评分阈值。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.modules.products.profile_rules import parse_ids

SHOPS = {
    "spigf": ("663599807", "kocotree官方旗舰店"),
    "spifs": ("191467219", "kocotree儿童服饰旗舰店"),
    "spihw": ("799193231", "kocotree运动户外旗舰店"),
    "spiyj": ("343339448", "kocotree旗舰店"),
    "spixb": ("990012831", "kocotree母婴玩具旗舰店"),
    "spitztx": ("271470254", "kocotree童装童鞋旗舰店"),
}
SOURCE_IDS = {
    "product_daily": "warehouse_bwd_product",
    "promotion_records": "warehouse_bwd_promotion",
    "product_profile_source": "warehouse_w01",
    "shop_identity": "warehouse_w01",
}
COUNTS = dict(
    visitors="product_uv",
    views="product_pv",
    favorites="favorite_product_users",
    buyers="transaction_users",
    orders="transaction_orders",
    units="transaction_products",
)
PRODUCT_NUMBERS = dict(
    product_revenue="transaction_amount", source_conversion_rate="transaction_conversion_rate"
)
AD_NUMBERS = dict(
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
    source_click_through_rate="click_through_rate",
    source_click_conversion_rate="click_conversion_rate",
)
COLUMNS = {
    "product_daily": [
        "z_id",
        "dt",
        "relevant_dt",
        "time_granularity",
        "shop_id",
        "shop_name",
        "spu_id",
        "spu_name",
        *COUNTS.values(),
        *PRODUCT_NUMBERS.values(),
        "create_time",
    ],
    "promotion_records": [
        "id",
        "dt",
        "start_dt",
        "time_granularity",
        "shop_id",
        "shop_name",
        "spu_id",
        "spu_name",
        "promotion_name",
        "spu_grouping",
        "promotion_type",
        "bidding_method",
        "is_deleted",
        *AD_NUMBERS.values(),
        "create_time",
    ],
    "product_profile_source": [
        "__id",
        "spkh",
        "spmc",
        "pl",
        "cpdj",
        "fzyy",
        "sjsjsj",
        "zt1",
        *SHOPS,
    ],
}
DATASETS = ("product_daily", "promotion_records", "product_profile_source", "shop_identity")


class IngestionError(ValueError):
    """只携带安全错误代码，不携带密码、完整经营行或查询参数。"""


def serializable(value):
    """将必要投影变为可存JSONB的值；不写文件快照。"""
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return value


def digest(value) -> str:
    encoded = json.dumps(
        serializable(value),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def initial_windows(as_of: date) -> list[tuple[date, date]]:
    start = as_of - timedelta(days=59)
    try:
        previous = (start.replace(year=start.year - 1), as_of.replace(year=as_of.year - 1))
    except ValueError:
        raise IngestionError("LEAP_DATE_POLICY_PENDING") from None
    if (previous[1] - previous[0]).days != 59:
        raise IngestionError("LEAP_DATE_POLICY_PENDING")
    return [(start, as_of), previous]


def identifier(value) -> str:
    if value is None or isinstance(value, (float, bool)):
        raise IngestionError("INVALID_IDENTIFIER")
    result = str(value)
    if not result.strip() or result != result.strip():
        raise IngestionError("INVALID_IDENTIFIER")
    return result


def decimal_value(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise IngestionError("INVALID_DECIMAL") from None
    if not number.is_finite():
        raise IngestionError("NONFINITE_DECIMAL")
    return number


def timestamp(value, *, required=False):
    if value is None:
        if required:
            raise IngestionError("MISSING_SOURCE_DATE")
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        raise IngestionError("INVALID_SOURCE_DATE") from None
    if result.tzinfo is not None:
        raise IngestionError("SOURCE_TIMEZONE_UNCONFIRMED")
    return result


@dataclass
class PreparedBatch:
    rows: dict[str, list[dict]]
    links: list[dict]
    hashes: dict[str, str]
    windows: list[tuple[date, date]]
    raw: dict[str, list[dict]]


def prepare(raw: dict[str, list[dict]], windows: list[tuple[date, date]]) -> PreparedBatch:
    """校验批准范围和键，转换必要原值；多资料不产生唯一运营归属。"""
    if set(raw) != {"product_daily", "promotion_records", "product_profile_source"}:
        raise IngestionError("UNEXPECTED_DATASETS")
    if not windows or any(b < a for a, b in windows):
        raise IngestionError("INVALID_WINDOWS")
    rows = {k: [] for k in DATASETS}
    links = []
    hashes = {}
    allowed = {value[0] for value in SHOPS.values()}
    for dataset, records in raw.items():
        seen = set()
        for source in records:
            row_hash = digest(source)
            if dataset == "product_profile_source":
                identity = identifier(source.get("__id"))
                row = {
                    "source_profile_id": identity,
                    "source_style_code": source.get("spkh"),
                    "product_name": source.get("spmc"),
                    "category": source.get("pl"),
                    "level": source.get("cpdj"),
                    "operator": source.get("fzyy"),
                    "source_listed_date": serializable(source.get("sjsjsj")),
                    "source_listing_status": source.get("zt1"),
                    "source_product_ids": {col: source.get(col) for col in SHOPS},
                }
                for col, (shop, _) in SHOPS.items():
                    value = source.get(col)
                    for product_id in parse_ids(value):
                        links.append(
                            dict(
                                source_id=SOURCE_IDS[dataset],
                                source_profile_id=identity,
                                source_column=col,
                                platform_code="pdd",
                                shop_id=shop,
                                product_id=product_id,
                            )
                        )
                row_key = (identity,)
            else:
                shop = identifier(source.get("shop_id"))
                product = identifier(source.get("spu_id"))
                dt = timestamp(source.get("dt"), required=True)
                day = dt.date()
                if shop not in allowed or not any(a <= day <= b for a, b in windows):
                    raise IngestionError("OUTSIDE_APPROVED_SCOPE")
                if source.get("time_granularity") != "day":
                    raise IngestionError("ROLLING_WINDOW")
                source_id = identifier(source.get("z_id" if dataset == "product_daily" else "id"))
                row = dict(
                    platform_code="pdd",
                    shop_id=shop,
                    product_id=product,
                    business_date=day,
                    source_window="day",
                    source_record_id=source_id,
                    source_dt=dt,
                    source_created_at=timestamp(source.get("create_time")),
                    source_product_name=source.get("spu_name"),
                    source_shop_name=source.get("shop_name"),
                )
                mapping = PRODUCT_NUMBERS if dataset == "product_daily" else AD_NUMBERS
                row.update({name: decimal_value(source.get(col)) for name, col in mapping.items()})
                row["source_numeric_text"] = {
                    col: serializable(source.get(col)) for col in mapping.values()
                }
                row["quality_flags"] = []
                if dataset == "product_daily":
                    row["source_relevant_dt"] = timestamp(source.get("relevant_dt"))
                    for name, col in COUNTS.items():
                        value = source.get(col)
                        if value is not None and (
                            isinstance(value, bool) or not isinstance(value, int)
                        ):
                            raise IngestionError("NONINTEGER_SOURCE_COUNT")
                        row[name] = value
                    if row["visitors"] is not None and row["buyers"] is not None:
                        if row["buyers"] > row["visitors"]:
                            row["quality_flags"].append("buyers_exceed_visitors")
                    row_key = (shop, product, day)
                else:
                    row["source_start_dt"] = timestamp(source.get("start_dt"), required=True)
                    if row["source_start_dt"].date() != day:
                        raise IngestionError("INVALID_DAY_START")
                    row.update(
                        {
                            name: source.get(name)
                            for name in (
                                "promotion_name",
                                "spu_grouping",
                                "promotion_type",
                                "bidding_method",
                                "is_deleted",
                            )
                        }
                    )
                    row_key = (source_id,)
                if any(
                    value is not None and value < 0
                    for name, value in row.items()
                    if name in set(mapping) | set(COUNTS)
                ):
                    row["quality_flags"].append("negative_source_value")
                if any(row[name] is None for name in mapping):
                    row["quality_flags"].append("missing_source_measure")
            if row_key in seen:
                raise IngestionError("DUPLICATE_SOURCE_KEY")
            seen.add(row_key)
            row.update(source_id=SOURCE_IDS[dataset], row_hash=row_hash)
            rows[dataset].append(row)
        hashes[dataset] = digest(
            {
                "rows": sorted(row["row_hash"] for row in rows[dataset]),
                "windows": windows if dataset != "product_profile_source" else [],
            }
        )
    rows["shop_identity"] = [
        dict(
            source_id=SOURCE_IDS["shop_identity"],
            source_shop_key=col,
            platform_code="pdd",
            shop_id=shop,
            shop_name=name,
        )
        for col, (shop, name) in SHOPS.items()
    ]
    hashes["shop_identity"] = digest(rows["shop_identity"])
    return PreparedBatch(rows, links, hashes, windows, raw)
