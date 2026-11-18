"""项目内归档持续压缩复用；只改变物理表示，不删历史输入或改数据库记录。"""

import fcntl
import json
import os
import re
import time
import tomllib
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_budget import (
    PENDING,
    consume,
    inventory,
    maintenance_budget,
    resume_inventory,
)
from app.modules.archive_payload import (
    PAYLOADS,
    _checked_path,
    _read_manifest,
    atomic_bytes,
    safe,
    write_payload,
)
from app.modules.archive_store import _verify, store_for

MIN_PAYLOAD = 256 * 1024
WORKING_BYTES = 64 * 1024**2


def _identity(connection, root):
    """固定生产身份/目录；本地仅本项目库，隔离测试库仅本机随机命名。"""
    database, user = connection.execute(sa.text("SELECT current_database(),current_user")).one()
    if os.environ.get("APP_ENV") == "production":
        if database != "pdd_dashboard" or user != "songbing" or root != Path("/app/var/ingestion"):
            raise ValueError("ARCHIVE_IDENTITY")
    elif re.fullmatch(r"pdd_operations_v2_test_[0-9a-f]{12}", database):
        if user != "pdd_admin" or connection.engine.url.host not in ("127.0.0.1", "localhost"):
            raise ValueError("ARCHIVE_IDENTITY")
    elif (
        database != "pdd_operations"
        or user not in ("pdd_ingest", "pdd_admin")
        or root != Path(__file__).resolve().parents[4] / "var/ingestion"
        or connection.engine.url.host not in ("127.0.0.1", "localhost")
    ):
        raise ValueError("ARCHIVE_IDENTITY")


def _expected_metadata(path):
    """只读可信摘要位置；GC先确认全部输入可鉴真，再解码任何大文件。"""
    sidecar = safe(path.with_name(path.name + ".sha256"))
    if sidecar.is_file():
        expected = sidecar.read_text().strip()
        if not re.fullmatch("[0-9a-f]{64}", expected):
            raise ValueError("ARCHIVE_CHECKSUM")
        return expected, False
    import json

    if path.name == "input.ndjson":
        policy = safe(path.parent / "interpretation.json")
        expected = json.loads(policy.read_text()).get("input_sha256") if policy.is_file() else None
        if not expected:
            raise ValueError("ARCHIVE_RESTORE_REQUIRED")
        return expected, False
    manifest = safe(path.parent / "manifest.json")
    expected = (
        json.loads(manifest.read_text()).get("files", {}).get(path.name)
        if manifest.is_file()
        else None
    )
    if not expected:
        raise ValueError("ARCHIVE_RESTORE_REQUIRED")
    return expected, True


def _expected(path, deadline=None):
    expected, encoded_text = _expected_metadata(path)
    if encoded_text:
        _verify(path, expected, encoded_text=True, deadline=deadline)
        return None
    return expected


def _compact_locked(store, max_files, max_seconds, progress=None):
    deadline = time.monotonic() + max_seconds

    def budget():
        if time.monotonic() >= deadline:
            raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")

    root = store.root
    budget()
    progress = progress if progress is not None else {}
    progress.update(converted=0, collected_blocks=0, removed_temporary_files=0)
    files = dict(inventory(root, repair=True, progress=progress))
    before = sum(info.st_size for info in files.values())
    progress["before_bytes"] = before
    for path in files:
        if re.fullmatch(r"\.pending-[a-z0-9_]{8}", path.name):
            consume(entries=1)
            path.unlink()
            progress["removed_temporary_files"] += 1
    payloads = sorted(p for p in files if p.name in PAYLOADS)
    plain = []
    checked = set()
    manifests = {}
    for path in payloads:
        budget()
        manifest = manifests[path] = _read_manifest(path, checked)
        if manifest is None and files[path].st_size >= MIN_PAYLOAD:
            plain.append(path)
    # 先处理较小历史输入，释放配额后再处理大文件；最多一文件64MiB临时块。
    plain.sort(key=lambda p: (files[p].st_size, str(p)))
    converted = 0
    unverified = set()
    for path in plain:
        if converted >= max_files or time.monotonic() >= deadline:
            break
        try:
            expected = _expected(path, deadline)
        except ValueError as exc:
            if str(exc) != "ARCHIVE_RESTORE_REQUIRED":
                raise
            unverified.add(path)
            continue
        try:
            with path.open("rb") as stream:

                def pieces():
                    while chunk := stream.read(1024 * 1024):
                        consume(len(chunk))
                        yield chunk

                write_payload(
                    path,
                    pieces(),
                    store,
                    replace=True,
                    expected=expected,
                    allowance=WORKING_BYTES,
                    deadline=deadline,
                    base_usage=before + converted * WORKING_BYTES,
                )
        except ValueError as exc:
            if str(exc) != "ARCHIVE_NO_SAVING":
                raise
        else:
            converted += 1
            progress["converted"] = converted
            # 原子替换及新块改变了路径状态；只重读本次改变的清单。
            checked.clear()
            manifests[path] = _read_manifest(path, checked)
    # 所有已发布或失败批次的指针均纳入引用；不只看current，历史报告也完整保留。
    referenced = set()
    if not unverified:
        for manifest in manifests.values():
            budget()
            if manifest is not None:
                referenced.update(item["sha256"] for item in manifest["chunks"])
    garbage = []
    blocks = root / ".payloads"
    if not unverified and blocks.exists():
        for path in sorted(p for p in files if p.is_relative_to(blocks) and p.suffix == ".gz"):
            budget()
            _checked_path(path, checked)
            if not re.fullmatch("[0-9a-f]{64}", path.stem) or path.parent.name != path.stem[:2]:
                raise ValueError("ARCHIVE_PATH")
            if path.stem not in referenced:
                garbage.append(path)
    if garbage:
        # MAGIC/清单损坏可能伪装成明文；全体逻辑输入先按可信摘要验证，拒绝误删可恢复块。
        expected_inputs = []
        for path in payloads:
            budget()
            try:
                expected, encoded_text = _expected_metadata(path)
            except ValueError as exc:
                if str(exc) != "ARCHIVE_RESTORE_REQUIRED":
                    raise
                # 旧输入没有可信摘要时，只保留；不能据它的外观证明任何块已无引用。
                unverified.add(path)
                garbage = []
                break
            expected_inputs.append((path, expected, encoded_text))
        if garbage:
            for path, expected, encoded_text in expected_inputs:
                _verify(path, expected, encoded_text=encoded_text, deadline=deadline)
        budget()
        for path in garbage:
            consume(entries=1)
            path.unlink()
            progress["collected_blocks"] += 1
    return dict(
        status="ok",
        converted=converted,
        collected_blocks=len(garbage),
        removed_temporary_files=progress["removed_temporary_files"],
        unverified_files=len(unverified),
        remaining_plain=max(0, len(set(plain) - unverified) - converted),
        before_bytes=before,
        after_bytes=store.usage(),
    )


def _compact_batch(store, max_files, max_seconds, state, progress):
    """先续扫小批逻辑输入，完整GC独立尝试；GC预算不足也不阻止转换/临时清理进展。"""
    deadline = time.monotonic() + max_seconds

    def candidates():
        resumed = bool(state.get("scan_stack"))
        yield from resume_inventory(store.root, state, progress)
        if resumed:
            # 续扫走到末尾仍有预算时，最多绕回一次，补期间新增/删除造成的位置变化。
            yield from resume_inventory(store.root, state, progress)

    for path, info in candidates():
        if PENDING.fullmatch(path.name):
            consume(entries=1)
            path.unlink()
            progress["removed_temporary_files"] += 1
            continue
        if path.name not in PAYLOADS or info.st_size < MIN_PAYLOAD:
            continue
        if _read_manifest(path, set()) is not None:
            continue
        try:
            expected = _expected(path, deadline)
        except ValueError as exc:
            if str(exc) != "ARCHIVE_RESTORE_REQUIRED":
                raise
            progress["unverified_files"] = progress.get("unverified_files", 0) + 1
            continue
        try:
            with path.open("rb") as source:

                def pieces():
                    while data := source.read(1024 * 1024):
                        consume(len(data))
                        yield data

                # 已有输入在排他锁内严格净节省替换；临时增加不超过64MiB且保留磁盘余量。
                # 不执行全目录usage；新业务写入依旧必须按原配额扫描和校验。
                write_payload(
                    path,
                    pieces(),
                    store,
                    replace=True,
                    expected=expected,
                    allowance=WORKING_BYTES,
                    deadline=deadline,
                    base_usage=0,
                )
        except ValueError as exc:
            if str(exc) != "ARCHIVE_NO_SAVING":
                raise
        else:
            progress["converted"] += 1
        if progress["converted"] >= max_files:
            return dict(status="batch", **progress)
    # 一圈目录不是GC证明。重新持锁读取全量引用并完成原有摘要校验才允许回收块。
    gc = {}
    try:
        result = _compact_locked(store, 0, max(0, deadline - time.monotonic()), gc)
    finally:
        for key in ("collected_blocks", "removed_temporary_files"):
            progress[key] += gc.get(key, 0)
    return dict(result, **progress)


def compact_archives(engine, root, *, max_files=4, max_seconds=20, scheduled=False):
    """定时同步前有限批次维护；项目/发布锁忙则跳过，不延后正在运行的同步。"""
    root = safe(root)
    store = store_for(root / "maintenance" / "input.ndjson")
    if not store.payload_dedup_enabled:
        return dict(status="disabled", converted=0)
    if root.name != "ingestion" or store.root != root or not root.is_dir():
        raise ValueError("ARCHIVE_PATH")
    policy_path = next(
        (p / "config/archive.toml" for p in root.parents if (p / "config/archive.toml").is_file()),
        None,
    )
    policy = tomllib.loads(policy_path.read_text()) if policy_path else {}
    interval = policy.get("maintenance_interval_seconds", 3600)
    rate = policy.get("maintenance_bytes_per_second", 4194304)
    entry_rate = policy.get("maintenance_entries_per_second", 500)
    if any(type(x) is not int or x <= 0 for x in (interval, rate, entry_rate)) or interval < 300:
        raise ValueError("ARCHIVE_MAINTENANCE_POLICY")
    stamp = safe(root / ".maintenance.json")
    state = {}

    def cooling_down():
        nonlocal state
        if not scheduled or not stamp.exists():
            return False
        if stamp.stat().st_size > 65536:
            raise ValueError("ARCHIVE_MAINTENANCE_STATE")
        import math

        try:
            state = json.loads(stamp.read_text())
            last = state["attempted_at"]
        except (ValueError, KeyError, TypeError):
            raise ValueError("ARCHIVE_MAINTENANCE_STATE") from None
        if type(last) not in (int, float) or not math.isfinite(last) or last < 0:
            raise ValueError("ARCHIVE_MAINTENANCE_STATE")
        return 0 <= time.time() - last < interval

    if cooling_down():
        return dict(status="not_due", converted=0)
    if scheduled:
        max_files = policy.get("maintenance_max_files", 2)
        max_seconds = policy.get("maintenance_max_seconds", 20)
        if (
            type(max_files) is not int
            or not 1 <= max_files <= 4
            or type(max_seconds) not in (int, float)
            or not 0 < max_seconds <= 20
        ):
            raise ValueError("ARCHIVE_MAINTENANCE_POLICY")
    from app.modules.sales.datasets import SPECS

    keys = [
        "project-source-sync",
        "m1-product-board",
        "m5-source-report",
        "nmc-weather-publication",
        *("m3-" + code for code in sorted({"shop_daily", "sales_targets", *SPECS})),
    ]
    acquired = []
    with engine.connect() as connection:
        _identity(connection, root)
        try:
            for key in keys:
                if not connection.scalar(
                    sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), dict(key=key)
                ):
                    return dict(status="busy", converted=0)
                acquired.append(key)
            connection.commit()
            fd = os.open(root / ".archive.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                progress = dict(converted=0, collected_blocks=0, removed_temporary_files=0)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return dict(status="busy", converted=0)
                if cooling_down():
                    return dict(status="not_due", converted=0)
                if scheduled:
                    state["attempted_at"] = time.time()
                    atomic_bytes(
                        stamp,
                        json.dumps(state).encode(),
                        replace=stamp.exists(),
                    )
                try:
                    with maintenance_budget(
                        max_seconds, rate if scheduled else 0, entry_rate if scheduled else 0
                    ):
                        if scheduled:
                            return _compact_batch(store, max_files, max_seconds, state, progress)
                        return _compact_locked(store, max_files, max_seconds, progress)
                except ValueError as exc:
                    if str(exc) != "ARCHIVE_MAINTENANCE_BUDGET":
                        raise
                    return dict(status="budget", **progress)
                finally:
                    if scheduled:
                        # 已离开预算上下文，失败/中断也保存小型游标，不复制经营输入。
                        atomic_bytes(stamp, json.dumps(state).encode(), replace=True)
            finally:
                os.close(fd)
        finally:
            connection.rollback()
            for key in reversed(acquired):
                connection.execute(
                    sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), dict(key=key)
                )
            connection.commit()
