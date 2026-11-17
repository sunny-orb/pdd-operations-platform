"""核验并清理旧JST任务留下的重复输入，只操作项目售后归档。"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.jobs.sync_shop_daily import runtime_engine
from app.modules.archive_compaction import _identity
from app.modules.archive_payload import open_payload
from app.modules.archive_store import _safe, require_archive

OLD_SOURCES = ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales")


def scan(c, folder):
    active = set(
        c.execute(
            sa.text("""SELECT v.dataset_code FROM current_datasets d
        JOIN version_inputs i ON i.output_version_id=d.version_id
        JOIN dataset_versions v ON v.id=i.input_version_id
        WHERE d.dataset_code='aftersales_report'""")
        ).scalars()
    )
    if not {"rpa_returns", "rpa_shipped_sales", "rpa_cancelled_sales"} <= active:
        raise ValueError("RPA_CUTOVER_REQUIRED")
    if c.scalar(
        sa.text("""SELECT EXISTS (SELECT 1 FROM task_runs
        WHERE status='running' AND lease_expires_at > clock_timestamp())""")
    ):
        raise ValueError("ARCHIVE_SYNC_BUSY")

    # 封版输入、业务证明及任务回执都仍然可读；只有不被直接引用的原始文件可清理。
    refs = {
        Path(ref)
        for ref in c.execute(
            sa.text("""SELECT retained_input_ref FROM dataset_versions
        UNION SELECT interpretation_ref FROM dataset_versions
        UNION SELECT validation_evidence_ref FROM dataset_versions
        UNION SELECT evidence_ref FROM coverage_records
        UNION SELECT evidence_ref FROM task_runs
        UNION SELECT request_ref FROM task_runs
        UNION SELECT confirmation_ref FROM rule_sets
        UNION SELECT listed_date_evidence_ref FROM profile_listing_links""")
        ).scalars()
        if ref
    }
    return c.execute(
        sa.text("""SELECT DISTINCT t.evidence_ref,d.retained_input_ref
        FROM task_runs t JOIN dataset_versions d ON d.id=t.result_version_id
        WHERE t.status='succeeded' AND d.state='published'
          AND d.dataset_code IN :sources AND t.job_code='sync_'||d.dataset_code
          AND t.evidence_ref IS DISTINCT FROM d.interpretation_ref
          AND t.evidence_ref LIKE :folder ORDER BY 1,2""").bindparams(
            sa.bindparam("sources", expanding=True)
        ),
        {"sources": OLD_SOURCES, "folder": str(folder) + "/%"},
    ).all(), refs


def digest(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("ARCHIVE_PATH")
    with open_payload(path) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cleanup(c, root, *, apply=False):
    folder = _safe(root / "aftersales")
    pairs, refs = scan(c, folder)
    counts = {"files": 0, "allocated_bytes": 0, "different_or_unverified": 0}
    verified = {}
    for evidence, retained in pairs:
        policy, original = Path(evidence), Path(retained or "")
        candidate = policy.with_name("input.ndjson")
        if (
            policy.name != "interpretation.json"
            or candidate.parent.parent != folder
            or not re.fullmatch(r"[0-9a-f-]{36}", candidate.parent.name)
            or candidate == original
            or not candidate.is_file()
        ):
            continue
        candidate = _safe(candidate)
        sidecar = _safe(candidate.with_suffix(".ndjson.sha256"))
        if any(path in refs for path in (candidate, candidate.parent, sidecar)):
            continue
        try:
            if not original.is_relative_to(folder) or original.name != "input.ndjson":
                raise ValueError("ARCHIVE_PATH")
            original = _safe(original)
            # 早期封版输入没有独立校验文件。仅在原件仍在且两份内容完全一致时去重。
            try:
                require_archive(original)
            except ValueError as exc:
                if str(exc) != "ARCHIVE_RESTORE_REQUIRED" or not original.is_file():
                    raise
            duplicate_digest = digest(candidate)
            if digest(original) != duplicate_digest:
                raise ValueError("ARCHIVE_CONTENT_MISMATCH")
            files = (candidate, sidecar) if sidecar.exists() else (candidate,)
            if sidecar.exists():
                info = sidecar.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or sidecar.read_text().strip() != duplicate_digest
                ):
                    raise ValueError("ARCHIVE_CHECKSUM")
            verified.update((file, file.lstat()) for file in files)
        except (ValueError, OSError):
            counts["different_or_unverified"] += 1
    # 锁内先完整核验，再逐个核对文件身份；任务解释及封版原件全程保留。
    for file, before in verified.items():
        if file.lstat() != before:
            raise ValueError("ARCHIVE_CHANGED")
        if apply:
            file.unlink()
        counts["files"] += 1
        counts["allocated_bytes"] += before.st_blocks * 512
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = _safe(ROOT / "var/ingestion")
    engine = runtime_engine()
    try:
        with engine.connect() as c:
            c.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            _identity(c, root)
            for key in ("project-source-sync", "m5-source-report"):
                if not c.scalar(
                    sa.text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": key},
                ):
                    raise ValueError("ARCHIVE_SYNC_BUSY")
            fd = os.open(root / ".archive.lock", os.O_RDWR | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print(
                    json.dumps(
                        dict(applied=args.apply, **cleanup(c, root, apply=args.apply))
                    )
                )
            finally:
                os.close(fd)
    except (OSError, ValueError, sa.exc.SQLAlchemyError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "code": str(exc)
                    if isinstance(exc, ValueError)
                    else "CLEANUP_FAILED",
                }
            )
        )
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
