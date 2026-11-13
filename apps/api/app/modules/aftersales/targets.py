"""用户确认的金额退款区间；追加发布并由报告固定输入，不替历史报告换目标。"""

import tomllib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

METRICS = {"total_refund_rate": "整体总退款率", "aftersale_refund_rate": "售后退款率"}
POLICY = "rolling_30d_and_natural_month"
REVISION = "m5-amount-target-ranges-v1"


def normalize_config(value):
    """固定两个金额指标/两端界；非法或不完整配置拒绝，不推算中点或件数率。"""
    if (
        set(value) != {*METRICS, "application_policy_code"}
        or value["application_policy_code"] != POLICY
    ):
        raise ValueError("TARGET_CONFIG_INVALID")
    rows = []
    for code, label in METRICS.items():
        item = value[code]
        if not isinstance(item, dict) or set(item) != {"lower", "upper"}:
            raise ValueError("TARGET_CONFIG_INVALID")
        if any(not isinstance(item[key], (str, Decimal)) for key in item):
            raise ValueError("TARGET_DECIMAL_REQUIRED")
        lower, upper = Decimal(item["lower"]), Decimal(item["upper"])
        if not lower.is_finite() or not upper.is_finite() or not 0 <= lower <= upper <= 1:
            raise ValueError("TARGET_RANGE_INVALID")
        rows.append(dict(metric_code=code, label=label, lower_rate=lower, upper_rate=upper))
    return rows


def describe_range(row, current):
    """金额率与原上下界分别比较；状态仅说明观察值，不宣称业务完整/已达标。"""
    lower, upper = row["lower_rate"], row["upper_rate"]
    return dict(
        metric_code=row["metric_code"],
        label=METRICS[row["metric_code"]],
        lower_rate=lower,
        upper_rate=upper,
        current=current,
        lower_gap_pp=(current - lower) * 100 if current is not None else None,
        upper_gap_pp=(current - upper) * 100 if current is not None else None,
        state="metric_unavailable"
        if current is None
        else "below_range"
        if current < lower
        else "above_range"
        if current > upper
        else "within_range",
    )


def publish_targets(engine, config, archive_root):
    """已确认配置→不可变两条范围目标及真实父版；失败保旧、同内容复用。"""
    from app.modules.archive_store import require_archive

    from .publisher import canonical, fingerprint, private

    m = sa.MetaData()
    tables = {
        n: sa.Table(n, m, schema="public", autoload_with=engine, resolve_fks=False)
        for n in ("dataset_versions", "current_datasets", "task_runs", "refund_targets")
    }
    v, current, tasks, goals = (tables[n] for n in tables)
    run = str(uuid4())
    with engine.connect() as c:
        if not c.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext('m5-source-report'))")):
            c.rollback()
            raise ValueError("M5_SYNC_RUNNING")
        c.commit()
        try:
            with c.begin():
                now = c.scalar(sa.select(sa.func.clock_timestamp()))
                c.execute(
                    tasks.update()
                    .where(
                        tasks.c.job_code == "publish_refund_targets", tasks.c.status == "running"
                    )
                    .values(
                        status="abandoned",
                        finished_at=now,
                        lease_expires_at=None,
                        error_code="WORKER_LOST",
                    )
                )
                c.execute(
                    tasks.insert().values(
                        id=run,
                        job_code="publish_refund_targets",
                        request_ref=REVISION,
                        lock_key="m5-source-report",
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=run,
                        lease_expires_at=now + timedelta(seconds=120),
                    )
                )
            try:
                raw = config() if callable(config) else config
                rows = normalize_config(raw)
                digest = fingerprint(dict(revision=REVISION, config=raw))
                with c.begin():
                    active = c.scalar(
                        sa.select(tasks.c.id)
                        .where(
                            tasks.c.id == run,
                            tasks.c.status == "running",
                            tasks.c.worker_ref == run,
                            tasks.c.lease_expires_at > sa.func.clock_timestamp(),
                        )
                        .with_for_update()
                    )
                    if active is None:
                        raise ValueError("TASK_LEASE_LOST")
                    existing = c.execute(
                        sa.select(v.c.id, v.c.retained_input_ref, v.c.interpretation_ref).where(
                            v.c.dataset_code == "refund_targets",
                            v.c.content_fingerprint == digest,
                            v.c.state == "published",
                        )
                    ).one_or_none()
                    vid = existing.id if existing else str(uuid4())
                    if existing:
                        require_archive(existing.retained_input_ref)
                        manifest = require_archive(existing.interpretation_ref)
                    else:
                        manifest = private(
                            Path(archive_root) / run / "targets.json",
                            canonical(dict(revision=REVISION, config=raw, rows=rows)),
                        )
                    if not existing:
                        c.execute(
                            v.insert().values(
                                id=vid,
                                dataset_code="refund_targets",
                                origin_kind="source",
                                state="draft",
                                source_system="user_confirmation",
                                source_locator="config/aftersales-targets.toml",
                                row_schema_revision=REVISION,
                                interpretation_ref=manifest,
                                producer_revision=REVISION,
                                retained_input_ref=manifest,
                            )
                        )
                        for row in rows:
                            c.execute(
                                goals.insert().values(
                                    id=str(uuid4()),
                                    dataset_version_id=vid,
                                    source_row_key=row["metric_code"],
                                    source_target_key=row["metric_code"],
                                    source_scope_label=row["label"],
                                    source_scope_text="已确认的总体金额口径；滚动30天和自然月共用",
                                    scope_kind="all",
                                    category_match_mode="all",
                                    style_match_mode="all",
                                    scope_status="complete",
                                    source_period_label="滚动30天及自然月共享当前目标",
                                    period_kind="custom",
                                    period_status="reference_only",
                                    target_rate=None,
                                    metric_code=row["metric_code"],
                                    lower_rate=row["lower_rate"],
                                    upper_rate=row["upper_rate"],
                                    application_policy_code=POLICY,
                                    evidence_note=(
                                        "用户2026-10-28确认，见data-contract M5-03；"
                                        "不是历史当时目标"
                                    ),
                                )
                            )
                        stored = c.execute(
                            sa.select(
                                goals.c.metric_code, goals.c.lower_rate, goals.c.upper_rate
                            ).where(goals.c.dataset_version_id == vid)
                        ).all()
                        if {tuple(x) for x in stored} != {
                            (r["metric_code"], r["lower_rate"], r["upper_rate"]) for r in rows
                        }:
                            raise ValueError("TARGET_DB_RECONCILIATION_FAILED")
                        c.execute(
                            v.update()
                            .where(v.c.id == vid)
                            .values(
                                state="validated",
                                content_fingerprint=digest,
                                validation_evidence_ref=manifest,
                                received_at=sa.func.clock_timestamp(),
                            )
                        )
                        c.execute(v.update().where(v.c.id == vid).values(state="published"))
                    c.execute(
                        insert(current)
                        .values(dataset_code="refund_targets", version_id=vid)
                        .on_conflict_do_update(
                            index_elements=[current.c.dataset_code], set_={"version_id": vid}
                        )
                    )
                    done = c.execute(
                        tasks.update()
                        .where(
                            tasks.c.id == run,
                            tasks.c.status == "running",
                            tasks.c.worker_ref == run,
                            tasks.c.lease_expires_at > sa.func.clock_timestamp(),
                        )
                        .values(
                            status="succeeded",
                            finished_at=sa.func.clock_timestamp(),
                            result_version_id=vid,
                            lease_expires_at=None,
                            evidence_ref=manifest,
                        )
                    )
                    if done.rowcount != 1:
                        raise ValueError("TASK_LEASE_LOST")
                return dict(status="succeeded", version_id=vid, reused=bool(existing))
            except Exception as exc:
                with c.begin():
                    c.execute(
                        tasks.update()
                        .where(tasks.c.id == run, tasks.c.status == "running")
                        .values(
                            status="failed",
                            finished_at=sa.func.clock_timestamp(),
                            lease_expires_at=None,
                            error_code="TARGET_PUBLISH_FAILED",
                            error_summary=type(exc).__name__,
                        )
                    )
                raise
        finally:
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m5-source-report'))"))
            c.commit()


def run_targets(engine, archive_root, config_path):
    """由独立调度读取集中业务配置；GET永不读文件或发布目标。"""
    return publish_targets(
        engine, lambda: tomllib.loads(Path(config_path).read_text()), archive_root
    )


def add_target_ranges(repo, q, data):
    """仅从本报告targets输入读取；总体目标不冒用到任意筛选的品类/单品。"""
    from .classification import branch_enabled

    data["target_ranges"] = []
    if not repo.report or not branch_enabled(
        repo.classification_parameters(), "target_application"
    ):
        return data
    vid = repo.c.scalar(
        sa.text("""SELECT i.input_version_id FROM public.version_inputs i
        JOIN public.dataset_versions v ON v.id=i.input_version_id
        WHERE i.output_version_id=:id AND i.input_role='targets'
        AND v.dataset_code='refund_targets' AND v.state='published'"""),
        dict(id=repo.report["dataset_version_id"]),
    )
    if not vid:
        return data
    rows = (
        repo.c.execute(
            sa.text("""SELECT id,metric_code,lower_rate,upper_rate FROM public.refund_targets
        WHERE dataset_version_id=:id AND target_rate IS NULL AND scope_kind='all'
        AND scope_status='complete' AND application_policy_code=:policy
        ORDER BY CASE metric_code WHEN 'total_refund_rate' THEN 0 ELSE 1 END"""),
            dict(id=vid, policy=POLICY),
        )
        .mappings()
        .all()
    )
    report = repo.report
    start, end = report["period_start"], report["period_end"]
    natural_month = start.day == 1 and (end + timedelta(days=1)).day == 1
    applicable = not (q.category or q.keyword or q.focus != "all") and (
        (end - start).days == 29 or report["report_type"] == "monthly" and natural_month
    )
    metrics = {m["key"]: m["value"] for m in data["metrics"]}
    keys = {"total_refund_rate": "total_rate", "aftersale_refund_rate": "aftersale_rate"}
    for row in rows:
        value = metrics.get(keys[row["metric_code"]]) if applicable else None
        target = describe_range(row, Decimal(value) if value is not None else None)
        target.update(target_id=row["id"], version_id=vid)
        if not applicable:
            target["state"] = "scope_not_applicable"
        data["target_ranges"].append(target)
    text = "；".join(
        f"{r['label']}目标{r['lower_rate'] * 100:g}%–{r['upper_rate'] * 100:g}%"
        for r in data["target_ranges"]
    )
    for item in data["risk_metrics"]:
        if item["key"] == "target_gap":
            total = next(
                (r for r in data["target_ranges"] if r["metric_code"] == "total_refund_rate"), None
            )
            item.update(
                value=total["upper_gap_pp"] if total else None,
                detail=text,
                note="显示距整体目标上界的百分点差；两目标区间分别保留，不取中值。",
                status="observed_reference"
                if total and total["upper_gap_pp"] is not None
                else "unavailable",
            )
        elif item["key"] == "excess_estimate":
            total = next(
                (r for r in data["target_ranges"] if r["metric_code"] == "total_refund_rate"), None
            )
            g = metrics.get("gmv")
            if total and total["current"] is not None and g is not None and Decimal(g) > 0:
                item["value"] = max(Decimal(0), total["current"] - total["upper_rate"]) * Decimal(g)
                item["status"] = "observed_reference"
                item["note"] = "超过整体目标上界部分×本报告GMV，仅观察测算，不是利润/现金损失。"
    return data
