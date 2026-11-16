"""已授权的11个历史店铺日；整行数仓优先，绝不按字段混源。"""

from dataclasses import replace
from datetime import date

import sqlalchemy as sa

from .contracts import ReadWindow, SalesError
from .datasets import HISTORY_SPEC, REFUND_BASE
from .normalization import fingerprint
from .source_normalization import normalize_source, numeric
from .source_policy import SHOPS, display_shop_name

APPROVED = (
    ("799193231", "2026-02-21", "tbl8vvB4sSYPDyLM"),
    ("799193231", "2026-02-22", "tbl8vvB4sSYPDyLM"),
    ("191467219", "2026-08-23", "tblgjdGAipop0AB2"),
    ("343339448", "2026-03-03", "tbl2JokW7lfX6gGJ"),
    ("343339448", "2026-08-21", "tbl2JokW7lfX6gGJ"),
    ("343339448", "2026-08-23", "tbl2JokW7lfX6gGJ"),
    ("990012831", "2026-08-15", None),
    ("271470254", "2026-02-21", None),
    ("271470254", "2026-02-22", None),
    ("271470254", "2026-08-20", None),
    ("271470254", "2026-08-23", None),
)
HISTORY_WINDOW = ReadWindow(date(2026, 2, 21), date(2026, 8, 23), tuple(SHOPS))


def normalize_history(rows):
    """完整11条审计输入→不可变候选；原版无净额/笔数，保持NULL。"""
    keys = [(r.get("shop_id"), r.get("date"), r.get("source_table")) for r in rows]
    if len(keys) != len(APPROVED) or set(keys) != set(APPROVED):
        raise SalesError("HISTORY_SCOPE_MISMATCH")
    raw, result = [], []
    for r in sorted(rows, key=lambda r: (r["shop_id"], r["date"])):
        table = r["source_table"]
        record = r.get("record_id")
        if table and (not isinstance(record, str) or not record.strip()):
            raise SalesError("HISTORY_RECORD_ID_MISSING")
        values = {k: numeric(r.get(k)) for k in ("spend", "revenue", "impressions", "clicks")}
        if any(v is None for v in values.values()):
            raise SalesError("HISTORY_METRIC_MISSING")
        if not table and (record is not None or any(values.values())):
            raise SalesError("HISTORY_INACTIVE_NOT_ZERO")
        if any(values[k] != values[k].to_integral_value() for k in ("impressions", "clicks")):
            raise SalesError("HISTORY_COUNT_INVALID")
        key = f"{table}/{record}" if table else f"legacy-inactive/{r['shop_id']}/{r['date']}"
        item = dict(
            id=key,
            dt=r["date"],
            time_granularity="day",
            shop_id=r["shop_id"],
            shop_name=SHOPS[r["shop_id"]][1],
            metrics=dict(
                zip(("总花费(元)", "交易额(元)", "曝光量", "点击量"), values.values(), strict=True)
            ),
        )
        normalized = normalize_source(
            "shop_channel_product_promotion", [item], HISTORY_WINDOW
        ).rows[0]
        normalized.update(
            source_record_id=record,
            source_channel_label="飞书历史补齐" if table else "原版停投确认记录",
        )
        result.append(normalized)
        raw.append(
            dict(
                shop_id=r["shop_id"], date=r["date"], source_table=table, record_id=record, **values
            )
        )
    if len({r["source_row_key"] for r in result}) != len(result):
        raise SalesError("HISTORY_DUPLICATE_RECORD")
    candidate = normalize_source("shop_channel_product_promotion", [item], HISTORY_WINDOW)
    return replace(
        candidate,
        rows=tuple(result),
        raw_rows=tuple(raw),
        fingerprint=fingerprint(dict(policy="history-v1", rows=raw)),
        source_metadata=dict(
            authorization="2026-10-30用户要求按原版完善",
            original_confirmation="2026-10-23原版声明",
            verified_on="2026-10-30",
            base=REFUND_BASE,
        ),
    )


def effective_rows(warehouse, history):
    """选整行再汇总；数仓NULL/0同样覆盖历史，其他渠道保持独立。"""

    def key(r):
        return r["shop_id"], r["business_date"], r["channel_code"]

    occupied = {key(r) for r in warehouse}
    return [*warehouse, *(r for r in history if key(r) not in occupied)]


def history_rows(connection, versions):
    """从当前响应固定的版本读取历史；版本未接入时返回空，不现场读上游。"""
    version = versions.get(HISTORY_SPEC.code)
    if isinstance(version, dict):
        version = version["id"]
    if not version:
        return []
    return [
        dict(r)
        for r in connection.execute(
            sa.text(
                "SELECT * FROM public.shop_channel_promotion_daily "
                "WHERE dataset_version_id=:v ORDER BY business_date,shop_id"
            ),
            dict(v=version),
        ).mappings()
    ]


def supplement_audit(warehouse, history, start=None, end=None, shops=None):
    """完整清单不随筛选消失，逐条给出范围外/数仓替换/生效。"""
    occupied = {(r["shop_id"], r["business_date"], r["channel_code"]) for r in warehouse}
    result = []
    for row in history:
        day = row["business_date"]
        outside = (
            (start and day < start)
            or (end and day > end)
            or (shops is not None and row["shop_id"] not in shops)
        )
        table = row["source_row_key"].split("/", 1)[0]
        link = (
            f"https://kocotree.feishu.cn/base/{REFUND_BASE}?table={table}"
            if table.startswith("tbl")
            else None
        )
        result.append(
            dict(
                shop_id=row["shop_id"],
                shop_name=display_shop_name(row["shop_id"], row["source_shop_name"]),
                date=str(day),
                source=row["source_channel_label"],
                source_url=link,
                source_record_id=row["source_record_id"],
                spend=str(row["spend"]),
                revenue=str(row["attributed_revenue"]),
                status="outside_range"
                if outside
                else "warehouse"
                if (row["shop_id"], day, row["channel_code"]) in occupied
                else "supplement",
            )
        )
    return result
