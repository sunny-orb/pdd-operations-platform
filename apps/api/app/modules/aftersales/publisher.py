"""M5必要原行与固定期间报告发布；仅既有v2表，失败保旧、重跑幂等。"""

import json
import os
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.modules.aftersales.classification import rule_parameters
from app.modules.aftersales.ingestion import MASTER_TABLE, NOTE, REVISION, TABLES
from app.modules.sales.source_policy import SHOPS

FACTS = dict(
    jst_returns="return_records",
    jst_shipped_sales="product_sales_records",
    jst_cancelled_sales="product_sales_records",
)
ROLES = dict(
    jst_returns="j1_returns",
    jst_shipped_sales="j2_shipped_sales",
    jst_cancelled_sales="j3_cancelled_sales",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def producer():
    return fingerprint(
        {
            p.name: p.read_text()
            for p in (
                Path(__file__),
                Path(__file__).with_name("ingestion.py"),
                Path(__file__).with_name("classification.py"),
                Path(__file__).with_name("bundle_categories.py"),
                Path(__file__).parents[2] / "connectors/aftersales.py",
            )
        }
    )


def reflect(engine):
    m = sa.MetaData()
    tables = {
        n: sa.Table(n, m, schema="public", autoload_with=engine, resolve_fks=False)
        for n in (
            "dataset_versions",
            "current_datasets",
            "task_runs",
            "coverage_records",
            "version_inputs",
            "shops",
            "rule_sets",
            "aftersales_reports",
            "return_records",
            "product_sales_records",
        )
    }
    tables["return_records"].c.source_analysis_fields.type.none_as_null = True
    return tables


def private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with open(path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as out:
        out.write(value)
    return str(path.resolve())


def publish_source(engine, code, receive, root):
    """receive(heartbeat)返回(end,必要原投影,规范行)，真实源读取前登记任务。"""
    code_hash = producer()
    t = reflect(engine)
    run = str(uuid4())
    task, v, current = t["task_runs"], t["dataset_versions"], t["current_datasets"]
    with engine.connect() as c:
        locked = c.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext('m5-source-report'))")
        ).scalar_one()
        c.commit()
        if not locked:
            raise ValueError("M5_SYNC_RUNNING")
        try:
            with c.begin():
                c.execute(
                    task.update()
                    .where(task.c.job_code == "sync_" + code, task.c.status == "running")
                    .values(
                        status="abandoned",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        error_code="WORKER_LOST",
                    )
                )
                now = c.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                c.execute(
                    task.insert().values(
                        id=run,
                        job_code="sync_" + code,
                        request_ref=REVISION,
                        lock_key="m5-" + code,
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=run,
                        lease_expires_at=now + timedelta(hours=2),
                    )
                )

            def heartbeat():
                with c.begin():
                    c.execute(
                        task.update()
                        .where(task.c.id == run, task.c.status == "running")
                        .values(
                            lease_expires_at=sa.func.clock_timestamp()
                            + sa.text("interval '2 hours'")
                        )
                    )

            end, raw, rows = receive(heartbeat)
            if producer() != code_hash:
                raise ValueError("M5_PRODUCER_CHANGED")
            digest = fingerprint(
                dict(revision=REVISION, producer=code_hash, end=end, raw=raw, rows=rows)
            )
            evidence = private(
                Path(root) / run / "interpretation.json",
                canonical(
                    dict(
                        revision=REVISION,
                        source=TABLES[code],
                        period_start=end - timedelta(days=59),
                        period_end=end,
                        rows=len(rows),
                        fingerprint=digest,
                        note=NOTE,
                        source_shop_aliases=SHOPS,
                        quantity_evidence="1119 historical nonnull groups equal; finite scope only",
                        amount_gate="blocked: platform subsidy and two material counterexamples",
                        deletion_policy="stable double read; old versions retained",
                    )
                ),
            )
            archive = private(
                Path(root) / run / "input.ndjson", "\n".join(canonical(r) for r in raw) + "\n"
            )
            with c.begin():
                reuse = c.execute(
                    sa.select(v.c.id).where(
                        v.c.dataset_code == code,
                        v.c.content_fingerprint == digest,
                        v.c.state == "published",
                    )
                ).scalar_one_or_none()
                vid = reuse or str(uuid4())
                if not reuse:
                    for _, (sid, name, _) in SHOPS.items():
                        c.execute(
                            insert(t["shops"])
                            .values(id=sid, shop_name=name, status="active")
                            .on_conflict_do_nothing()
                        )
                    c.execute(
                        v.insert().values(
                            id=vid,
                            dataset_code=code,
                            origin_kind="source",
                            state="draft",
                            source_system="warehouse",
                            source_locator="cubeappdata."
                            + TABLES[code]
                            + " | cubeappdata."
                            + MASTER_TABLE,
                            row_schema_revision=REVISION,
                            interpretation_ref=evidence,
                            producer_revision=producer(),
                            retained_input_ref=archive,
                            received_at=sa.func.clock_timestamp(),
                            period_start=end - timedelta(days=59),
                            period_end=end,
                        )
                    )
                    facts = t[FACTS[code]]
                    for i in range(0, len(rows), 1000):
                        c.execute(
                            facts.insert(),
                            [
                                dict(row, id=str(uuid4()), dataset_version_id=vid)
                                for row in rows[i : i + 1000]
                            ],
                        )
                    # 从PG重读必要字段逐格比对，不只验证插入行数。
                    actual = (
                        c.execute(
                            sa.select(*[facts.c[k] for k in rows[0]]).where(
                                facts.c.dataset_version_id == vid
                            )
                        )
                        .mappings()
                        .all()
                    )
                    if fingerprint(
                        [dict(x) for x in sorted(actual, key=lambda x: x["source_row_key"])]
                    ) != fingerprint(sorted(rows, key=lambda x: x["source_row_key"])):
                        raise ValueError("SOURCE_PG_CONTENT_MISMATCH")
                    for _, (sid, _, _) in SHOPS.items():
                        cov = t["coverage_records"]
                        c.execute(
                            cov.insert().values(
                                id=str(uuid4()),
                                dataset_version_id=vid,
                                scope_kind="shop",
                                shop_id=sid,
                                window_start=end - timedelta(days=59),
                                window_end=end,
                                availability_state="observed"
                                if any(x["shop_id"] == sid for x in rows)
                                else "no_records",
                                completeness_state="unknown",
                                reason_code="UPSTREAM_COMPLETENESS_UNKNOWN",
                                evidence_ref=evidence,
                                checked_at=sa.func.clock_timestamp(),
                            )
                        )
                    c.execute(
                        v.update()
                        .where(v.c.id == vid)
                        .values(
                            state="validated",
                            content_fingerprint=digest,
                            validation_evidence_ref=evidence,
                        )
                    )
                    c.execute(v.update().where(v.c.id == vid).values(state="published"))
                c.execute(
                    insert(current)
                    .values(dataset_code=code, version_id=vid)
                    .on_conflict_do_update(
                        index_elements=[current.c.dataset_code], set_={"version_id": vid}
                    )
                )
                c.execute(
                    task.update()
                    .where(task.c.id == run)
                    .values(
                        status="succeeded",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        result_version_id=vid,
                        evidence_ref=evidence,
                    )
                )
            return dict(
                status="reused" if reuse else "published",
                version_id=vid,
                rows=len(rows),
                observed_to=str(max(x["period_end"] for x in rows)),
            )
        except Exception:
            c.rollback()
            with c.begin():
                c.execute(
                    task.update()
                    .where(task.c.id == run, task.c.status == "running")
                    .values(
                        status="failed",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        error_code="M5_RECEPTION_FAILED",
                    )
                )
            raise
        finally:
            c.rollback()
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m5-source-report'))"))
            c.commit()


def publish_reports(engine, root):
    """从同次成功的三角色生成最新/前一期滚动窗和已覆盖月报；固定输入，零补造。"""
    t = reflect(engine)
    v, rpt, current = t["dataset_versions"], t["aftersales_reports"], t["current_datasets"]
    with engine.connect() as c:
        locked = c.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext('m5-source-report'))")
        ).scalar_one()
        c.commit()
        if not locked:
            raise ValueError("M5_SYNC_RUNNING")
        try:
            with c.begin():
                heads = {
                    x["dataset_code"]: dict(x)
                    for x in c.execute(
                        sa.select(v)
                        .join(current, current.c.version_id == v.c.id)
                        .where(v.c.dataset_code.in_(list(FACTS)), v.c.state == "published")
                    ).mappings()
                }
                if set(heads) != set(FACTS):
                    raise ValueError("M5_INPUTS_MISSING")
                target_version = c.scalar(
                    sa.select(current.c.version_id)
                    .join(v, v.c.id == current.c.version_id)
                    .where(current.c.dataset_code == "refund_targets", v.c.state == "published")
                )
                end = min(x["period_end"] for x in heads.values())
                start = max(x["period_start"] for x in heads.values())
                params = rule_parameters(targets_available=bool(target_version))
                rhash = fingerprint(params)
                rules = t["rule_sets"]
                rule_id = c.execute(
                    sa.select(rules.c.id).where(
                        rules.c.set_code == "m5-reference", rules.c.revision == rhash
                    )
                ).scalar_one_or_none()
                if not rule_id:
                    rule_id = str(uuid4())
                    c.execute(
                        rules.insert().values(
                            id=rule_id,
                            set_code="m5-reference",
                            revision=rhash,
                            domain="aftersales",
                            schema_revision="v1",
                            parameters=params,
                            confirmation_status="partially_confirmed",
                            source_locator="docs/m5-aftersales-implementation.md",
                            content_sha256=rhash,
                            evidence_note=NOTE,
                        )
                    )
                previous_month_end = end.replace(day=1) - timedelta(days=1)
                periods = [("custom", end - timedelta(days=7)), ("custom", end)]
                if previous_month_end.replace(day=1) >= start:
                    periods.insert(0, ("monthly", previous_month_end))
                outputs = []
                for kind, stop in periods:
                    begin = stop.replace(day=1) if kind == "monthly" else stop - timedelta(days=29)
                    if begin < start:
                        continue
                    previous = c.execute(
                        sa.select(rpt.c.id)
                        .join(v, v.c.id == rpt.c.dataset_version_id)
                        .where(
                            rpt.c.report_type == kind,
                            rpt.c.period_end < stop,
                            v.c.state == "published",
                        )
                        .order_by(rpt.c.period_end.desc(), rpt.c.revision_no.desc(), rpt.c.id)
                        .limit(1)
                    ).scalar_one_or_none()
                    digest = fingerprint(
                        dict(
                            revision=REVISION,
                            producer=producer(),
                            kind=kind,
                            start=begin,
                            end=stop,
                            inputs={k: x["id"] for k, x in heads.items()},
                            targets=target_version,
                            rule=rule_id,
                            previous=previous,
                        )
                    )
                    existing = c.execute(
                        sa.select(rpt.c.id)
                        .join(v, v.c.id == rpt.c.dataset_version_id)
                        .where(
                            v.c.dataset_code == "aftersales_report",
                            v.c.content_fingerprint == digest,
                            v.c.state == "published",
                        )
                    ).scalar_one_or_none()
                    if existing:
                        outputs.append(existing)
                        continue
                    vid, rid = str(uuid4()), str(uuid4())
                    evidence = private(
                        Path(root) / vid / "report.json",
                        canonical(
                            dict(
                                fingerprint=digest,
                                inputs={k: x["id"] for k, x in heads.items()},
                                targets=target_version,
                                start=begin,
                                end=stop,
                                note=NOTE,
                            )
                        ),
                    )
                    c.execute(
                        v.insert().values(
                            id=vid,
                            dataset_code="aftersales_report",
                            origin_kind="derived",
                            state="draft",
                            row_schema_revision=REVISION,
                            interpretation_ref=evidence,
                            producer_revision=producer(),
                            retained_input_ref=evidence,
                            period_start=begin,
                            period_end=stop,
                            rule_set_id=rule_id,
                        )
                    )
                    for code, head in heads.items():
                        c.execute(
                            t["version_inputs"]
                            .insert()
                            .values(
                                output_version_id=vid,
                                input_role=ROLES[code],
                                input_version_id=head["id"],
                                window_start=begin,
                                window_end=stop,
                                usage_note="J1确认日/J2J3支付日；参考部分报告；不可视为同批订单",
                            )
                        )
                    if target_version:
                        c.execute(
                            t["version_inputs"]
                            .insert()
                            .values(
                                output_version_id=vid,
                                input_role="targets",
                                input_version_id=target_version,
                                window_start=begin,
                                window_end=stop,
                                usage_note="用户共享周期区间目标；新报告固定当前目标，非历史当时目标",
                            )
                        )
                    revision = (
                        c.execute(
                            sa.select(sa.func.max(rpt.c.revision_no)).where(
                                rpt.c.report_type == kind,
                                rpt.c.period_start == begin,
                                rpt.c.period_end == stop,
                            )
                        ).scalar_one()
                        or 0
                    ) + 1
                    c.execute(
                        rpt.insert().values(
                            id=rid,
                            dataset_version_id=vid,
                            report_type=kind,
                            period_start=begin,
                            period_end=stop,
                            revision_no=revision,
                            source_generated_time_status="unknown",
                            source_generated_time_note="本地派生，源无报告生成时间",
                            previous_report_id=previous,
                            previous_comparison_status="available" if previous else "missing",
                            previous_comparison_reason=None
                            if previous
                            else "未有同类型上期真实报告",
                            yoy_comparison_status="missing",
                            yoy_comparison_reason="当前接入窗口无去年同期间输入",
                            evidence_note=NOTE,
                        )
                    )
                    c.execute(
                        v.update()
                        .where(v.c.id == vid)
                        .values(
                            state="validated",
                            content_fingerprint=digest,
                            validation_evidence_ref=evidence,
                        )
                    )
                    c.execute(v.update().where(v.c.id == vid).values(state="published"))
                    outputs.append(rid)
                # 旧期补报不得倒退入口；本次仅发布实际可读的最新默认修订。
                latest = c.execute(
                    sa.select(rpt.c.dataset_version_id)
                    .join(v, v.c.id == rpt.c.dataset_version_id)
                    .where(v.c.state == "published")
                    .order_by(
                        rpt.c.period_end.desc(),
                        sa.case(
                            (rpt.c.report_type == "monthly", 0),
                            (rpt.c.report_type == "weekly", 1),
                            else_=2,
                        ),
                        rpt.c.revision_no.desc(),
                        rpt.c.period_start.desc(),
                        rpt.c.id,
                    )
                    .limit(1)
                ).scalar_one()
                c.execute(
                    insert(current)
                    .values(dataset_code="aftersales_report", version_id=latest)
                    .on_conflict_do_update(
                        index_elements=[current.c.dataset_code], set_={"version_id": latest}
                    )
                )
            return dict(status="published", version_id=latest, reports=len(outputs))
        finally:
            c.rollback()
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m5-source-report'))"))
            c.commit()
