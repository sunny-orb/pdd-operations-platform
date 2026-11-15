"""压缩共享原始输入必须保持逐字节可恢复、有限内存和私有路径。"""

import hashlib

import pytest


def test_repeated_large_payload_reuses_blocks_and_roundtrips(tmp_path):
    from app.modules.archive_payload import open_payload
    from app.modules.archive_store import require_archive, write_archive_stream

    root = tmp_path / "ingestion"
    (tmp_path / "config").mkdir()
    (tmp_path / "config/archive.toml").write_text("payload_dedup_enabled=true\nmin_free_bytes=0\n")
    data = b'{"id":"123","amount":"10.50","note":"history"}\n' * 30000
    a = root / "source" / "a" / "input.ndjson"
    b = root / "source" / "b" / "input.ndjson"
    write_archive_stream(a, [data[:500000], data[500000:]])
    first = {p: p.stat().st_size for p in (root / ".payloads").rglob("*.gz")}
    assert first
    write_archive_stream(b, (data[i : i + 12345] for i in range(0, len(data), 12345)))
    assert {p: p.stat().st_size for p in (root / ".payloads").rglob("*.gz")} == first
    assert a.stat().st_size < 10000
    for path in (a, b):
        assert require_archive(path) == str(path)
        with open_payload(path) as f:
            assert f.read() == data
        assert path.with_name(path.name + ".sha256").read_text() == hashlib.sha256(data).hexdigest()


def test_corrupt_block_is_rejected_not_returned_as_empty(tmp_path):
    from app.modules.archive_store import require_archive, write_archive_stream

    root = tmp_path / "ingestion"
    (tmp_path / "config").mkdir()
    (tmp_path / "config/archive.toml").write_text("payload_dedup_enabled=true\nmin_free_bytes=0\n")
    path = root / "source" / "a" / "input.ndjson"
    write_archive_stream(path, [b"hello history\n" * 100000])
    next((root / ".payloads").rglob("*.gz")).write_bytes(b"bad")
    with pytest.raises(ValueError, match="ARCHIVE_"):
        require_archive(path)


def test_legacy_plain_payload_and_small_file_stay_compatible(tmp_path):
    from app.modules.archive_payload import open_payload

    path = tmp_path / "input.ndjson"
    path.write_bytes(b'{"value":null}\n')
    with open_payload(path) as f:
        assert list(f) == [b'{"value":null}\n']


def test_storage_limit_does_not_leave_partial_reference(tmp_path):
    from app.modules.archive_store import write_archive_stream

    root = tmp_path / "ingestion"
    (tmp_path / "config").mkdir()
    (tmp_path / "config/archive.toml").write_text(
        "payload_dedup_enabled=true\nmax_archive_bytes=128\nmin_free_bytes=0\n"
    )
    path = root / "source" / "a" / "input.ndjson"
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        write_archive_stream(path, [b"x" * 2000000])
    assert not path.exists()


def test_failure_after_pointer_replace_keeps_referenced_blocks(tmp_path, monkeypatch):
    import app.modules.archive_payload as payload
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "ingestion"
    path = root / "source" / "a" / "input.ndjson"
    path.parent.mkdir(parents=True)
    data = b"preserve historical bytes\n" * 50000
    path.write_bytes(data)
    atomic = payload.atomic_bytes

    def failed_directory_sync(target, content, replace=False):
        atomic(target, content, replace=replace)
        if target == path:
            raise OSError("directory sync failed")

    monkeypatch.setattr(payload, "atomic_bytes", failed_directory_sync)
    with pytest.raises(OSError, match="directory sync failed"):
        payload.write_payload(path, [data], ArchiveStore(root, min_free_bytes=0), replace=True)
    with payload.open_payload(path) as f:
        assert f.read() == data


def test_conversion_working_budget_preserves_original_even_with_free_quota(tmp_path):
    from app.modules.archive_payload import write_payload
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "ingestion"
    path = root / "source" / "a" / "input.ndjson"
    path.parent.mkdir(parents=True)
    data = b"fixed budget history\n" * 50000
    path.write_bytes(data)
    with pytest.raises(ValueError, match="ARCHIVE_MAINTENANCE_BUDGET"):
        write_payload(
            path, [data], ArchiveStore(root, min_free_bytes=0), replace=True, allowance=32
        )
    assert path.read_bytes() == data
    assert not list((root / ".payloads").rglob("*.gz"))


def test_manifest_repeated_blocks_have_bounded_filesystem_checks(tmp_path, monkeypatch):
    """重复引用不能让元数据读取按引用数重复遍历整条文件祖先链。"""
    import json
    from pathlib import Path

    from app.modules.archive_payload import MAGIC, read_manifest

    path = tmp_path / "ingestion/source/a/input.ndjson"
    path.parent.mkdir(parents=True)
    block = {"sha256": "a" * 64, "size": 1}
    path.write_bytes(
        MAGIC
        + json.dumps(dict(format=1, size=4096, sha256="b" * 64, chunks=[block] * 4096)).encode()
    )
    original = Path.lstat
    checked = []

    def observed_lstat(self, *args, **kwargs):
        checked.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", observed_lstat)
    assert read_manifest(path)["size"] == 4096
    assert len(checked) < 128


@pytest.mark.parametrize("link_kind", ["symbolic", "hard"])
def test_each_manifest_read_rechecks_block_links(tmp_path, link_kind):
    import os

    from app.modules.archive_payload import read_manifest, write_payload
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "ingestion"
    path = root / "source/a/input.ndjson"
    path.parent.mkdir(parents=True)
    write_payload(path, [b"historical input"], ArchiveStore(root, min_free_bytes=0))
    assert read_manifest(path)
    block = next((root / ".payloads").rglob("*.gz"))
    outside = tmp_path / "outside.gz"
    outside.write_bytes(block.read_bytes())
    block.unlink()
    if link_kind == "symbolic":
        block.symlink_to(outside)
    else:
        os.link(outside, block)
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        read_manifest(path)
