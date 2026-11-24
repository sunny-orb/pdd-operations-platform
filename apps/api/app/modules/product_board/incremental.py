"""商品日经营和推广的有界回看；归档保存完整候选，不另存增量副本。"""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_payload import read_payload_text
from app.modules.archive_store import require_archive
from app.modules.database_residency import ensure_online
from app.modules.sales.source_policy import SHOPS

from .source import TABLES, fingerprint, windows
from .storage import FACT_TABLES, REVISION

# 普通接收重查最新业务日，旧日修订交给周对账；新进入的同比日期另行补读。
LOOKBACK_DAYS = 1
DAY_FIELDS = {"product_daily": "business_date", "product_promotion": "period_start"}
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


def missing_product_shop_days(connection, code, days):
    """查当前已发布版本的指定店铺日，返回未观察到行的店铺；缺行不推断缺数。"""
    if code not in DAY_FIELDS:
        raise ValueError("INVALID_SOURCE_SELECTION")
    head = connection.execute(
        sa.text(
            "SELECT v.id,v.period_end FROM public.current_datasets d "
            "JOIN public.dataset_versions v ON v.id=d.version_id "
            "WHERE d.dataset_code=:code AND v.state='published'"
        ),
        {"code": code},
    ).first()
    if not head:
        return {}
    days = sorted(set(days) & _days(windows(head.period_end, code)))
    if not days:
        return {}
    ensure_online(connection, [head.id])
    field, table = DAY_FIELDS[code], FACT_TABLES[code]
    # 共享表示的事实行以块ID定位；先按当前版本和日期桶筛选，避免读全版共享事实。
    observed = set(
        connection.execute(
            sa.text(
                f"SELECT {field},source_shop_id FROM public.{table} "
                f"WHERE dataset_version_id=:version AND {field}=ANY(:days) "
                "UNION "
                f"SELECT f.{field},f.source_shop_id FROM public.fact_content_members m "
                f"JOIN public.shared_{table} f ON f.block_id=m.block_id "
                "WHERE m.version_id=:version AND left(m.bucket,10)=ANY(:labels) "
                f"AND f.{field}=ANY(:days)"
            ),
            {"version": head.id, "days": days, "labels": [day.isoformat() for day in days]},
        )
    )
    gaps = {}
    for day in days:
        missing = tuple(shop for shop in SHOPS if (day, shop) not in observed)
        if missing:
            gaps[day] = missing
    return gaps


def _merge(code, oldrows, incoming, periods, end, repair_days=()):
    field = DAY_FIELDS[code]
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
                if old[field] not in repair_days:
                    raise ValueError("SOURCE_DELETE_UNCONFIRMED")
                retained.append(old)
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


def receive_product_board(
    store,
    reader,
    selected=None,
    full_sources=(),
    heartbeat=lambda: None,
    *,
    repair_days=None,
    repair_only=False,
):
    """在store.run锁内调用；逐来源覆盖求差，返回完整规范化候选及共同接收末日。

    普通读取最新业务日和全部新进入批准范围的日期；断档补齐旧覆盖之后的日期。
    完整对账仅放宽被明确选中的来源。补收日保留上游暂缺的旧键，独立补收固定当前窗口。
    """
    selected = tuple(TABLES) if selected is None else tuple(selected)
    if not selected or not set(selected) <= TABLES.keys() or not set(full_sources) <= TABLES.keys():
        raise ValueError("INVALID_SOURCE_SELECTION")
    repair_days = {code: set(days) for code, days in (repair_days or {}).items()}
    if not repair_days.keys() <= set(selected) & DAY_FIELDS.keys():
        raise ValueError("INVALID_SOURCE_SELECTION")
    if repair_only and (
        set(selected) != repair_days.keys()
        or not all(repair_days.values())
        or set(selected) & set(full_sources)
    ):
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
    if repair_only:
        if heads.keys() != set(selected):
            raise ValueError("SOURCE_INPUT_MISSING")
        ends = {v["period_end"] for v in heads.values()}
        if len(ends) != 1:
            raise ValueError("INCREMENTAL_BASELINE_SCOPE_MISMATCH")
        latest = ends.pop()
    else:
        latest = reader.latest(selected)
    baseline, baseline_ends, periods = {}, {}, {}
    for code in selected:
        if code not in heads:
            continue
        old_end, oldrows = restore_baseline(store, code, heads[code], heartbeat)
        if code != "profile_records":
            field = DAY_FIELDS[code]
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
            additions = repair_days.get(code, set())
            expired = additions - approved
            if expired:
                if (
                    repair_only
                    or code not in baseline_ends
                    or not expired <= _days(windows(baseline_ends[code], code))
                ):
                    raise ValueError("SOURCE_DATE_SCOPE_INVALID")
                # 探测时仍在旧窗口的日期可能随正常接收退出；不为它扩大新的来源范围。
                additions = repair_days[code] = additions & approved
            if repair_only:
                periods[code] = _periods(additions)
            elif code not in heads or code in full_sources:
                periods[code] = _periods(approved)
            else:
                old_end = baseline_ends[code]
                start = end - timedelta(days=LOOKBACK_DAYS - 1)
                if old_end < start:
                    start = old_end + timedelta(days=1)
                days = approved - _days(windows(old_end, code))
                days |= approved & _days(((start, end),))
                periods[code] = _periods(days | additions)
        store.incremental_receipts[code] = dict(
            baseline=store.incremental_baselines[code],
            mode="snapshot"
            if code == "profile_records"
            else "full_reconciliation"
            if code in full_sources
            else "bootstrap"
            if code not in heads
            else "gap_repair"
            if repair_only
            else "lookback",
            read_periods=periods.get(code),
            repair_days=sorted(repair_days.get(code, ())),
            lookback_days=None if repair_only or code == "profile_records" else LOOKBACK_DAYS,
        )
        heartbeat()
    actual_end, rows = reader.read_stable(selected, periods=periods, end=end)
    if actual_end != end or set(rows) != set(selected):
        raise ValueError("SOURCE_SELECTION_MISMATCH")
    for code in selected:
        if code in baseline and code not in full_sources and code != "profile_records":
            rows[code] = _merge(
                code, baseline[code], rows[code], periods[code], end, repair_days.get(code, ())
            )
    return end, rows
