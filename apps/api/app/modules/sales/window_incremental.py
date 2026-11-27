"""销售固定日来源窗口接收：保留批准历史，不把业务日当修改游标。"""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_store import require_archive
from app.modules.database_residency import ensure_online

from .archive_replay import restore_candidate
from .contracts import SalesError, ShopDailyCandidate
from .datasets import PRODUCT_REGION_SOURCE_MAX_ROWS, SOURCE_MAX_ROWS
from .normalization import fingerprint, normalize_shop_daily
from .source_normalization import normalize_source
from .source_policy import MAX_ROWS

# 普通接收重查最新业务日；有来源证据的旧缺日按日补齐，其余历史修订由周对账发现。
LOOKBACK_DAYS = 1
SALES_INCREMENTAL_SOURCES = frozenset(
    {
        "shop_daily",
        "shop_ratings",
        "shop_channel_product_promotion",
        "shop_channel_star_shop",
        "shop_regions",
        "product_regions",
    }
)


def normalize_sales_window(code, rows, window, heartbeat=lambda: None, *, allow_empty=False):
    """允许稳定空局部读数参与双读；空候选不能直接作为完整历史发布。"""
    if allow_empty and not rows:
        return ShopDailyCandidate(
            window,
            (),
            (),
            fingerprint({"empty_window": code, "start": str(window.start), "end": str(window.end)}),
        )
    if code == "shop_daily":
        heartbeat()
        return normalize_shop_daily(rows, window)
    return normalize_source(code, rows, window, heartbeat)


def _read_windows(approved, baseline_end, repair_dates, repair_only):
    """合并相邻业务日；普通接收保留最新日及停机续接，单独补缺仅加尚未读过的尾段。"""
    start = max(approved.start, approved.end - timedelta(days=LOOKBACK_DAYS - 1))
    if repair_only:
        start = baseline_end + timedelta(days=1)
    else:
        start = min(start, baseline_end + timedelta(days=1))
    ordinary_days = {start + timedelta(days=i) for i in range((approved.end - start).days + 1)}
    days = set(repair_dates) | ordinary_days
    spans = []
    for day in sorted(days):
        if spans and day == spans[-1][1] + timedelta(days=1):
            spans[-1] = (spans[-1][0], day)
        else:
            spans.append((day, day))
    return (
        days,
        [type(approved)(first, last, approved.shop_ids) for first, last in spans],
        set(repair_dates) - ordinary_days,
    )


def _merge_window_rows(
    store, baseline, read, windows, days, gap_days, repair_dates, row_limit, heartbeat
):
    """普通日期校验后替换；历史补缺只加缺失店铺日，已有店铺日原行保留。"""
    combined, new, observed = [], {}, set()
    old_by_key = {row["source_row_key"]: row for row in baseline.rows}
    existing_shop_days = {
        (row["shop_id"], row[store.date_column])
        for row in baseline.rows
        if row[store.date_column] in gap_days
    }
    for window in windows:
        heartbeat()
        incoming = read(window)
        if incoming.window != window:
            raise SalesError("PUBLICATION_SCOPE_MISMATCH")
        for row, raw in zip(incoming.rows, incoming.raw_rows, strict=True):
            key = row["source_row_key"]
            if key in new:
                raise SalesError("SOURCE_KEY_MOVED")
            old = old_by_key.get(key)
            if old is not None and (
                (old["shop_id"], old[store.date_column]) != (row["shop_id"], row[store.date_column])
                or (
                    store.code == "shop_regions"
                    and old["source_region_name"] != row["source_region_name"]
                )
            ):
                raise SalesError("SOURCE_KEY_MOVED")
            new[key] = row
            observed.add(row[store.date_column])
            if (row["shop_id"], row[store.date_column]) not in existing_shop_days:
                combined.append(raw)
        if len(new) > row_limit:
            raise SalesError("SOURCE_ROW_LIMIT")
    if not repair_dates <= observed:
        raise SalesError("SOURCE_REPAIR_DATE_NO_RECORDS", 503)
    for index, (old, raw) in enumerate(zip(baseline.rows, baseline.raw_rows, strict=True)):
        if index % 1000 == 0:
            heartbeat()
        key = old["source_row_key"]
        if old[store.date_column] in gap_days:
            combined.append(raw)
        elif old[store.date_column] not in days:
            if key in new:
                raise SalesError("SOURCE_KEY_MOVED")
            combined.append(raw)
        elif key not in new:
            raise SalesError("SOURCE_OLD_KEY_MISSING")
    if len(combined) > row_limit:
        raise SalesError("SOURCE_ROW_LIMIT")
    return combined


def receive_sales_window(
    store,
    read,
    *,
    heartbeat=lambda: None,
    full=False,
    row_limit=None,
    repair_dates=(),
    repair_only=False,
):
    """在来源锁内稳定双读；以已核归档为基线，将指定补缺日和普通增量合成一个完整候选。"""
    if store.code not in SALES_INCREMENTAL_SOURCES:
        raise SalesError("INVALID_INCREMENTAL_DATASET")
    approved = store.approved
    repair_dates = frozenset(repair_dates)
    if any(
        type(day) is not date or not approved.start <= day <= approved.end for day in repair_dates
    ):
        raise SalesError("SOURCE_WINDOW_NOT_APPROVED")
    if (repair_dates and full) or (repair_only and not repair_dates):
        raise SalesError("INVALID_REPAIR_SCOPE")
    if row_limit is None:
        row_limit = (
            MAX_ROWS
            if store.code == "shop_daily"
            else (
                PRODUCT_REGION_SOURCE_MAX_ROWS
                if store.code == "product_regions"
                else SOURCE_MAX_ROWS
            )
        )
    with store.engine.connect() as c:
        head = (
            c.execute(
                sa.text("""
            SELECT v.id,v.retained_input_ref,v.content_fingerprint,v.producer_revision
            FROM current_datasets d
            JOIN dataset_versions v ON v.id=d.version_id
            WHERE d.dataset_code=:code AND v.state='published'
        """),
                {"code": store.code},
            )
            .mappings()
            .one_or_none()
        )
        if head:
            ensure_online(c, [head["id"]])
    if repair_dates and not head:
        raise SalesError("INCREMENTAL_BASELINE_REQUIRED")
    windows = [approved]
    if head and not full:
        source = Path(require_archive(head["retained_input_ref"]))
        for name in ("interpretation.json", "producer-source.json"):
            require_archive(source.parent / name)
        baseline = restore_candidate(
            source.parent, heartbeat=heartbeat, expected_producer=head["producer_revision"]
        )
        if baseline.fingerprint != head["content_fingerprint"]:
            raise SalesError("INCREMENTAL_BASELINE_MISMATCH")
        if (
            baseline.window.start != approved.start
            or baseline.window.shop_ids != approved.shop_ids
            or baseline.window.end > approved.end
        ):
            raise SalesError("INCREMENTAL_BASELINE_SCOPE_MISMATCH")
        days, windows, gap_days = _read_windows(
            approved, baseline.window.end, repair_dates, repair_only
        )
        # 本轮仅修复店铺地区；其他来源的覆盖粒度与分类边界沿原策略。
        if store.code != "shop_regions":
            gap_days = set()
        combined = _merge_window_rows(
            store, baseline, read, windows, days, gap_days, repair_dates, row_limit, heartbeat
        )
        del baseline
        incoming = normalize_sales_window(store.code, combined, approved, heartbeat)
    else:
        heartbeat()
        incoming = read(approved)
        if incoming.window != approved:
            raise SalesError("PUBLICATION_SCOPE_MISMATCH")
    if not incoming.rows:
        raise SalesError("SOURCE_NO_RECORDS")
    if len(incoming.rows) > row_limit:
        raise SalesError("SOURCE_ROW_LIMIT")
    metadata = {
        "incremental_baseline": str(head["id"]) if head else None,
        "receipt_mode": "full_reconciliation" if full else "lookback" if head else "bootstrap",
        "lookback_days": LOOKBACK_DAYS,
    }
    if len(windows) == 1:
        metadata.update(read_start=str(windows[0].start), read_end=str(windows[0].end))
    if repair_dates:
        metadata.update(
            receipt_mode="gap_repair",
            repair_dates=[str(day) for day in sorted(repair_dates)],
            read_windows=[[str(window.start), str(window.end)] for window in windows],
        )
    return replace(incoming, source_metadata=metadata)
