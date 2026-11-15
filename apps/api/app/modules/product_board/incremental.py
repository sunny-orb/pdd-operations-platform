"""商品日经营和推广的有界回看；归档保存完整候选，不另存增量副本。"""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_payload import read_payload_text
from app.modules.archive_store import require_archive
from app.modules.database_residency import ensure_online

from .source import TABLES, fingerprint, windows
from .storage import FACT_TABLES, REVISION

LOOKBACK_DAYS = 30
# 同一m1-board-v2-1指纹协议的三代已发布布局：原始、内容复用、窗口增量。
# 仅接受完整的已知集合；不能以缺文件跳过校验，也不能导入执行归档代码。
PRODUCER_LAYOUTS = (
    {"storage.py", "source.py", "profile_rules.py"},
    {"storage.py", "source.py", "profile_rules.py", "fact_content.py"},
    {"storage.py", "source.py", "profile_rules.py", "fact_content.py", "incremental.py"},
)


def restore_baseline(store, code, version, heartbeat=lambda: None):
    """验证已发布归档、生成代码和指纹，仅还原原投影已有字段的SQL类型。"""
    archive = Path(require_archive(version["retained_input_ref"]))
    manifest = json.loads((archive / "manifest.json").read_text())
    path = archive / (code + ".json")
    if path.name not in manifest["files"]:
        raise ValueError("INCREMENTAL_BASELINE_MISMATCH")
    producer = {
        name: (archive / name).read_text() for name in manifest["files"] if name.endswith(".py")
    }
    if (
        producer.keys() not in PRODUCER_LAYOUTS
        or fingerprint(producer) != manifest.get("producer")
        or manifest.get("producer") != version["producer_revision"]
        or manifest.get("revision") != REVISION
    ):
        raise ValueError("INCREMENTAL_BASELINE_MISMATCH")
    end = date.fromisoformat(manifest["end"])
    if code != "profile_records" and end != version["period_end"]:
        raise ValueError("INCREMENTAL_BASELINE_SCOPE_MISMATCH")
    rows = json.loads(read_payload_text(path))
    columns = store.tables[FACT_TABLES[code]].c
    for index, row in enumerate(rows):
        if index % 1000 == 0:
            heartbeat()
        for name, value in row.items():
            if name not in columns:
                raise ValueError("INCREMENTAL_BASELINE_MISMATCH")
            if value is None:
                continue
            if isinstance(columns[name].type, sa.Date):
                row[name] = date.fromisoformat(value)
            elif isinstance(columns[name].type, sa.Numeric):
                row[name] = Decimal(str(value))
    digest = fingerprint(
        dict(
            rows=rows,
            revision=manifest["revision"],
            producer=manifest["producer"],
            end=None if code == "profile_records" else end,
            inputs=None,
        )
    )
    if digest != version["content_fingerprint"]:
        raise ValueError("INCREMENTAL_BASELINE_MISMATCH")
    return end, rows


def _days(periods):
    return {
        start + timedelta(days=i)
        for start, finish in periods
        for i in range((finish - start).days + 1)
    }


def _periods(days):
    result = []
    for day in sorted(days):
        if result and result[-1][1] + timedelta(days=1) == day:
            result[-1] = (result[-1][0], day)
        else:
            result.append((day, day))
    return tuple(result)


def _merge(code, oldrows, incoming, periods, end):
    field = "business_date" if code == "product_daily" else "period_start"
    approved = _days(windows(end, code))
    received = _days(periods)
    new = {r["source_row_key"]: r for r in incoming}
    if len(new) != len(incoming):
        raise ValueError("SOURCE_KEY_INVALID_OR_DUPLICATE")
    retained = []
    for old in oldrows:
        key = old["source_row_key"]
        if key in new and any(
            old[k] != new[key][k] for k in ("source_shop_id", "source_product_id", field)
        ):
            raise ValueError("SOURCE_KEY_IDENTITY_CHANGED")
        if old[field] not in approved:
            continue
        if old[field] in received:
            if key not in new:
                raise ValueError("SOURCE_DELETE_UNCONFIRMED")
        else:
            if key in new:
                raise ValueError("SOURCE_KEY_IDENTITY_CHANGED")
            retained.append(old)
    if any(r[field] not in received for r in incoming):
        raise ValueError("SOURCE_DATE_SCOPE_INVALID")
    combined = sorted([*retained, *incoming], key=lambda row: row["source_row_key"])
    if len(combined) > 300000:
        raise ValueError("SOURCE_ROW_LIMIT")
    if code == "product_daily" and len(
        {(r["source_shop_id"], r["source_product_id"], r[field]) for r in combined}
    ) != len(combined):
        raise ValueError("PRODUCT_DAY_DUPLICATE")
    return combined


def receive_product_board(store, reader, selected=None, full_sources=(), heartbeat=lambda: None):
    """在store.run锁内调用；逐来源覆盖求差，返回完整规范化候选及共同接收末日。

    普通读取当前30日和全部新进入批准范围的日期；断档从旧覆盖末尾回看。
    完整对账仅放宽被明确选中的来源，资料始终使用完整必要快照。
    """
    selected = tuple(TABLES) if selected is None else tuple(selected)
    if not selected or not set(selected) <= TABLES.keys() or not set(full_sources) <= TABLES.keys():
        raise ValueError("INVALID_SOURCE_SELECTION")
    with store.engine.connect() as c:
        heads = {
            r["dataset_code"]: dict(r)
            for r in c.execute(
                sa.text(
                    "SELECT v.* FROM current_datasets d "
                    "JOIN dataset_versions v ON v.id=d.version_id "
                    "WHERE d.dataset_code=ANY(:codes) AND v.state='published'"
                ),
                {"codes": list(selected)},
            ).mappings()
        }
        ensure_online(c, [v["id"] for v in heads.values()])
    store.incremental_baselines = {
        code: heads[code]["id"] if code in heads else None for code in selected
    }
    store.reconcile_sources = (set(full_sources) & set(selected)) | (
        {"profile_records"} & set(selected)
    )
    latest = reader.latest(selected)
    baseline, baseline_ends, periods = {}, {}, {}
    for code in selected:
        if code not in heads:
            continue
        old_end, oldrows = restore_baseline(store, code, heads[code], heartbeat)
        if code != "profile_records":
            field = "business_date" if code == "product_daily" else "period_start"
            # 归档末日是已核窗口，不是记录MAX；较慢源可以在更晚的共同窗口内为空。
            if any(row[field] > latest for row in oldrows):
                raise ValueError("INCREMENTAL_BASELINE_SCOPE_MISMATCH")
            baseline_ends[code] = old_end
        baseline[code] = oldrows
    end = max(latest, *baseline_ends.values()) if baseline_ends else latest
    store.incremental_receipts = {}
    for code in selected:
        if code != "profile_records":
            approved = _days(windows(end, code))
            if code not in heads or code in full_sources:
                periods[code] = _periods(approved)
            else:
                old_end = baseline_ends[code]
                start = end - timedelta(days=LOOKBACK_DAYS - 1)
                if old_end < start:
                    start = old_end - timedelta(days=LOOKBACK_DAYS - 1)
                days = approved - _days(windows(old_end, code))
                days |= approved & _days(((start, end),))
                periods[code] = _periods(days)
        store.incremental_receipts[code] = dict(
            baseline=store.incremental_baselines[code],
            mode="snapshot"
            if code == "profile_records"
            else "full_reconciliation"
            if code in full_sources
            else "bootstrap"
            if code not in heads
            else "lookback",
            read_periods=periods.get(code),
            lookback_days=None if code == "profile_records" else LOOKBACK_DAYS,
        )
        heartbeat()
    actual_end, rows = reader.read_stable(selected, periods=periods, end=end)
    if actual_end != end or set(rows) != set(selected):
        raise ValueError("SOURCE_SELECTION_MISMATCH")
    for code in selected:
        if code in baseline and code not in full_sources and code != "profile_records":
            rows[code] = _merge(code, baseline[code], rows[code], periods[code], end)
    return end, rows
