"""串行接收、私有原文证据与原子发布；失败保留旧版。"""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

from app.connectors.weather import BULLETIN_URL, MID_RANGE_URL
from app.modules.archive_store import require_archive, store_for
from app.modules.weather.parser import PARSER_REVISION, build_document

LOCK_KEY = "nmc-weather-publication"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _write(path, value):
    store_for(path).check(len(canonical(value).encode()))
    path.write_text(canonical(value), encoding="utf-8")
    os.chmod(path, 0o600)


def _lease(tasks, run):
    """租约属于当前running worker且未过期；真实时钟不能用事务开始时刻替代。"""
    return (
        tasks.c.id == run,
        tasks.c.status == "running",
        tasks.c.worker_ref == run,
        tasks.c.lease_expires_at > sa.func.clock_timestamp(),
    )


def _assert_lease(c, tasks, run):
    if c.scalar(sa.select(tasks.c.id).where(*_lease(tasks, run)).with_for_update()) is None:
        raise ValueError("TASK_LEASE_LOST")


def _reuse_evidence(existing, manifest):
    """兼容无sidecar的既有天气归档；用发布指纹及清单绑定核验后返回证据路径。"""
    document_path = Path(require_archive(existing["retained_input_ref"]))
    evidence_path = Path(require_archive(existing["validation_evidence_ref"]))
    if str(evidence_path) != existing["interpretation_ref"]:
        raise ValueError("ARCHIVE_MANIFEST")
    body = json.loads(document_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        canonical({"parser": PARSER_REVISION, "document": body}).encode()
    ).hexdigest()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (
        digest != manifest["content_sha256"]
        or evidence != {**manifest, "publication_id": existing["id"]}
        or document_path != evidence_path.parent / manifest["document_file"]
    ):
        raise ValueError("ARCHIVE_CHECKSUM")
    return str(evidence_path)


def publish(engine, receive, archive_root):
    """receive()->两官文；返回真实父版本/文档ID和复用状态，接收不持发布事务。"""
    metadata = sa.MetaData()
    tables = {
        name: sa.Table(name, metadata, schema="public", autoload_with=engine, resolve_fks=False)
        for name in ("weather_publications", "dataset_versions", "current_datasets", "task_runs")
    }
    weather, versions, current, tasks = (tables[name] for name in tables)
    run = str(uuid4())
    folder = Path(archive_root) / run
    evidence = None
    candidate_created = False
    with engine.connect() as c:
        if not c.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": LOCK_KEY}):
            c.rollback()
            raise ValueError("SYNC_ALREADY_RUNNING")
        c.commit()
        try:
            with c.begin():
                now = c.scalar(sa.select(sa.func.clock_timestamp()))
                c.execute(
                    tasks.update()
                    .where(tasks.c.job_code == "sync_weather", tasks.c.status == "running")
                    .values(
                        status="abandoned",
                        finished_at=now,
                        lease_expires_at=None,
                        error_code="WORKER_LOST",
                        error_summary="天气接收进程已结束，旧发布保留",
                    )
                )
                c.execute(
                    tasks.insert().values(
                        id=run,
                        job_code="sync_weather",
                        request_ref=PARSER_REVISION,
                        lock_key=LOCK_KEY,
                        status="running",
                        queued_at=now,
                        started_at=now,
                        worker_ref=run,
                        lease_expires_at=now + timedelta(seconds=180),
                        evidence_ref=evidence,
                    )
                )
            try:
                try:
                    received = receive()
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError("SOURCE_RECEIVE_FAILED") from exc
                with c.begin():
                    _assert_lease(c, tasks, run)
                day, document = build_document(received)
                body = document.model_dump()
                digest = hashlib.sha256(
                    canonical({"parser": PARSER_REVISION, "document": body}).encode()
                ).hexdigest()
                manifest = dict(
                    parser_revision=PARSER_REVISION,
                    content_sha256=digest,
                    source_system="nmc_official",
                    issue_date=str(day),
                    document_file="document.json",
                )
                with c.begin():
                    _assert_lease(c, tasks, run)
                    existing = (
                        c.execute(
                            sa.select(
                                weather.c.id,
                                versions.c.retained_input_ref,
                                versions.c.validation_evidence_ref,
                                versions.c.interpretation_ref,
                            )
                            .join(versions, versions.c.id == weather.c.id)
                            .where(
                                weather.c.content_sha256 == digest, versions.c.state == "published"
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    publication_id = existing["id"] if existing else str(uuid4())
                    if existing:
                        evidence = _reuse_evidence(existing, manifest)
                    else:
                        # 仅真正的新候选创建目录，重复检查/锁冲突不留下空目录。
                        store_for(folder / "document.json").check(len(canonical(body).encode()))
                        folder.mkdir(parents=True, mode=0o700)
                        candidate_created = True
                        os.chmod(folder.parent, 0o700)
                        os.chmod(folder, 0o700)
                        evidence = str(folder / "manifest.json")
                        _write(folder / "document.json", body)
                        _write(
                            folder / "manifest.json", {**manifest, "publication_id": publication_id}
                        )
                        revision = (
                            c.scalar(
                                sa.select(
                                    sa.func.coalesce(sa.func.max(weather.c.revision_no), 0)
                                ).where(weather.c.issue_date == day)
                            )
                            + 1
                        )
                        c.execute(
                            versions.insert().values(
                                id=publication_id,
                                dataset_code="weather_publication",
                                origin_kind="source",
                                state="draft",
                                source_system="nmc_official",
                                source_locator=BULLETIN_URL + " | " + MID_RANGE_URL,
                                row_schema_revision=PARSER_REVISION,
                                interpretation_ref=evidence,
                                producer_revision=PARSER_REVISION,
                                period_start=day,
                                period_end=day + timedelta(days=6),
                                retained_input_ref=str(folder / "document.json"),
                            )
                        )
                        c.execute(
                            weather.insert().values(
                                id=publication_id,
                                issue_date=day,
                                revision_no=revision,
                                source_system="nmc_official",
                                content_sha256=digest,
                                parser_revision=PARSER_REVISION,
                                document=body,
                            )
                        )
                        c.execute(
                            versions.update()
                            .where(versions.c.id == publication_id)
                            .values(
                                state="validated",
                                content_fingerprint="nmc:" + digest,
                                validation_evidence_ref=evidence,
                                received_at=sa.func.clock_timestamp(),
                            )
                        )
                        c.execute(
                            versions.update()
                            .where(versions.c.id == publication_id)
                            .values(state="published")
                        )
                    latest = c.scalar(
                        sa.select(weather.c.id)
                        .join(versions, versions.c.id == weather.c.id)
                        .where(versions.c.state == "published")
                        .order_by(weather.c.issue_date.desc(), weather.c.revision_no.desc())
                        .limit(1)
                    )
                    from sqlalchemy.dialects.postgresql import insert

                    c.execute(
                        insert(current)
                        .values(dataset_code="weather_publication", version_id=latest)
                        .on_conflict_do_update(
                            index_elements=[current.c.dataset_code], set_={"version_id": latest}
                        )
                    )
                    completed = c.execute(
                        tasks.update()
                        .where(*_lease(tasks, run))
                        .values(
                            status="succeeded",
                            finished_at=sa.func.clock_timestamp(),
                            lease_expires_at=None,
                            result_version_id=publication_id,
                            evidence_ref=evidence,
                            source_updated_text=" | ".join(s.issue_time for s in received),
                            source_update_observed_at=sa.func.clock_timestamp(),
                        )
                    )
                    if completed.rowcount != 1:
                        raise ValueError("TASK_LEASE_LOST")
                return dict(
                    status="succeeded",
                    publication_id=publication_id,
                    version_id=publication_id,
                    reused=existing is not None,
                )
            except Exception as exc:
                code = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    and str(exc)
                    in {
                        "SOURCE_IDENTITY_INVALID",
                        "SOURCE_TIME_INVALID",
                        "SOURCE_BODY_EMPTY",
                        "SOURCE_TOO_LARGE",
                        "SOURCE_REDIRECT_REJECTED",
                        "SOURCE_RECEIVE_FAILED",
                        "TASK_LEASE_LOST",
                    }
                    else "WEATHER_PUBLICATION_FAILED"
                )
                if c.in_transaction():
                    c.rollback()
                if candidate_created:
                    try:
                        _write(folder / "manifest.json", {"status": "failed", "error_code": code})
                    except (OSError, ValueError):
                        # 磁盘故障不能遮蔽任务失败记录，更不能覆盖旧版归档。
                        evidence = None
                with c.begin():
                    c.execute(
                        tasks.update()
                        .where(
                            tasks.c.id == run,
                            tasks.c.status == "running",
                            tasks.c.worker_ref == run,
                        )
                        .values(
                            status="failed",
                            finished_at=sa.func.clock_timestamp(),
                            lease_expires_at=None,
                            error_code=code,
                            error_summary="天气接收或校验未成功，保留最近成功发布",
                            evidence_ref=evidence,
                        )
                    )
                raise ValueError(code) from exc
        finally:
            if c.in_transaction():
                c.rollback()
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": LOCK_KEY})
            c.commit()
