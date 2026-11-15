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
