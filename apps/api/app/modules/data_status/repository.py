"""只读发布/任务/检查元信息；后台按版本缓存日期摘要，GET不扫描经营事实。"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import SQLAlchemyError

from app.modules.data_status.catalog import DATASET_NAMES, SOURCES, job_for, source_identity
from app.modules.data_status.checkpoint_store import read_legacy_tasks
from app.modules.data_status.failure_codes import SYNC_FAILURE_CODES, safe_failure_code
from app.modules.data_status.history_coverage import history_status
from app.modules.data_status.timeliness import source_timeliness
from app.modules.product_board.repository import BoardError
from app.modules.sales.datasets import READ_SPECS

DATE_TABLES = {
    "shop_daily": ("shop_daily_facts", "business_date"),
    "product_daily": ("read_product_daily_facts", "business_date"),
    "product_promotion": ("read_product_promotion_records", "period_end"),
    "shop_ratings": ("shop_rating_daily", "rating_date"),
    "shop_channel_product_promotion": ("shop_channel_promotion_daily", "business_date"),
    "shop_channel_star_shop": ("shop_channel_promotion_daily", "business_date"),
    "shop_regions": ("shop_region_daily", "business_date"),
    "product_regions": ("read_product_region_records", "period_end"),
    "shop_refunds": ("shop_refund_records", "business_date"),
    "jst_returns": ("read_return_records", "period_end"),
    "rpa_returns": ("read_return_records", "period_end"),
    "rpa_shipped_sales": ("read_product_sales_records", "period_end"),
    "rpa_cancelled_sales": ("read_product_sales_records", "period_end"),
    "jst_shipped_sales": ("read_product_sales_records", "period_end"),
    "jst_cancelled_sales": ("read_product_sales_records", "period_end"),
    "sales_targets": ("sales_targets", "period_end"),
}


def versions(c):
    return {
        r["dataset_code"]: dict(r)
        for r in c.execute(
            sa.text("""
      SELECT v.id,v.dataset_code,v.received_at,v.sealed_at,v.period_start,v.period_end,
             v.upstream_revision,v.row_schema_revision FROM current_datasets p
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
            snapshot = {}
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
                .values(
                    platform_snapshot=table.c.platform_snapshot.op("-")(
                        sa.cast(list(source.datasets), ARRAY(sa.Text))
                    ).op("||")(sa.cast(snapshot, JSONB))
                )
            )


class _StatusSnapshotChanged(Exception):
    """行的更新事务已结束时，要求从新快照确认一次真实任务状态。"""


def _active_sales_transactions(c, tasks, now):
    """仅将准确任务行的未提交更新与同一后端的来源执行权联合视为存活。"""
    supported = {"shop_daily", "sales_targets", *READ_SPECS}
    expired = [
        task
        for task in tasks.values()
        if task["status"] == "running"
        and task["lease_expires_at"] <= now
        and task["lock_key"] in supported
        and task["job_code"]
        in ("sync_" + task["lock_key"], "sync_" + task["lock_key"] + "_reconciliation")
    ]
    if not expired:
        return set(), False
    active = set(
        c.execute(
            sa.text("""
            SELECT t.id FROM public.task_runs t
            JOIN pg_catalog.pg_locks tx ON tx.locktype='transactionid'
              AND tx.transactionid=t.xmax AND tx.granted AND tx.mode='ExclusiveLock'
            JOIN pg_catalog.pg_locks owner ON owner.pid=tx.pid
              AND owner.locktype='advisory' AND owner.granted AND owner.mode='ExclusiveLock'
              AND owner.objsubid=1
              AND owner.classid=((hashtext('m3-' || t.lock_key)::bigint >> 32)
                                 & 4294967295)::oid
              AND owner.objid=(hashtext('m3-' || t.lock_key)::bigint & 4294967295)::oid
            JOIN pg_catalog.pg_database db ON db.oid=owner.database
              AND db.datname=current_database()
            WHERE t.id=ANY(:ids)
            """),
            {"ids": [task["id"] for task in expired]},
        ).scalars()
    )
    # 锁视图不是MVCC快照；发布可能刚提交，先有界换快照再判断失锁。
    stale_snapshot = any(
        task["updater_xid"] != "0" and task["id"] not in active for task in expired
    )
    return active, stale_snapshot


def read_status(engine, *, interval_seconds=900, enabled=True, source_schedule=None):
    arguments = dict(
        interval_seconds=interval_seconds, enabled=enabled, source_schedule=source_schedule
    )
    try:
        return _read_status_snapshot(engine, **arguments, retry_expired=True)
    except _StatusSnapshotChanged:
        return _read_status_snapshot(engine, **arguments, retry_expired=False)


def _read_status_snapshot(engine, *, interval_seconds, enabled, source_schedule, retry_expired):
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
                SELECT DISTINCT ON(job_code) id,job_code,lock_key,status,queued_at,finished_at,
                  result_version_id,error_code,lease_expires_at,xmax::text AS updater_xid
                FROM task_runs
                ORDER BY job_code,queued_at DESC,id DESC
            """)
                ).mappings()
            }
            active_sales_tasks, stale_snapshot = _active_sales_transactions(c, jobs, now)
            if retry_expired and stale_snapshot:
                raise _StatusSnapshotChanged
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

            def dataset(code, checkpoint=None, *, source_id=None):
                from app.modules.data_status.schedule_policy import receipt_after_attempt

                head = heads.get(code)
                task = (
                    legacy_tasks.get(source_id)
                    if source_id != "product_regions"
                    and reconciliations.get(source_id, {}).get("last_attempt_at")
                    else jobs.get(job_for(code))
                )
                expired = bool(
                    task
                    and task["status"] == "running"
                    and task["lease_expires_at"] <= now
                    and task.get("id") not in active_sales_tasks
                )
                cached = (checkpoint or {}).get("platform_snapshot", {}).get(code, {})
                result = dict(
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
                cycle = reconciliations.get(source_id, {})
                if (
                    source_id == "product_regions"
                    and task
                    and cycle.get("status") == "succeeded"
                    and (checkpoint or {}).get("accepted_at") == cycle.get("last_success_at")
                    and (checkpoint or {}).get("error_code")
                    not in SYNC_FAILURE_CODES | {"SOURCE_CHANGED_DURING_SYNC"}
                    and receipt_after_attempt(checkpoint or {}, task)
                ):
                    # 同源周接收已稳定成功：展示新尝试，历史普通任务原样保留。
                    result.update(
                        last_attempt_at=cycle["last_attempt_at"],
                        last_finished_at=cycle["last_finished_at"],
                        last_success_at=max(
                            stamp
                            for stamp in (result["last_success_at"], cycle["last_success_at"])
                            if stamp is not None
                        ),
                        attempt_status="succeeded",
                        error_code=None,
                    )
                return result

            from app.modules.data_status.schedule_policy import (
                RECONCILIATION_SOURCES,
                due_sources,
                first_reconciliation,
                next_check,
                next_reconciliation,
                source_retry_at,
                source_times,
                unconfirmed_receipt,
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
            reconciliations = (
                {
                    r["source_id"]: dict(r)
                    for r in c.execute(sa.text("SELECT * FROM source_reconciliations")).mappings()
                }
                if scheduled
                else {}
            )

            legacy_tasks = read_legacy_tasks(c) if reconciliations else {}

            def reconciliation(code):
                if not scheduled or code not in RECONCILIATION_SOURCES:
                    return None
                row = reconciliations.get(code, {"status": "waiting"})
                next_at = (
                    next_reconciliation(
                        row,
                        source_schedule.reconciliation_time,
                        source_schedule.reconciliation_retry_seconds,
                    )
                    if code in reconciliations
                    else first_reconciliation(now, source_schedule.reconciliation_time)
                )
                retrying = row["status"] in ("failed", "deferred")
                return dict(
                    cycle_at=row.get("cycle_at"),
                    status=row["status"],
                    last_attempt_at=row.get("last_attempt_at"),
                    last_finished_at=row.get("last_finished_at"),
                    last_success_at=row.get("last_success_at"),
                    error_code=row.get("error_code"),
                    next_retry_at=next_at if retrying else None,
                    next_scheduled_at=None if retrying else next_at,
                )

            rows = []
            rpa_active = (
                heads.get("aftersales_report", {}).get("row_schema_revision") == "m5-rpa-period-v1"
            )
            for source in SOURCES:
                # 运营中心也消费当前售后报告；JST旧链仅供固定历史读取，不能冒充当前来源。
                if rpa_active and source.id in ("jst_returns", "jst_sales", "jst_master"):
                    continue
                p = checkpoints.get(source.id, {})
                datasets = [dataset(code, p, source_id=source.id) for code in source.datasets]
                fixed_times = source_times(source.id, *schedule_args) if scheduled else ()
                retry_at = source_retry_at(p)
                sync_status = p.get("sync_status")
                if fixed_times and sync_status:
                    # 该来源已有独立普通接收结果，优先于首轮对账前的旧任务证据。
                    expired_sync = sync_status == "running" and now > p[
                        "sync_started_at"
                    ] + timedelta(minutes=20)
                    for item in datasets:
                        item.update(
                            last_attempt_at=p.get("sync_started_at"),
                            last_finished_at=p.get("sync_finished_at"),
                            attempt_status="abandoned" if expired_sync else sync_status,
                            error_code="WORKER_LOST"
                            if expired_sync
                            else safe_failure_code(p.get("error_code"))
                            if sync_status == "failed"
                            else None,
                        )
                        successes_at = (
                            item["last_success_at"],
                            p.get("accepted_at"),
                            p.get("sync_finished_at") if sync_status == "succeeded" else None,
                        )
                        item["last_success_at"] = max(
                            (stamp for stamp in successes_at if stamp is not None), default=None
                        )
                for item in datasets:
                    reason = p.get("error_code") or item.get("error_code")
                    if reason in ("DATASET_RESTORE_REQUIRED", "MAINTENANCE_DEFERRED"):
                        item.update(attempt_status="deferred", error_code=reason)
                deferred = any(item["attempt_status"] == "deferred" for item in datasets)
                has_data = all(row["version_id"] for row in datasets)
                running = any(row["attempt_status"] == "running" for row in datasets)
                failed = (
                    any(
                        row["attempt_status"] in ("failed", "abandoned", "cancelled")
                        for row in datasets
                    )
                    or p.get("error_code") in SYNC_FAILURE_CODES
                )
                changed = p.get("observed_fingerprint") and p["observed_fingerprint"] != p.get(
                    "accepted_fingerprint"
                )
                if source.monitor_only:
                    changed = p.get("observation", {}).get("changed_since_previous_check")
                upstream_state = (
                    "unchecked"
                    if not p
                    else "checking"
                    if p["check_status"] == "checking"
                    else "failed"
                    if p["check_status"] == "failed"
                    else "observed"
                    if source.monitor_only and changed is None
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
                    and not source.monitor_only
                    or deferred
                    or fixed_times
                    and unconfirmed_receipt(p)
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
                history = history_status(
                    source.id,
                    p.get("platform_snapshot", {}).get("history"),
                    heads,
                    now,
                    *schedule_args,
                )
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
                        reconciliation=reconciliation(source.id),
                        timeliness=source_timeliness(source, p, now),
                        history=history,
                        upstream=dict(
                            state=upstream_state,
                            checked_at=p.get("checked_at"),
                            observed_at=p.get("observed_at"),
                            web_updated_at=p.get("web_updated_at"),
                            web_checked_at=p.get("web_checked_at"),
                            next_check_at=retry_at
                            or (
                                next_check(source.id, now, *schedule_args)
                                if fixed_times
                                else p.get("checked_at") + timedelta(seconds=interval_seconds)
                                if source.monitor_only and p.get("checked_at") and enabled
                                else None
                            ),
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
                        reconciliation_time=source_schedule.reconciliation_time
                        if scheduled
                        else None,
                        reconciliation_retry_seconds=source_schedule.reconciliation_retry_seconds
                        if scheduled
                        else None,
                    ),
                ),
                "meta": {"generated_at": now},
            }
    except SQLAlchemyError as exc:
        raise BoardError("DATA_STATUS_UNAVAILABLE", 503) from exc
