"""六类M3来源的固定字段解释，保留原值且不推定完整日或订单归因。"""

import hashlib
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import SalesError, ShopDailyCandidate
from .datasets import EXTENDED_REVISION, PROMOTION_METRICS, REFUND_SHOPS, SPECS
from .normalization import fingerprint
from .source_policy import MISSING_MARKERS, SHOPS


def numeric(value, *, negative=False, formula=False):
    if value is None or (
        isinstance(value, str) and (not value.strip() or value.strip() in MISSING_MARKERS)
    ):
        return None
    if formula and isinstance(value, str) and value.startswith(("#DIV/0!", "#VALUE!", "#ERROR!")):
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise InvalidOperation
        result = Decimal(value)
        if not result.is_finite() or (result < 0 and not negative):
            raise InvalidOperation
        return result
    except (ValueError, InvalidOperation) as exc:
        raise SalesError("SOURCE_NUMERIC_INVALID") from exc


def source_day(raw, window, *, feishu=False):
    try:
        value = datetime.fromisoformat(str(raw))
        if feishu:
            if value.utcoffset() is None or value.utcoffset().total_seconds() != 28800:
                raise ValueError
            value = value.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        if (
            value.tzinfo
            or value.time().isoformat() != "00:00:00"
            or not window.start <= value.date() <= window.end
        ):
            raise ValueError
        return value.date()
    except (TypeError, ValueError) as exc:
        raise SalesError("SOURCE_DATE_INVALID") from exc


def normalize_source(code, rows, window, heartbeat=lambda: None):
    """必要原行→候选；重复身份/日期冲突拒绝，无字段或真实零不互换。"""
    spec = SPECS[code]
    if not rows:
        raise SalesError("SOURCE_NO_RECORDS")
    result, raw_rows, ids, business_keys = [], [], set(), set()
    for index, item in enumerate(rows):
        if index % 1000 == 0:
            heartbeat()
        refund = code == "shop_refunds"
        key = str(item.get("record_id" if refund else "id") or "")
        if not key.strip() or key != key.strip() or key in ids:
            raise SalesError("SOURCE_DUPLICATE_OR_EMPTY_ID")
        ids.add(key)
        if refund:
            if (
                item.get("平台") != "拼多多"
                or item.get("部门") != "市场部"
                or item.get("店铺") not in REFUND_SHOPS
            ):
                raise SalesError("SOURCE_REFUND_SCOPE_CHANGED")
            shop = REFUND_SHOPS[item["店铺"]]
            source_shop = None
            name, raw_date = item["店铺"], item.get("日期")
        else:
            source_shop = str(item.get("shop_id") or "")
            if source_shop not in spec.shop_ids or item.get("shop_name") != SHOPS[source_shop][1]:
                raise SalesError("SOURCE_SHOP_CONFLICT")
            if item.get("time_granularity") != "day":
                raise SalesError("SOURCE_GRANULARITY_INVALID")
            shop, name, raw_date = SHOPS[source_shop][0], item["shop_name"], item.get("dt")
        day = source_day(raw_date, window, feishu=refund)
        if refund or code in {
            "shop_ratings",
            "shop_channel_product_promotion",
            "shop_channel_star_shop",
        }:
            business_key = (shop, day)
            if business_key in business_keys:
                raise SalesError("SOURCE_DUPLICATE_BUSINESS_KEY")
            business_keys.add(business_key)
        row = dict(
            source_row_key=key,
            source_record_id=key,
            shop_id=shop,
            shop_match_status="matched",
            source_shop_id=source_shop,
            source_shop_name=name,
            date_status="parsed_label",
        )
        row[spec.date_column] = day
        if refund:
            row.update(
                source_platform=item["平台"],
                source_department=item["部门"],
                source_date_raw=str(raw_date),
                reported_refund_amount=numeric(item.get("统计退款金额")),
                source_reported_amount=numeric(item.get("统计金额")),
                source_refund_ratio=numeric(item.get("退款占比"), formula=True),
                source_modified_time_raw=None,
                source_modified_at=None,
            )
        else:
            row["source_dt_raw"] = str(raw_date)
            if code == "shop_ratings":
                row.update(
                    source_score=numeric(item.get("shop_rating_score"), negative=True),
                    source_created_time_raw=str(item["create_time"])
                    if item.get("create_time")
                    else None,
                    source_created_at=None,
                )
            else:
                row.update(
                    source_updated_time_raw=str(item["updated_at"])
                    if item.get("updated_at")
                    else None,
                    source_updated_at=None,
                )
                if spec.channel:
                    metrics = item.get("metrics") if spec.channel == "product_promotion" else item
                    if not isinstance(metrics, dict):
                        raise SalesError("SOURCE_JSON_INVALID")
                    mapping = (
                        PROMOTION_METRICS
                        if spec.channel == "product_promotion"
                        else {
                            "cost": "spend",
                            "transaction_amount": "attributed_revenue",
                            "impression_count": "impressions",
                            "click_count": "clicks",
                        }
                    )
                    row.update(
                        {field: numeric(metrics.get(source)) for source, field in mapping.items()}
                    )
                    row.update(
                        channel_code=spec.channel,
                        source_channel_label=None,
                        net_attributed_revenue=None,
                        attributed_deals=None,
                        source_created_time_raw=str(item["create_time"])
                        if item.get("create_time")
                        else None,
                        source_created_at=None,
                    )
                elif code == "shop_regions":
                    row.update(
                        source_region_name=item.get("province"),
                        **{
                            field: numeric(item.get(source))
                            for source, field in {
                                "transaction_amount": "transaction_amount",
                                "transaction_orders": "transaction_orders",
                                "transaction_users": "transaction_buyers",
                                "price_per_buyer": "source_price_per_buyer",
                                "price_per_order": "source_price_per_order",
                            }.items()
                        },
                    )
                else:
                    row.update(
                        period_end=day,
                        source_granularity="day",
                        source_region_name=item.get("province"),
                        **{
                            "source_" + field: item.get(field)
                            for field in (
                                "style_code",
                                "product_code",
                                "product_name",
                                "product_category",
                                "virtual_category",
                                "color_specification",
                            )
                        },
                        **{
                            field: numeric(item.get(field))
                            for field in (
                                "actual_shipped_qty",
                                "actual_shipped_amount",
                                "actual_returned_qty",
                                "actual_returned_amount",
                            )
                        },
                        source_refund_rate=numeric(item.get("refund_rate")),
                    )
        raw = {field: item.get(field) for field in spec.columns if field != "json_data"}
        if spec.channel == "product_promotion":
            raw["metrics"] = {key: item.get("metrics", {}).get(key) for key in PROMOTION_METRICS}
        result.append(row)
        raw_rows.append(raw)
    pairs = sorted(zip(result, raw_rows, strict=True), key=lambda pair: pair[0]["source_row_key"])
    normalized, retained = tuple(p[0] for p in pairs), tuple(p[1] for p in pairs)
    digest = fingerprint(
        dict(
            policy=EXTENDED_REVISION,
            source=asdict(spec),
            mapping=SHOPS,
            refund_shops=REFUND_SHOPS,
            normalizer_revision=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            start=str(window.start),
            end=str(window.end),
            rows=retained,
        )
    )
    return ShopDailyCandidate(window, normalized, retained, digest)
