"""M1试行评分原子发布；共用接收互斥、固定四输入、失败不推进指针。"""

import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.modules.archive_store import write_archive
from app.modules.product_board.repository import CODES, snapshot

from .service import METRIC_CODES, build_bundle, digest, producer_hash, validate_bundle

REF = "docs/m1-model-reuse.md#本次接入v2的最小设计"


def identity(*parts):
    return str(uuid5(NAMESPACE_URL, "/".join(map(str, parts))))


def _archive(path, bundle):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    file = path / "publication.json"
    write_archive(file, json.dumps(bundle, ensure_ascii=False, default=str, sort_keys=True))
    os.chmod(file, 0o600)
    return str(file)


def _version(c, tables, code, fingerprint, producer, evidence, *, rule=None):
    table = tables["dataset_versions"]
    vid = identity("m1-trial", code, fingerprint)
    old = c.execute(sa.select(table.c.state).where(table.c.id == vid)).scalar_one_or_none()
    if old:
        if old not in ("validated", "published"):
            raise ValueError("SCORE_VERSION_NOT_SEALED")
        return vid, False
    c.execute(
        table.insert().values(
            id=vid,
            dataset_code=code,
            origin_kind="derived",
            state="draft",
            row_schema_revision="v2-20261028",
            interpretation_ref=evidence,
            producer_revision=producer,
            rule_set_id=rule,
            retained_input_ref=evidence,
        )
    )
    return vid, True


def _edges(c, tables, output, edges):
    c.execute(
        tables["version_inputs"].insert(),
        [
            dict(
                output_version_id=output,
                input_role=role,
                input_version_id=version,
                window_start=start,
                window_end=end,
            )
            for role, version, start, end in edges
        ],
    )


def _seal(c, tables, vid, fingerprint, evidence):
    t = tables["dataset_versions"]
    c.execute(
        t.update()
        .where(t.c.id == vid)
        .values(
            state="validated", content_fingerprint=fingerprint, validation_evidence_ref=evidence
        )
    )


def _persist(c, t, b, evidence):
    params = b["parameters"]
    rule_hash = digest(params)
    rule_id = identity("m1-trial-rule", rule_hash)
    c.execute(
        insert(t["rule_sets"])
        .values(
            id=rule_id,
            set_code="m1-stage-independent-trial",
            revision=rule_hash,
            domain="product_scoring",
            schema_revision="v1",
            parameters=params,
            confirmation_status="partially_confirmed",
            source_locator=REF,
            confirmation_ref=REF,
            content_sha256=rule_hash,
            evidence_note="用户2026-10-28启用新试行标准及M1-01日新增收藏定义；推广沿来源已归因支付日汇总；来源完整性与参照效果未验证，正式放量关闭",
        )
        .on_conflict_do_nothing()
    )
    standard_fp = digest(
        dict(inputs=b["inputs"], calibration=b["calibration"], producer=b["producer"])
    )
    standard_version, new_standard = _version(
        c, t, "score_standards", standard_fp, b["producer"], evidence
    )
    end, start = b["end"], b["start"]
    if new_standard:
        for row in b["scores"]:
            sid = identity(standard_version, row["listing_id"])
            c.execute(
                t["score_standards"]
                .insert()
                .values(
                    id=sid,
                    standard_code="stage-independent/" + row["listing_id"],
                    dataset_version_id=standard_version,
                    listing_id=row["listing_id"],
                    source_listing_key=json.dumps(
                        [row["platform_code"], row["shop_id"], row["product_id"]]
                    ),
                    source_category_system="current_profile",
                    source_category_label=row["category"] or "资料品类缺失",
                    model_category_label=row["category"],
                    basis_kind="proposed",
                    identity_match_status="matched",
                    source_date_status="unknown",
                    source_locator=evidence,
                    evidence_note="已核本地完整K；当期60日/T−14阶段无关试行，非原V6模型；正式放量待验证",
                )
            )
            items = []
            for feature, signal in row["signals"].items():
                spec = signal["standard"]
                bands = spec["bands"] if spec else [None] * 4
                items.append(
                    dict(
                        standard_id=sid,
                        metric_code=METRIC_CODES[feature],
                        basic_threshold=bands[0],
                        good_threshold=bands[1],
                        excellent_threshold=bands[2],
                        outstanding_threshold=bands[3],
                        threshold_status="available" if spec else "missing",
                        precision_kind="full_precision" if spec else "unknown",
                        reference_group_label=spec["reference"] if spec else None,
                        reference_category_system="current_profile" if spec else None,
                        reference_category_label=spec["reference"] if spec else None,
                        declared_sample_count=spec["n"] if spec else None,
                        declared_sample_unit="platform/shop/product窗口中位数" if spec else None,
                        sample_basis_status="verified_in_scope" if spec else "unknown",
                        history_start=start,
                        history_end=end - timedelta(days=14),
                        quantile_method="linear" if spec else None,
                        fallback_level="global"
                        if spec and spec["reference"] == "全品类"
                        else "category"
                        if spec
                        else None,
                        fallback_basis_status="verified_in_scope"
                        if spec and spec["reference"] == "全品类"
                        else "not_used",
                        source_locator=evidence,
                        missing_reason=None if spec else signal["reason"],
                    )
                )
            c.execute(t["score_standard_items"].insert(), items)
        _edges(
            c,
            t,
            standard_version,
            [
                ("product_history", b["inputs"]["product_daily"], start, end - timedelta(days=14)),
                ("ad_current", b["inputs"]["product_promotion"], start, end - timedelta(days=14)),
                ("profile", b["inputs"]["profile_links"], None, None),
            ],
        )
        _seal(c, t, standard_version, standard_fp, evidence)
    score_fp = digest(
        dict(
            inputs=b["inputs"],
            standard=standard_version,
            rule=rule_hash,
            scores=b["scores"],
            producer=b["producer"],
        )
    )
    score_version, new_score = _version(
        c, t, "product_scores", score_fp, b["producer"], evidence, rule=rule_id
    )
    if new_score:
        for row in b["scores"]:
            sid = identity(score_version, row["listing_id"])
            c.execute(
                t["product_scores"]
                .insert()
                .values(
                    id=sid,
                    dataset_version_id=score_version,
                    listing_id=row["listing_id"],
                    standard_id=identity(standard_version, row["listing_id"]),
                    **row["raw_features"],
                    current_lifecycle=None,
                    lifecycle_reason="实际上架日期适用性未核；本次为阶段无关试行",
                    decision_code="unscored"
                    if row["score_status"] == "unscored"
                    else "pending_validation",
                    decision_evidence="新试行标准已启用；收藏按7日新增累计未扣取消及同定义参照试算；完整日未知增长14分不计，正式放量关闭",
                )
            )
            c.execute(
                t["product_score_items"].insert(),
                [
                    dict(
                        score_id=sid,
                        metric_code=METRIC_CODES[feature],
                        signal_status="valid" if signal["points"] is not None else "not_computable",
                        missing_reason_code=None
                        if signal["points"] is not None
                        else "TRIAL_INPUT_OR_STANDARD_PENDING",
                        missing_reason_text=signal["reason"],
                        evidence_context=None
                        if signal["points"] is not None
                        else dict(note=signal["reason"]),
                    )
                    for feature, signal in row["signals"].items()
                ],
            )
        _edges(
            c,
            t,
            score_version,
            [
                ("product_current", b["inputs"]["product_daily"], end - timedelta(days=6), end),
                (
                    "product_previous",
                    b["inputs"]["product_daily"],
                    end - timedelta(days=13),
                    end - timedelta(days=7),
                ),
                ("ad_current", b["inputs"]["product_promotion"], end - timedelta(days=6), end),
                ("profile", b["inputs"]["profile_links"], None, None),
                ("standard", standard_version, None, None),
            ],
        )
        _seal(c, t, score_version, score_fp, evidence)
    for table, parent, key in (
        ("score_standard_items", "score_standards", "standard_id"),
        ("product_score_items", "product_scores", "score_id"),
    ):
        version = standard_version if parent == "score_standards" else score_version
        count = c.execute(
            sa.text(
                f"SELECT count(*) FROM public.{parent} p JOIN public.{table} i "
                f"ON i.{key}=p.id WHERE p.dataset_version_id=:v"
            ),
            dict(v=version),
        ).scalar_one()
        if count != len(b["scores"]) * 10:
            raise ValueError("SCORE_STORED_SIGNAL_COUNT_MISMATCH")
    return dict(score_standards=standard_version, product_scores=score_version)


def publish_scores(engine, *, archive_root=None):
    """只在M1四输入发布成功后调用；同输入幂等，拒绝并发/不相容/代码变动。"""
    code_hash = producer_hash()
    m = sa.MetaData()
    tables = {
        name: sa.Table(name, m, schema="public", autoload_with=engine, resolve_fks=False)
        for name in (
            "dataset_versions",
            "current_datasets",
            "version_inputs",
            "rule_sets",
            "score_standards",
            "score_standard_items",
            "product_scores",
            "product_score_items",
            "task_runs",
        )
    }
    tables["product_score_items"].c.evidence_context.type.none_as_null = True
    run = str(uuid4())
    root = (
        Path(archive_root)
        if archive_root
        else Path(__file__).resolve().parents[5] / "var/scoring/product_board"
    )
    with engine.connect() as c:
        locked = c.execute(
            sa.text("SELECT pg_try_advisory_lock(hashtext('m1-product-board'))")
        ).scalar_one()
        c.commit()
        if not locked:
            raise ValueError("M1_SYNC_OR_SCORING_RUNNING")
        task = tables["task_runs"]
        try:
            with c.begin():
                c.execute(
                    task.update()
                    .where(
                        task.c.job_code == "score_product_board",
                        task.c.status == "running",
                        task.c.lease_expires_at < sa.func.clock_timestamp(),
                    )
                    .values(
                        status="abandoned",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        error_code="LEASE_EXPIRED",
                    )
                )
                now = c.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
                c.execute(
                    task.insert().values(
                        id=run,
                        job_code="score_product_board",
                        request_ref="stage-independent-trial-v1",
                        lock_key="m1-product-board",
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=run,
                        lease_expires_at=now + timedelta(seconds=180),
                    )
                )
            with snapshot(engine) as repo:
                bundle = build_bundle(repo)
            validate_bundle(bundle)
            if producer_hash() != code_hash or bundle["producer"] != code_hash:
                raise ValueError("SCORING_CODE_CHANGED")
            evidence = _archive(root / run, bundle)
            with c.begin():
                current = dict(
                    c.execute(
                        sa.text(
                            "SELECT dataset_code,version_id FROM public.current_datasets "
                            "WHERE dataset_code=ANY(:codes) ORDER BY dataset_code FOR UPDATE"
                        ),
                        dict(codes=list(CODES)),
                    ).all()
                )
                if current != bundle["inputs"]:
                    raise ValueError("SCORE_INPUTS_CHANGED")
                lease = c.execute(
                    sa.select(task.c.lease_expires_at).where(task.c.id == run)
                ).scalar_one()
                if lease < c.execute(sa.select(sa.func.clock_timestamp())).scalar_one():
                    raise ValueError("SCORING_LEASE_EXPIRED")
                versions = _persist(c, tables, bundle, evidence)
                for code, vid in versions.items():
                    version = tables["dataset_versions"]
                    c.execute(
                        version.update()
                        .where(version.c.id == vid, version.c.state == "validated")
                        .values(state="published")
                    )
                    c.execute(
                        insert(tables["current_datasets"])
                        .values(dataset_code=code, version_id=vid)
                        .on_conflict_do_update(
                            index_elements=["dataset_code"], set_=dict(version_id=vid)
                        )
                    )
                c.execute(
                    task.update()
                    .where(task.c.id == run)
                    .values(
                        status="succeeded",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        result_version_id=versions["product_scores"],
                        evidence_ref=evidence,
                    )
                )
            return dict(
                status="published_trial",
                versions=versions,
                products=len(bundle["scores"]),
                as_of_date=str(bundle["end"]),
            )
        except Exception as exc:
            if c.in_transaction():
                c.rollback()
            with c.begin():
                c.execute(
                    task.update()
                    .where(task.c.id == run, task.c.status == "running")
                    .values(
                        status="failed",
                        finished_at=sa.func.clock_timestamp(),
                        lease_expires_at=None,
                        error_code="SCORING_PUBLICATION_FAILED",
                        error_summary=type(exc).__name__,
                    )
                )
            raise
        finally:
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m1-product-board'))"))
            c.commit()
