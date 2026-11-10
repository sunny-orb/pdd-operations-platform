"""将已授权、已实读的网页时间登记为平台证据；不触发上游或认定同步成功。"""

from datetime import datetime

import sqlalchemy as sa

from app.modules.data_status.catalog import SOURCES, WAREHOUSE_PAGES
from app.modules.data_status.timeliness import merge_update_timing


def record_web_updates(engine, rows, checked_at):
    """整批校验固定表/网页ID/原样时间；事务拒绝旧核对批次，保留所有自动检查状态。"""
    if not isinstance(checked_at, datetime) or checked_at.utcoffset() is None:
        raise ValueError("WEB_CHECK_TIME_REQUIRES_TIMEZONE")
    tables = {s.locator.removeprefix("cubeappdata."): s for s in SOURCES if s.system == "warehouse"}
    values, seen = [], set()
    for row in rows:
        source = tables.get(row.get("table"))
        if not source or source.id in seen or row.get("tableId") != WAREHOUSE_PAGES[source.id][1]:
            raise ValueError("WEB_SOURCE_IDENTITY_MISMATCH")
        raw = row.get("updated", "")
        if datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S") != raw:
            raise ValueError("INVALID_WEB_UPDATE_TIME")
        seen.add(source.id)
        values.append(dict(code=source.id, updated=raw, checked=checked_at))
    with engine.begin() as c:
        if not c.scalar(
            sa.text("SELECT pg_try_advisory_xact_lock(hashtext('project-source-sync'))")
        ):
            raise ValueError("SOURCE_CHECK_IN_PROGRESS")
        for item in values:
            prior = (
                c.execute(
                    sa.text("SELECT * FROM public.source_checkpoints WHERE source_id=:code"), item
                )
                .mappings()
                .first()
            )
            if prior is None:
                raise ValueError("WEB_CHECK_OLDER_OR_SOURCE_UNINITIALIZED")
            snapshot = {
                **prior["platform_snapshot"],
                "update_timing": merge_update_timing(prior, item["updated"], checked_at),
            }
            result = c.execute(
                sa.text("""UPDATE public.source_checkpoints
              SET web_updated_at=:updated,web_checked_at=:checked,platform_snapshot=:snapshot
              WHERE source_id=:code AND (web_checked_at IS NULL OR web_checked_at<=:checked)
            """).bindparams(sa.bindparam("snapshot", type_=sa.JSON)),
                {**item, "snapshot": snapshot},
            )
            if result.rowcount != 1:
                raise ValueError("WEB_CHECK_OLDER_OR_SOURCE_UNINITIALIZED")
    return len(values)
