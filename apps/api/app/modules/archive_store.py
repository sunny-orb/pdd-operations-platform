"""归档文件的唯一写入与容量边界；不删除已发布输入，不修改数据库。"""

import fcntl
import hashlib
import io
import json
import logging
import os
import stat
import tempfile
import time
import tomllib
from functools import lru_cache
from pathlib import Path

from app.modules.archive_payload import PAYLOADS, open_payload, read_manifest, write_payload

DEFAULT_MAX = 12 * 1024**3
DEFAULT_FREE = 2 * 1024**3


def _safe(path):
    if ".." in Path(path).parts:
        raise ValueError("ARCHIVE_PATH")
    path = Path(path).absolute()
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ValueError("ARCHIVE_PATH")
    return path


@lru_cache(maxsize=256)
def _digest(path, inode, size, mtime_ns, ctime_ns, encoded_text=False, deadline=None):
    digest = hashlib.sha256()
    if encoded_text:
        digest.update(b'"')
        with open_payload(path) as raw, io.TextIOWrapper(raw, encoding="utf-8") as source:
            while chunk := source.read(1024 * 1024):
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")
                digest.update(json.dumps(chunk, ensure_ascii=False)[1:-1].encode())
        digest.update(b'"')
    else:
        with open_payload(path) as source:
            while chunk := source.read(1024 * 1024):
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")
                digest.update(chunk)
    after = Path(path).stat()
    if (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        inode,
        size,
        mtime_ns,
        ctime_ns,
    ):
        raise ValueError("ARCHIVE_CHANGED")
    return digest.hexdigest()


def _verify(path, expected, encoded_text=False, deadline=None):
    path = _safe(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("ARCHIVE_PATH")
    reader = (
        _digest.__wrapped__ if deadline is not None or read_manifest(path) is not None else _digest
    )
    actual = reader(
        str(path),
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        encoded_text,
        deadline,
    )
    if actual != expected:
        raise ValueError("ARCHIVE_CHECKSUM")


def require_archive(reference):
    """复用前核验原始输入，缺档/损坏不得切current；目录保护全部清单成员。"""
    path = _safe(reference)
    if not path.exists():
        logging.error("ARCHIVE_RESTORE_REQUIRED: %s", path.name)
        raise ValueError("ARCHIVE_RESTORE_REQUIRED")
    if path.is_dir():
        manifest = _safe(path / "manifest.json")
        if not manifest.is_file():
            raise ValueError("ARCHIVE_RESTORE_REQUIRED")
        manifest_checksum = _safe(path / "manifest.json.sha256")
        if manifest_checksum.exists():
            _verify(manifest, manifest_checksum.read_text().strip())
        members = json.loads(manifest.read_text()).get("files")
        if not isinstance(members, dict) or not members:
            raise ValueError("ARCHIVE_MANIFEST")
        for name, digest in members.items():
            if Path(name).name != name or name in (".", ".."):
                raise ValueError("ARCHIVE_PATH")
            child = _safe(path / name)
            if not child.is_file():
                raise ValueError("ARCHIVE_RESTORE_REQUIRED")
            _verify(child, digest, encoded_text=True)
    else:
        sidecar = _safe(path.with_name(path.name + ".sha256"))
        if sidecar.exists():
            _verify(path, sidecar.read_text().strip())
        elif path.name == "input.ndjson":
            policy = _safe(path.parent / "interpretation.json")
            expected = (
                json.loads(policy.read_text()).get("input_sha256") if policy.is_file() else None
            )
            if not expected:
                raise ValueError("ARCHIVE_RESTORE_REQUIRED")
            _verify(path, expected)
        elif not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("ARCHIVE_PATH")
    return str(path)


class ArchiveStore:
    def __init__(
        self,
        root,
        *,
        max_bytes=DEFAULT_MAX,
        min_free_bytes=DEFAULT_FREE,
        payload_dedup_enabled=False,
    ):
        self.root = _safe(root)
        self.max_bytes, self.min_free_bytes = max_bytes, min_free_bytes
        self.payload_dedup_enabled = payload_dedup_enabled

    def usage(self):
        total = 0
        if not self.root.exists():
            return total
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in (*dirs, *files):
                p = Path(directory) / name
                info = p.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_nlink > 1 and p.is_file():
                    raise ValueError("ARCHIVE_PATH")
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
        return total

    def check(self, additional=0):
        used = self.usage()
        parent = self.root
        while not parent.exists():
            parent = parent.parent
        disk = os.statvfs(parent)
        if (
            used + additional > self.max_bytes
            or disk.f_bavail * disk.f_frsize - additional < self.min_free_bytes
        ):
            logging.error(
                "ARCHIVE_CAPACITY: used=%d requested=%d limit=%d", used, additional, self.max_bytes
            )
            raise ValueError("ARCHIVE_CAPACITY")
        if used + additional > self.max_bytes * 2 // 3:
            logging.warning("ARCHIVE_CAPACITY_WARNING: used=%d limit=%d", used, self.max_bytes)
        return used

    def write(self, path, data):
        return self.write_stream(path, io.BytesIO(data), len(data))

    def write_stream(self, path, source, size):
        path = _safe(path)
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("ARCHIVE_PATH")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = os.open(self.root / ".archive.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                raise ValueError("ARCHIVE_EXISTS")
            self.check(size)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _safe(path.parent)
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    remaining = size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("ARCHIVE_CHANGED")
                        stream.write(chunk)
                        remaining -= len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, path, follow_symlinks=False)
            finally:
                os.unlink(temporary)
        finally:
            os.close(lock)
        return str(path)


def store_for(path):
    path = Path(path).absolute()
    root = next((p for p in path.parents if p.name in ("ingestion", "scoring")), path.parent)
    config = next(
        (p / "config/archive.toml" for p in path.parents if (p / "config/archive.toml").is_file()),
        None,
    )
    policy = tomllib.loads(config.read_text()) if config else {}
    return ArchiveStore(
        root,
        max_bytes=policy.get("max_archive_bytes", DEFAULT_MAX),
        min_free_bytes=policy.get("min_free_bytes", DEFAULT_FREE),
        payload_dedup_enabled=policy.get("payload_dedup_enabled", False),
    )


def write_archive(path, content):
    data = content.encode() if isinstance(content, str) else content
    if store_for(path).payload_dedup_enabled and Path(path).name in PAYLOADS:
        return write_archive_stream(path, [data])
    path = _safe(path)
    store = store_for(path)
    sidecar = path.with_name(path.name + ".sha256")
    expected = hashlib.sha256(data).hexdigest()
    if sidecar.exists() and _safe(sidecar).read_text().strip() != expected:
        raise ValueError("ARCHIVE_CHECKSUM")
    store.check(len(data) + (0 if sidecar.exists() else 64))
    result = store.write(path, data)
    if not sidecar.exists():
        store.write(sidecar, expected.encode())
    return result


def write_archive_stream(path, chunks):
    """流式保存来源字节，支持内容块；仅本次临时文件，不复制完整输入到内存。"""
    from app.modules.archive_payload import atomic_bytes

    path = _safe(path)
    store = store_for(path)
    store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_relative_to(store.root) or path == store.root:
        raise ValueError("ARCHIVE_PATH")
    lock = os.open(store.root / ".archive.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            raise ValueError("ARCHIVE_EXISTS")
        sidecar = _safe(path.with_name(path.name + ".sha256"))
        expected = sidecar.read_text().strip() if sidecar.exists() else None
        if store.payload_dedup_enabled and path.name in PAYLOADS:
            digest = write_payload(path, chunks, store, expected=expected)
        else:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
            h = hashlib.sha256()
            try:
                with os.fdopen(fd, "wb") as stream:
                    for chunk in chunks:
                        store.check(len(chunk) + 64)
                        stream.write(chunk)
                        stream.flush()
                        h.update(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                digest = h.hexdigest()
                if expected is not None and digest != expected:
                    raise ValueError("ARCHIVE_CHECKSUM")
                os.link(temporary, path, follow_symlinks=False)
            finally:
                os.unlink(temporary)
        if expected is None:
            atomic_bytes(sidecar, digest.encode())
        return str(path)
    finally:
        os.close(lock)


def close_temporary(folder):
    """只清理由本次接收创建的UUID暂存目录；不扫其他任务目录。"""
    import shutil
    from uuid import UUID

    path = _safe(folder)
    if path.parent.name not in ("m3-feishu-staging", "m3-target-staging", "source-checks"):
        raise ValueError("ARCHIVE_PATH")
    if str(UUID(path.name)) != path.name:
        raise ValueError("ARCHIVE_PATH")
    if path.exists():
        ArchiveStore(path).usage()  # 拒绝目录中的链接，不跟随链接删除。
        shutil.rmtree(path)


def temporary_pages(method):
    """稳定双读完成或失败后清理该接收器的全部临时页。"""
    from functools import wraps

    @wraps(method)
    def receive(self, *args, **kwargs):
        try:
            if hasattr(self, "folder"):
                store_for(self.folder / "page.ndjson").check(64 * 1024**2)
            return method(self, *args, **kwargs)
        finally:
            if hasattr(self, "folder"):
                close_temporary(self.folder)

    return receive


def run_source_cli(command, *, cwd, file_limit=64 * 1024**2, **kwargs):
    """仅限制这个CLI子进程的输出文件；不改变父进程/宿主机限制。"""
    import subprocess
    import sys

    root = Path(cwd) / "var/ingestion"
    store_for(root / "source-checks" / "pending" / "page").check(file_limit * 2)
    wrapper = (
        "import os,resource,sys;limit=int(sys.argv[1]);"
        "resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit));"
        "os.execvp(sys.argv[2],sys.argv[2:])"
    )
    return subprocess.run(
        [sys.executable, "-c", wrapper, str(file_limit), *command], cwd=cwd, **kwargs
    )
