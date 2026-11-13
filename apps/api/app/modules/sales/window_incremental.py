"""销售固定日来源窗口接收：保留批准历史，不把业务日当修改游标。"""

from dataclasses import replace
from datetime import timedelta
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

LOOKBACK_DAYS = 30
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


def receive_sales_window(store, read, *, heartbeat=lambda: None, full=False, row_limit=None):
    """在store.run来源锁内接收；read稳定双读，返回可独立重放的完整候选。

    周对账/无基线读完整批准范围；普通接收只替换回看窗口，缺键和跨日移键拒绝。
    已验证范围取归档窗口，不用最大业务日期推定齐数；断档扩大到旧窗口末尾回看。
    """
    if store.code not in SALES_INCREMENTAL_SOURCES:
        raise SalesError("INVALID_INCREMENTAL_DATASET")
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
            SELECT v.id,v.retained_input_ref,v.content_fingerprint FROM current_datasets d
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
    approved = store.approved
    window = approved
    baseline = None
    if head and not full:
        source = Path(require_archive(head["retained_input_ref"]))
        for name in ("interpretation.json", "producer-source.json"):
            require_archive(source.parent / name)
        baseline = restore_candidate(source.parent, heartbeat=heartbeat)
        if baseline.fingerprint != head["content_fingerprint"]:
            raise SalesError("INCREMENTAL_BASELINE_MISMATCH")
        if (
            baseline.window.start != approved.start
            or baseline.window.shop_ids != approved.shop_ids
            or baseline.window.end > approved.end
        ):
            raise SalesError("INCREMENTAL_BASELINE_SCOPE_MISMATCH")
        start = max(approved.start, approved.end - timedelta(days=LOOKBACK_DAYS - 1))
        if baseline.window.end < start:
            start = max(approved.start, baseline.window.end - timedelta(days=LOOKBACK_DAYS - 1))
        window = type(approved)(start, approved.end, approved.shop_ids)
    heartbeat()
    incoming = read(window)
    if incoming.window != window:
        raise SalesError("PUBLICATION_SCOPE_MISMATCH")
    if baseline is not None:
        # 同一ID不能从历史段跨入窗口，窗口内旧键也不能不明消失或改店/改日期。
        new = {r["source_row_key"]: r for r in incoming.rows}
        retained = []
        for index, (old, raw) in enumerate(zip(baseline.rows, baseline.raw_rows, strict=True)):
            if index % 1000 == 0:
                heartbeat()
            key = old["source_row_key"]
            if old[store.date_column] < window.start:
                if key in new:
                    raise SalesError("SOURCE_KEY_MOVED")
                retained.append(raw)
            elif key not in new:
                raise SalesError("SOURCE_OLD_KEY_MISSING")
            elif (old["shop_id"], old[store.date_column]) != (
                new[key]["shop_id"],
                new[key][store.date_column],
            ):
                raise SalesError("SOURCE_KEY_MOVED")
            elif (
                store.code == "shop_regions"
                and old["source_region_name"] != new[key]["source_region_name"]
            ):
                raise SalesError("SOURCE_KEY_MOVED")
        combined = [*retained, *incoming.raw_rows]
        if len(combined) > row_limit:
            raise SalesError("SOURCE_ROW_LIMIT")
        # 原投影已收集，提前释放两份规范化中间结果，避免与完整候选并存。
        del baseline, new, incoming
        incoming = normalize_sales_window(store.code, combined, approved, heartbeat)
    if not incoming.rows:
        raise SalesError("SOURCE_NO_RECORDS")
    if len(incoming.rows) > row_limit:
        raise SalesError("SOURCE_ROW_LIMIT")
    return replace(
        incoming,
        source_metadata={
            "incremental_baseline": str(head["id"]) if head else None,
            "receipt_mode": "full_reconciliation"
            if full
            else "bootstrap"
            if not head
            else "lookback",
            "read_start": str(window.start),
            "read_end": str(window.end),
            "lookback_days": LOOKBACK_DAYS,
        },
    )
