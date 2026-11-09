"""文件系统行为：容量、原子写入、越界及损坏不得破坏归档。"""

import pytest


def test_capacity_failure_leaves_existing_input_unchanged(tmp_path):
    from app.modules.archive_store import ArchiveStore

    store = ArchiveStore(tmp_path, max_bytes=10, min_free_bytes=0)
    store.write(tmp_path / "batch" / "a.json", b"12345678")
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        store.write(tmp_path / "batch" / "b.json", b"456")
    assert (tmp_path / "batch/a.json").read_bytes() == b"12345678"
    assert not (tmp_path / "batch/b.json").exists()


def test_existing_payload_is_immutable_and_symlink_is_refused(tmp_path):
    from app.modules.archive_store import ArchiveStore

    store = ArchiveStore(tmp_path, max_bytes=1000, min_free_bytes=0)
    store.write(tmp_path / "a.json", b"first")
    with pytest.raises(ValueError, match="ARCHIVE_EXISTS"):
        store.write(tmp_path / "a.json", b"second")
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        store.write(tmp_path / "link/escape.json", b"oops")
    assert (tmp_path / "a.json").read_bytes() == b"first"


def test_missing_member_in_directory_manifest_requires_restore(tmp_path):
    from app.modules.archive_store import require_archive

    (tmp_path / "manifest.json").write_text('{"files":{"input.json":"aabb"}}')
    with pytest.raises(ValueError, match="ARCHIVE_RESTORE_REQUIRED"):
        require_archive(tmp_path)


def test_owned_temporary_directory_is_cleaned_and_foreign_path_refused(tmp_path):
    import uuid

    from app.modules.archive_store import close_temporary

    folder = tmp_path / "m3-feishu-staging" / str(uuid.uuid4())
    folder.mkdir(parents=True)
    (folder / "page.ndjson").write_text("private raw")
    close_temporary(folder)
    assert not folder.exists()
    keep = tmp_path / "other"
    keep.mkdir()
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        close_temporary(keep)
    assert keep.exists()


def test_external_source_process_cannot_write_beyond_its_file_limit(tmp_path):
    import sys

    from app.modules.archive_store import run_source_cli

    result = run_source_cli(
        [sys.executable, "-c", "with open('large','wb') as f: f.write(b'x'*4096)"],
        cwd=tmp_path,
        capture_output=True,
        file_limit=1024,
    )
    assert result.returncode != 0
    assert (tmp_path / "large").stat().st_size <= 1024


def test_parent_path_cannot_escape_archive_root(tmp_path):
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        ArchiveStore(root, min_free_bytes=0).write(root / ".." / "outside", b"bad")
    assert not (tmp_path / "outside").exists()


def test_existing_but_corrupt_manifest_input_is_refused(tmp_path):
    from app.modules.archive_store import require_archive

    (tmp_path / "input.json").write_text("CORRUPTED")
    (tmp_path / "manifest.json").write_text('{"files":{"input.json":"aabb"}}')
    with pytest.raises(ValueError, match="ARCHIVE_CHECKSUM"):
        require_archive(tmp_path)


def test_manifest_cannot_hide_a_missing_member_by_rewriting_its_file_list(tmp_path):
    import hashlib

    from app.modules.archive_store import require_archive

    original = b'{"files":{"lost.json":"missing","kept.json":"ignored"}}'
    (tmp_path / "manifest.json").write_bytes(original)
    (tmp_path / "manifest.json.sha256").write_text(hashlib.sha256(original).hexdigest())
    import json

    digest = hashlib.sha256(json.dumps("x").encode()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"kept.json": digest}}))
    (tmp_path / "kept.json").write_text("x")
    # 被篡改清单必须先与侧车不符，不可依赖删减后的成员列表。
    with pytest.raises(ValueError, match="ARCHIVE_CHECKSUM"):
        require_archive(tmp_path)
