"""原始输入透明压缩块：逻辑路径/字节不变，相同块只保存一次。"""

import gzip
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import time
import zlib
from contextlib import contextmanager
from pathlib import Path

MAGIC = b"PDD-ARCHIVE-CHUNKS-V1\n"
PAYLOADS = frozenset(
    {
        "input.ndjson",
        "normalized.ndjson",
        "product_daily.json",
        "product_promotion.json",
        "profile_records.json",
    }
)
MAX_CHUNK = 1024 * 1024
MIN_CHUNK = 64 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
MAX_BLOCKS = 32768


def safe(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("ARCHIVE_PATH")
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ValueError("ARCHIVE_PATH")
    if path.exists():
        s = path.stat()
        if path.is_file() and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1):
            raise ValueError("ARCHIVE_PATH")
    return path


def payload_root(path):
    root = next(
        (p for p in Path(path).absolute().parents if p.name in ("ingestion", "scoring")), None
    )
    if root is None:
        raise ValueError("ARCHIVE_PATH")
    return safe(root)


def block_path(root, digest):
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("ARCHIVE_MANIFEST")
    return safe(root / ".payloads" / digest[:2] / (digest + ".gz"))


def read_block(root, item):
    if (
        not isinstance(item, dict)
        or set(item) != {"sha256", "size"}
        or type(item["size"]) is not int
        or not 0 < item["size"] <= MAX_CHUNK
    ):
        raise ValueError("ARCHIVE_MANIFEST")
    path = block_path(root, item["sha256"])
    try:
        if path.stat().st_size > MAX_CHUNK + 4096:
            raise ValueError("ARCHIVE_CHECKSUM")
        with gzip.open(path, "rb") as f:
            data = f.read(item["size"] + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("ARCHIVE_RESTORE_REQUIRED") from exc
    if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("ARCHIVE_CHECKSUM")
    return data


def _checked_path(path, checked):
    """只在一次读取或持归档锁的维护批次内复用路径检查，绝不跨调用持久缓存。"""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("ARCHIVE_PATH")
    item = path
    while item not in checked:
        try:
            info = item.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise ValueError("ARCHIVE_PATH")
        checked.add(item)
        item = item.parent
    return path


def read_manifest(path):
    """每次独立调用重新检查清单和所有块路径，重复引用只检查一次。"""
    return _read_manifest(path, set())


def _read_manifest(path, checked):
    path = _checked_path(path, checked)
    with path.open("rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            return None
        raw = f.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError("ARCHIVE_MANIFEST")
    try:
        m = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("ARCHIVE_MANIFEST") from exc
    if (
        not isinstance(m, dict)
        or set(m) != {"format", "size", "sha256", "chunks"}
        or m["format"] != 1
        or type(m["size"]) is not int
        or m["size"] < 0
        or not isinstance(m["chunks"], list)
        or len(m["chunks"]) > MAX_BLOCKS
    ):
        raise ValueError("ARCHIVE_MANIFEST")
    digests = set()
    if not isinstance(m["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", m["sha256"]):
        raise ValueError("ARCHIVE_MANIFEST")
    digests.add(m["sha256"])
    for item in m["chunks"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"sha256", "size"}
            or type(item["size"]) is not int
            or not 0 < item["size"] <= MAX_CHUNK
        ):
            raise ValueError("ARCHIVE_MANIFEST")
        if not isinstance(item["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", item["sha256"]):
            raise ValueError("ARCHIVE_MANIFEST")
        digests.add(item["sha256"])
    if sum(x["size"] for x in m["chunks"]) != m["size"]:
        raise ValueError("ARCHIVE_MANIFEST")
    root = next((p for p in path.parents if p.name in ("ingestion", "scoring")), None)
    if root is None:
        raise ValueError("ARCHIVE_PATH")
    for digest in digests:
        _checked_path(root / ".payloads" / digest[:2] / (digest + ".gz"), checked)
    return m


class _PayloadStream(io.RawIOBase):
    def __init__(self, root, manifest):
        self.root, self.manifest = root, manifest
        self.index = 0
        self.buffer = memoryview(b"")
        self.digest = hashlib.sha256()
        self.verified = False

    def readable(self):
        return True

    def readinto(self, target):
        if not self.buffer:
            if self.index == len(self.manifest["chunks"]):
                if not self.verified and self.digest.hexdigest() != self.manifest["sha256"]:
                    raise ValueError("ARCHIVE_CHECKSUM")
                self.verified = True
                return 0
            data = read_block(self.root, self.manifest["chunks"][self.index])
            self.index += 1
            self.digest.update(data)
            self.buffer = memoryview(data)
        size = min(len(target), len(self.buffer))
        target[:size] = self.buffer[:size]
        self.buffer = self.buffer[size:]
        return size


@contextmanager
def open_payload(path):
    """按原始字节流读取；不会解压成另一份落盘副本。读到EOF验证完整摘要。"""
    path = safe(path)
    manifest = read_manifest(path)
    if manifest is None:
        with path.open("rb") as f:
            yield f
    else:
        with io.BufferedReader(_PayloadStream(payload_root(path), manifest)) as f:
            yield f


def read_payload_text(path):
    with open_payload(path) as f:
        return f.read().decode("utf-8")


def logical_chunks(pieces):
    """64KiB至1MiB内容边界；与调用者每次传入多少字节无关。"""
    pending = bytearray()

    def cut():
        for match in re.finditer(rb"\n|\},\{", pending, 0):
            end = match.end()
            if MIN_CHUNK <= end <= MAX_CHUNK and zlib.crc32(pending[end - 64 : end]) & 63 == 0:
                return end
        return MAX_CHUNK

    for piece in pieces:
        if not isinstance(piece, (bytes, bytearray, memoryview)):
            raise TypeError("ARCHIVE_BYTES_REQUIRED")
        for start in range(0, len(piece), MIN_CHUNK):
            pending.extend(piece[start : start + MIN_CHUNK])
            while len(pending) >= MAX_CHUNK:
                end = cut()
                yield bytes(pending[:end])
                del pending[:end]
    while len(pending) > MAX_CHUNK:
        end = cut()
        yield bytes(pending[:end])
        del pending[:end]
    if pending:
        yield bytes(pending)


def atomic_bytes(path, data, replace=False):
    path = safe(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if replace:
            os.replace(tmp, path)
        else:
            os.link(tmp, path, follow_symlinks=False)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_payload(path, pieces, store, *, replace=False, expected=None, allowance=0, deadline=None):
    """调用方持.archive.lock；每块写前限额，失败只移除本调用新建且未发布的块。"""
    path = safe(path)
    root = payload_root(path)
    if root != store.root or path.name not in PAYLOADS:
        raise ValueError("ARCHIVE_PATH")
    if path.exists() and not replace:
        raise ValueError("ARCHIVE_EXISTS")
    before = path.stat() if path.exists() else None
    base_usage = store.usage()
    new_bytes = 0
    created = []
    items = []
    digest = hashlib.sha256()
    total = 0
    published = False
    raw = None

    def capacity(size):
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")
        if replace and allowance and new_bytes + size > allowance:
            raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")
        disk = os.statvfs(root)
        if (
            base_usage + new_bytes + size
            > (max(store.max_bytes, base_usage) + allowance if replace else store.max_bytes)
            or disk.f_bavail * disk.f_frsize - size < store.min_free_bytes
        ):
            raise ValueError("ARCHIVE_CAPACITY")

    try:
        for data in logical_chunks(pieces):
            capacity(0)
            item = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            digest.update(data)
            total += len(data)
            block = block_path(root, item["sha256"])
            if block.exists():
                read_block(root, item)
            else:
                compressed = gzip.compress(data, compresslevel=6, mtime=0)
                capacity(len(compressed))
                atomic_bytes(block, compressed)
                created.append(block)
                new_bytes += len(compressed)
            items.append(item)
            if len(items) > MAX_BLOCKS:
                raise ValueError("ARCHIVE_MANIFEST")
        hexdigest = digest.hexdigest()
        if expected is not None and expected != hexdigest:
            raise ValueError("ARCHIVE_CHECKSUM")
        raw = (
            MAGIC
            + json.dumps(
                {"format": 1, "size": total, "sha256": hexdigest, "chunks": items},
                separators=(",", ":"),
            ).encode()
        )
        if len(raw) > MAX_MANIFEST:
            raise ValueError("ARCHIVE_MANIFEST")
        capacity(len(raw) + 64)
        if before:
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("ARCHIVE_CHANGED")
            if new_bytes + len(raw) >= before.st_size:
                raise ValueError("ARCHIVE_NO_SAVING")
        atomic_bytes(path, raw, replace=replace)
        published = True
        return hexdigest
    finally:
        # replace/link可能已成功而目录fsync报错；已被指针引用的块绝不能回收。
        committed = published or (
            raw is not None
            and path.is_file()
            and path.stat().st_size == len(raw)
            and path.read_bytes() == raw
        )
        if not committed:
            for item in reversed(created):
                safe(item).unlink()
