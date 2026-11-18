"""固定必要投影与规范化；不猜完整日、退款或来源时区。"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .contracts import ReadWindow, SalesError, ShopDailyCandidate
from .normalization_compat import normalizer_revision
from .source_policy import CORE, METRICS, MISSING_MARKERS, POLICY_REVISION, SHOP_NAME_ALIASES, SHOPS


def serializable(value):
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    raise TypeError(type(value).__name__)


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=serializable, separators=(",", ":")
    )


def fingerprint(value) -> str:
    """与canonical逐字节相同，逐片摘要避免完整JSON字符串及UTF-8副本并存。"""
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, default=serializable, separators=(",", ":")
    )
    for piece in encoder.iterencode(value):
        digest.update(piece.encode())
    return digest.hexdigest()


def normalize_shop_daily(rows: list[dict], window: ReadWindow) -> ShopDailyCandidate:
    """输入仅必要投影；核心错误阻断候选，非核心空值保持NULL并保留原值。"""
    if not rows:
        raise SalesError("SOURCE_NO_RECORDS")
    normalized, raw, ids, keys = [], [], set(), set()
    for item in rows:
        record_id, source_shop = str(item.get("id") or ""), str(item.get("shop_id") or "")
        if not record_id or record_id.strip() != record_id or source_shop not in SHOPS:
            raise SalesError("SOURCE_IDENTITY_INVALID")
        if item.get("time_granularity") != "day":
            raise SalesError("SOURCE_GRANULARITY_INVALID")
        try:
            dt = datetime.fromisoformat(str(item["dt"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise SalesError("SOURCE_DATE_INVALID") from exc
        if (
            dt.time().isoformat() != "00:00:00"
            or dt.tzinfo
            or not window.start <= dt.date() <= window.end
        ):
            raise SalesError("SOURCE_DATE_INVALID")
        if record_id in ids or (source_shop, dt.date()) in keys:
            raise SalesError("SOURCE_DUPLICATE_KEY")
        ids.add(record_id)
        keys.add((source_shop, dt.date()))
        metrics = item.get("metrics")
        if not isinstance(metrics, dict):
            raise SalesError("SOURCE_JSON_INVALID")
        name = item.get("shop_name")
        if name not in SHOP_NAME_ALIASES[source_shop]:
            raise SalesError("SOURCE_SHOP_NAME_CONFLICT")
        row = dict(
            source_record_id=record_id,
            source_row_key=record_id,
            shop_id=SHOPS[source_shop][0],
            shop_match_status="matched",
            source_shop_id=source_shop,
            source_shop_name=name,
            source_dt_raw=str(item["dt"]),
            business_date=dt.date(),
            date_status="parsed_label",
            source_updated_time_raw=str(item["updated_at"]) if item.get("updated_at") else None,
            source_updated_at=None,
        )
        for key, field in METRICS.items():
            value = metrics.get(key)
            if value is None or (
                isinstance(value, str) and (not value.strip() or value.strip() in MISSING_MARKERS)
            ):
                if key in CORE:
                    raise SalesError("SOURCE_CORE_FIELD_MISSING")
                row[field] = None
                continue
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
                    raise InvalidOperation
                number = Decimal(value)
                if not number.is_finite() or number < 0:
                    raise InvalidOperation
                if key == "成交金额" and number != number.quantize(Decimal("0.01")):
                    raise InvalidOperation
                if key in CORE[1:] and number != number.to_integral_value():
                    raise InvalidOperation
            except (InvalidOperation, ValueError) as exc:
                raise SalesError("SOURCE_NUMERIC_INVALID") from exc
            row[field] = number
        normalized.append(row)
        raw.append(
            {
                **{
                    k: str(item[k]) if item.get(k) is not None else None
                    for k in ("id", "dt", "time_granularity", "shop_id", "shop_name", "updated_at")
                },
                "metrics": {k: metrics.get(k) for k in METRICS},
            }
        )
    pairs = sorted(zip(normalized, raw, strict=True), key=lambda pair: pair[0]["source_row_key"])
    result_rows, raw_rows = tuple(p[0] for p in pairs), tuple(p[1] for p in pairs)
    content = dict(
        policy=POLICY_REVISION,
        mapping=SHOPS,
        metrics=METRICS,
        normalizer_revision=normalizer_revision(Path(__file__)),
        start=str(window.start),
        end=str(window.end),
        rows=raw_rows,
    )
    return ShopDailyCandidate(window, result_rows, raw_rows, fingerprint(content))
