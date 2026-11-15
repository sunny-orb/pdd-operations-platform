"""历史原始输入的保留规划与离线传输；不删除数据库事实或封版元数据。"""

import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath

import sqlalchemy as sa

from app.modules.archive_payload import open_payload
from app.modules.archive_store import _safe, store_for

PAYLOADS = {
    "input.ndjson",
    "normalized.ndjson",
    "product_daily.json",
    "product_promotion.json",
    "profile_records.json",
}


def protected_versions(current, versions, tasks, edges):
    """保护实际当前、上一成功发布及传递依赖；无任务序列的来源多保留最新两版。"""
    by_id = {v["id"]: v for v in versions}
    keep = set(current.values())
    for code, head in current.items():
        previous = next(
            (
                t["result_version_id"]
                for t in reversed(tasks)
                if t["result_version_id"] in by_id
                and by_id[t["result_version_id"]]["dataset_code"] == code
                and t["result_version_id"] != head
            ),
            None,
        )
        if previous:
            keep.add(previous)
        else:
            candidates = sorted(
                (v for v in versions if v["dataset_code"] == code),
                key=lambda v: str(v["created_at"]),
                reverse=True,
            )
            keep.update(v["id"] for v in candidates[:2])
    while True:
        expanded = keep | {i for o, i in edges if o in keep}
        if expanded == keep:
            return keep
        keep = expanded


def safe_member(root, relative):
    rel = PurePosixPath(relative)
    if (
        rel.is_absolute()
        or not rel.parts
        or any(p in (".", "..") for p in relative.split("/"))
        or "\\" in relative
    ):
        raise ValueError("ARCHIVE_PATH")
    path = _safe(Path(root) / str(rel))
    if not path.is_relative_to(_safe(root)):
        raise ValueError("ARCHIVE_PATH")
    return path


def file_record(root, path):
    path = _safe(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("ARCHIVE_PATH")
    digest = hashlib.sha256()
    size = 0
    with open_payload(path) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    after = path.stat()
    if (info.st_ino, info.st_size, info.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("ARCHIVE_CHANGED")
    return dict(path=str(path.relative_to(root)), size=size, sha256=digest.hexdigest())


def plan_retention(engine, root):
    """只读RR快照；返回可离线的payload路径，不自行判定已备份，不清理新/运行批次。"""
    root = _safe(root)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
        c.execute(sa.text("SET TRANSACTION READ ONLY"))
        if c.scalar(sa.text("SELECT count(*) FROM public.task_runs WHERE status='running'")):
            raise ValueError("ARCHIVE_SYNC_BUSY")
        versions = list(
            c.execute(
                sa.text(
                    "SELECT id,dataset_code,created_at,retained_input_ref,interpretation_ref,"
                    "validation_evidence_ref FROM public.dataset_versions WHERE state='published'"
                )
            ).mappings()
        )
        current = dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM public.current_datasets")).all()
        )
        tasks = list(
            c.execute(
                sa.text(
                    "SELECT id,result_version_id,evidence_ref FROM public.task_runs "
                    "WHERE status='succeeded' ORDER BY finished_at,id"
                )
            ).mappings()
        )
        edges = c.execute(
            sa.text("SELECT output_version_id,input_version_id FROM public.version_inputs")
        ).all()
    keep = protected_versions(current, versions, tasks, edges)
    protected_batches = set()
    known_batches = set()
    for version in versions:
        ref = version["retained_input_ref"]
        if ref and Path(ref).is_relative_to(root):
            known_batches.add("/".join(Path(ref).relative_to(root).parts[:2]))
    for v in versions:
        if v["id"] not in keep:
            continue
        for key in ("retained_input_ref", "interpretation_ref", "validation_evidence_ref"):
            if not v[key]:
                continue
            ref = Path(v[key])
            if ref.is_relative_to(root):
                relative = ref.relative_to(root)
                protected_batches.add("/".join(relative.parts[:2]))
    candidates = []
    for path in root.glob("*/*/*"):
        relative = str(path.relative_to(root))
        if (
            path.name in PAYLOADS
            and "/".join(relative.split("/")[:2]) in known_batches
            and "/".join(relative.split("/")[:2]) not in protected_batches
        ):
            _safe(path)
            if not path.is_file() or path.lstat().st_nlink != 1:
                raise ValueError("ARCHIVE_PATH")
            candidates.append(relative)
    # Task evidence is never a payload candidate, even if a future writer changes its name.
    evidence = {t["evidence_ref"] for t in tasks if t["evidence_ref"]}
    candidates = [p for p in candidates if str(root / p) not in evidence]
    return dict(
        current=current,
        protected_versions=sorted(keep),
        protected_batches=sorted(protected_batches),
        candidates=sorted(candidates),
    )


def export_bundle(root, items, output):
    """流式压缩，不在服务器复制另一份大文件；变更/越界拒绝。"""
    root = _safe(root)
    for item in items:
        path = safe_member(root, item["path"])
        if file_record(root, path) != {k: item[k] for k in ("path", "size", "sha256")}:
            raise ValueError("ARCHIVE_CHANGED")
    content = json.dumps(items, sort_keys=True).encode()
    with tarfile.open(fileobj=output, mode="w|gz") as archive:
        info = tarfile.TarInfo("archive-manifest.json")
        info.size, info.mode = len(content), 0o600
        archive.addfile(info, io.BytesIO(content))
        for item in items:
            path = safe_member(root, item["path"])
            info = tarfile.TarInfo(item["path"])
            info.size, info.mode = item["size"], 0o600
            with open_payload(path) as source:
                archive.addfile(info, source)


def verify_bundle(source):
    """只接受清单中的普通文件，逐字节摘要验证，拒绝额外成员/重复/链接。"""
    with tarfile.open(fileobj=source, mode="r|gz") as archive:
        first = archive.next()
        if (
            first is None
            or first.name != "archive-manifest.json"
            or not first.isfile()
            or first.size > 4 * 1024**2
        ):
            raise ValueError("ARCHIVE_MANIFEST")
        items = json.load(archive.extractfile(first))
        expected = {x["path"]: x for x in items}
        if len(expected) != len(items):
            raise ValueError("ARCHIVE_MANIFEST")
        for name in expected:
            safe_member(Path("/archive-validation"), name)
        found = set()
        while member := archive.next():
            if not member.isfile() or member.name not in expected or member.name in found:
                raise ValueError("ARCHIVE_MEMBER")
            item = expected[member.name]
            if member.size != item["size"]:
                raise ValueError("ARCHIVE_CHANGED")
            digest = hashlib.sha256()
            stream = archive.extractfile(member)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise ValueError("ARCHIVE_CHANGED")
            found.add(member.name)
        if found != set(expected):
            raise ValueError("ARCHIVE_MISSING")
    return items


def restore_bundle(source, root):
    """先校验整个本地包，再恢复缺失文件；既有相同文件跳过，差异绝不覆盖。"""
    root = _safe(root)
    if not source.seekable():
        raise ValueError("ARCHIVE_SEEK_REQUIRED")
    items = verify_bundle(source)
    existing = set()
    for item in items:
        path = safe_member(root, item["path"])
        if path.exists():
            if file_record(root, path) != {k: item[k] for k in ("path", "size", "sha256")}:
                raise ValueError("ARCHIVE_EXISTS")
            existing.add(item["path"])
    source.seek(0)
    with tarfile.open(fileobj=source, mode="r|gz") as archive:
        for member in archive:
            if member.name == "archive-manifest.json" or member.name in existing:
                continue
            path = safe_member(root, member.name)
            store_for(path).write_stream(path, archive.extractfile(member), member.size)
    return len(items) - len(existing)


def apply_verified_plan(engine, root, plan, receipt):
    """受信本机核验回执后的唯一删除入口；锁住所有发布器，重算保护集合。"""
    import fcntl
    import re
    from uuid import uuid4

    from app.modules.sales.datasets import SPECS

    if (
        receipt.get("verified") is not True
        or not re.fullmatch("[0-9a-f]{64}", receipt.get("backup_sha256", ""))
        or not Path(receipt.get("local_path", "")).is_absolute()
    ):
        raise ValueError("ARCHIVE_BACKUP_REQUIRED")
    root = _safe(root)
    keys = [
        "project-source-sync",
        "m1-product-board",
        "m5-source-report",
        "nmc-weather-publication",
        *("m3-" + code for code in sorted({"shop_daily", "sales_targets", *SPECS})),
    ]
    acquired = []
    with engine.connect() as connection:
        try:
            for key in keys:
                if not connection.scalar(
                    sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), dict(key=key)
                ):
                    raise ValueError("ARCHIVE_SYNC_BUSY")
                acquired.append(key)
            connection.commit()
            lock = os.open(root / ".archive.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                fresh = plan_retention(engine, root)
                if fresh["current"] != plan["current"] or not set(plan["candidates"]) <= set(
                    fresh["candidates"]
                ):
                    raise ValueError("ARCHIVE_PLAN_STALE")
                records = {x["path"]: x for x in receipt["items"]}
                if not set(plan["candidates"]) <= set(records):
                    raise ValueError("ARCHIVE_BACKUP_REQUIRED")
                for name in plan["candidates"]:
                    item = records[name]
                    if file_record(root, safe_member(root, name)) != {
                        k: item[k] for k in ("path", "size", "sha256")
                    }:
                        raise ValueError("ARCHIVE_CHANGED")
                maintenance = _safe(root.parent / "maintenance")
                maintenance.mkdir(mode=0o700, parents=True, exist_ok=True)
                identifier = "retention-" + uuid4().hex
                fd = os.open(
                    maintenance / (identifier + ".json"),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "w") as out:
                    json.dump(dict(plan=plan, receipt=receipt), out)
                    out.flush()
                    os.fsync(out.fileno())
                deleted = []
                fd = os.open(
                    maintenance / (identifier + ".jsonl"),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "w") as journal:
                    for name in plan["candidates"]:
                        path = safe_member(root, name)
                        path.unlink()
                        journal.write(json.dumps(dict(deleted=name)) + "\n")
                        journal.flush()
                        os.fsync(journal.fileno())
                        deleted.append(name)
                    journal.write(json.dumps(dict(complete=True, deleted=len(deleted))) + "\n")
                    journal.flush()
                    os.fsync(journal.fileno())
                return dict(
                    deleted=len(deleted),
                    bytes=sum(records[n]["size"] for n in deleted),
                    receipt=str(maintenance / (identifier + ".json")),
                )
            finally:
                os.close(lock)
        finally:
            connection.rollback()
            for key in reversed(acquired):
                connection.execute(
                    sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), dict(key=key)
                )
            connection.commit()
