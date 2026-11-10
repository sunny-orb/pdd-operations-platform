"""只读发布/任务/检查元信息；后台按版本缓存日期摘要，GET不扫描经营事实。"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.data_status.catalog import DATASET_NAMES, SOURCES, job_for, source_identity
from app.modules.data_status.history_coverage import history_status
from app.modules.data_status.timeliness import update_timeliness
from app.modules.product_board.repository import BoardError

DATE_TABLES = {
    "shop_daily": ("shop_daily_facts", "business_date"),
    "product_daily": ("product_daily_facts", "business_date"),
    "product_promotion": ("product_promotion_records", "period_end"),
    "shop_ratings": ("shop_rating_daily", "rating_date"),
    "shop_channel_product_promotion": ("shop_channel_promotion_daily", "business_date"),
    "shop_channel_star_shop": ("shop_channel_promotion_daily", "business_date"),
    "shop_regions": ("shop_region_daily", "business_date"),
    "product_regions": ("product_region_records", "period_end"),
    "shop_refunds": ("shop_refund_records", "business_date"),
    "jst_returns": ("return_records", "period_end"),
    "jst_shipped_sales": ("product_sales_records", "period_end"),
    "jst_cancelled_sales": ("product_sales_records", "period_end"),
    "sales_targets": ("sales_targets", "period_end"),
}


def versions(c):
    return {
        r["dataset_code"]: dict(r)
        for r in c.execute(
            sa.text("""
      SELECT v.id,v.dataset_code,v.received_at,v.sealed_at,v.period_start,v.period_end,
             v.upstream_revision FROM current_datasets p
             JOIN dataset_versions v ON v.id=p.version_id
    """)
        ).mappings()
    }


def refresh_platform_snapshots(c):
    """仅任务调用：当前版本改变才聚合真实日期，保存可校验版本绑定的轻量摘要。"""
    with c.begin():
        heads = versions(c)
        saved = {
            r["source_id"]: r["platform_snapshot"]
            for r in c.execute(
                sa.text("SELECT source_id,platform_snapshot FROM source_checkpoints")
            ).mappings()
        }
        cache = {}
        table = sa.Table("source_checkpoints", sa.MetaData(), autoload_with=c, schema="public")
        for source in SOURCES:
            if source.id not in saved:
                continue
            snapshot = {
                key: saved[source.id][key]
                for key in ("history", "update_timing")
                if key in saved[source.id]
            }
            for code in source.datasets:
                head = heads.get(code)
                if not head:
                    continue
                prior = saved[source.id].get(code)
                if prior and prior["version_id"] == head["id"]:
                    snapshot[code] = prior
                    continue
                if code not in cache:
                    day = head["period_end"] if code != "weather_publication" else None
                    if code in DATE_TABLES:
                        fact, column = DATE_TABLES[code]
                        day = c.scalar(
                            sa.text(
                                f"SELECT MAX({column}) FROM public.{fact} "
                                "WHERE dataset_version_id=:v"
                            ),
                            {"v": head["id"]},
                        )
                    cache[code] = {"version_id": head["id"], "date": str(day) if day else None}
                snapshot[code] = cache[code]
            c.execute(
                table.update()
                .where(table.c.source_id == source.id)
                .values(platform_snapshot=snapshot)
            )


def read_status(engine, *, interval_seconds=900, enabled=True, source_schedule=None):
    """返回已接收状态；新版本尚未生成日期摘要时留空，不展示旧版日期。"""
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            now = datetime.now(UTC)
            heads = versions(c)
            checkpoints = {
                r["source_id"]: dict(r)
                for r in c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings()
            }
            jobs = {
                r["job_code"]: dict(r)
                for r in c.execute(
                    sa.text("""
                SELECT DISTINCT ON(job_code) job_code,status,queued_at,finished_at,
                  result_version_id,error_code,lease_expires_at FROM task_runs
                ORDER BY job_code,queued_at DESC,id DESC
            """)
                ).mappings()
            }
            successes = {
                r["dataset_code"]: r["finished_at"]
                for r in c.execute(
                    sa.text("""
                SELECT p.dataset_code,max(t.finished_at) AS finished_at
                FROM current_datasets p JOIN task_runs t ON t.result_version_id=p.version_id
                  OR EXISTS (SELECT 1 FROM version_inputs i
                    WHERE i.output_version_id=t.result_version_id
                      AND i.input_version_id=p.version_id)
                WHERE t.status='succeeded' AND t.job_code LIKE 'sync_%'
                  AND t.job_code <> 'sync_project'
                GROUP BY p.dataset_code
            """)
                ).mappings()
            }
            weather = {}
            if "weather_publication" in heads:
                entries = (
                    c.scalar(
                        sa.text(
                            "SELECT document->'sources' FROM weather_publications WHERE id=:id"
                        ),
                        {"id": heads["weather_publication"]["id"]},
                    )
                    or []
                )
                weather = {item["url"]: item["issue_time"] for item in entries}

            def dataset(code, checkpoint=None):
                head, task = heads.get(code), jobs.get(job_for(code))
                expired = bool(
                    task and task["status"] == "running" and task["lease_expires_at"] <= now
                )
                cached = (checkpoint or {}).get("platform_snapshot", {}).get(code, {})
                return dict(
                    code=code,
                    name=DATASET_NAMES.get(code, code),
                    version_id=head["id"] if head else None,
                    date=cached.get("date")
                    if head and cached.get("version_id") == head["id"]
                    else None,
                    received_at=head["received_at"] if head else None,
                    last_success_at=successes.get(code),
                    last_attempt_at=task["queued_at"] if task else None,
                    last_finished_at=task["finished_at"] if task else None,
                    attempt_status="abandoned" if expired else task["status"] if task else None,
                    error_code="WORKER_LOST" if expired else task["error_code"] if task else None,
                    revision=head["upstream_revision"] if head else None,
                )

            from app.modules.data_status.schedule_policy import (
                due_sources,
                next_check,
                source_times,
            )

            scheduled = bool(
                source_schedule
                and source_schedule.source_schedule_enabled
                and source_schedule.enabled
            )
            schedule_args = (
                (source_schedule.warehouse_times, source_schedule.feishu_times)
                if source_schedule
                else ()
            )
            due = due_sources(now, *schedule_args) if scheduled else {}
            rows = []
            for source in SOURCES:
                p = checkpoints.get(source.id, {})
                datasets = [dataset(code, p) for code in source.datasets]
                fixed_times = source_times(source.id, *schedule_args) if scheduled else ()
                if fixed_times and p.get("sync_status"):
                    expired_sync = p["sync_status"] == "running" and now > p[
                        "sync_started_at"
                    ] + timedelta(minutes=20)
                    for item in datasets:
                        item.update(
                            last_attempt_at=p["sync_started_at"],
                            last_finished_at=p["sync_finished_at"],
                            attempt_status="abandoned" if expired_sync else p["sync_status"],
                            error_code="WORKER_LOST"
                            if expired_sync
                            else "SYNC_FAILED"
                            if p["sync_status"] == "failed"
                            else None,
                        )
                        if p["sync_status"] == "succeeded":
                            item["last_success_at"] = p["sync_finished_at"]
                        elif p.get("accepted_at"):
                            item["last_success_at"] = p["accepted_at"]
                has_data = all(row["version_id"] for row in datasets)
                running = any(row["attempt_status"] == "running" for row in datasets)
                failed = (
                    any(
                        row["attempt_status"] in ("failed", "abandoned", "cancelled")
                        for row in datasets
                    )
                    or p.get("error_code") == "SYNC_FAILED"
                )
                changed = p.get("observed_fingerprint") and p["observed_fingerprint"] != p.get(
                    "accepted_fingerprint"
                )
                upstream_state = (
                    "unchecked"
                    if not p
                    else "checking"
                    if p["check_status"] == "checking"
                    else "failed"
                    if p["check_status"] == "failed"
                    else "changed"
                    if changed
                    else "unchanged"
                )
                platform_state = (
                    "syncing"
                    if running
                    else "failed"
                    if failed
                    else "pending"
                    if changed
                    else "available"
                    if has_data
                    else "unavailable"
                )
                if source.system == "nmc":
                    for row in datasets:
                        row["date"] = weather.get(source.locator)
                if source.id == "jst_master":
                    # 原资料只随事实归档；不能拿事实业务日冒充资料快照日。
                    for row in datasets:
                        row["date"] = None
                rows.append(
                    dict(
                        id=source.id,
                        name=source.name,
                        **source_identity(source),
                        system=source.system,
                        locator=source.locator,
                        pages=source.pages,
                        kind=source.kind,
                        datasets=datasets,
                        platform_state=platform_state,
                        timeliness=update_timeliness(source, p, now),
                        history=history_status(
                            source.id,
                            p.get("platform_snapshot", {}).get("history"),
                            heads,
                            now,
                            *schedule_args,
                        ),
                        upstream=dict(
                            state=upstream_state,
                            checked_at=p.get("checked_at"),
                            observed_at=p.get("observed_at"),
                            web_updated_at=p.get("web_updated_at"),
                            web_checked_at=p.get("web_checked_at"),
                            next_check_at=next_check(source.id, now, *schedule_args)
                            if fixed_times
                            else None,
                            check_times=list(fixed_times),
                            stale=bool(
                                (
                                    due.get(source.id)
                                    and now > due[source.id] + timedelta(minutes=20)
                                    and (
                                        not p.get("scheduled_slot_at")
                                        or p["scheduled_slot_at"] < due[source.id]
                                    )
                                )
                                if fixed_times
                                else p
                                and now > p["checked_at"] + timedelta(seconds=interval_seconds * 2)
                            ),
                            observation=p.get("observation", {}),
                            accepted_at=p.get("accepted_at"),
                            error_code=p.get("error_code"),
                            duration_ms=p.get("duration_ms"),
                        ),
                    )
                )
            inputs = (
                c.execute(
                    sa.text("""
                SELECT v.dataset_code,src.dataset_code AS input_code,
                       i.input_version_id,p.version_id,i.window_end
                FROM current_datasets out JOIN dataset_versions v ON out.version_id=v.id
                JOIN version_inputs i ON i.output_version_id=v.id
                JOIN dataset_versions src ON src.id=i.input_version_id
                LEFT JOIN current_datasets p ON p.dataset_code=src.dataset_code
                WHERE out.dataset_code IN ('product_scores','aftersales_report')
            """)
                )
                .mappings()
                .all()
            )
            processing = []
            for code in ("product_scores", "aftersales_report", "refund_targets"):
                item = dataset(code)
                head = heads.get(code)
                stale = any(
                    r["dataset_code"] == code and r["input_version_id"] != r["version_id"]
                    for r in inputs
                )
                stale = stale or bool(
                    head
                    and code == "aftersales_report"
                    and "refund_targets" in heads
                    and not any(
                        r["dataset_code"] == code and r["input_code"] == "refund_targets"
                        for r in inputs
                    )
                )
                input_dates = [
                    r["window_end"] for r in inputs if r["dataset_code"] == code and r["window_end"]
                ]
                item.update(
                    date=str(head["period_end"])
                    if head and head["period_end"]
                    else str(max(input_dates))
                    if input_dates
                    else None,
                    state="syncing"
                    if item["attempt_status"] == "running"
                    else "failed"
                    if item["attempt_status"] in ("failed", "abandoned", "cancelled")
                    else "pending"
                    if stale
                    else "available"
                    if head
                    else "unavailable",
                    generated_at=head["sealed_at"] if head else None,
                    pages=["products", "overview"]
                    if code == "product_scores"
                    else ["aftersales", "overview"],
                )
                processing.append(item)
            return {
                "data": dict(
                    sources=rows,
                    processing=processing,
                    monitor=dict(
                        enabled=enabled,
                        interval_seconds=interval_seconds,
                        source_schedule_enabled=scheduled,
                        warehouse_times=list(source_schedule.warehouse_times)
                        if source_schedule
                        else [],
                        feishu_times=list(source_schedule.feishu_times) if source_schedule else [],
                    ),
                ),
                "meta": {"generated_at": now},
            }
    except SQLAlchemyError as exc:
        raise BoardError("DATA_STATUS_UNAVAILABLE", 503) from exc
