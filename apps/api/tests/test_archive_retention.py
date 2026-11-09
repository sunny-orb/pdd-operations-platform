"""历史保留：真实发布顺序、依赖闭包、离线校验与安全恢复。"""

import hashlib
import io
import tarfile

import pytest


def test_previous_uses_publication_order_and_protects_transitive_inputs():
    from app.modules.archive_retention import protected_versions

    versions = [
        dict(id=x, dataset_code="data", created_at=str(n)) for n, x in enumerate(["a", "b", "c"])
    ]
    tasks = [dict(result_version_id=x) for x in ["a", "b", "a"]]
    edges = [("b", "input"), ("input", "base")]
    assert protected_versions({"data": "a"}, versions, tasks, edges) >= {"a", "b", "input", "base"}


def test_export_verify_and_restore_preserve_bytes_and_reject_corruption(tmp_path):
    from app.modules.archive_retention import export_bundle, restore_bundle, verify_bundle

    root = tmp_path / "source"
    root.mkdir()
    p = root / "sales" / "old" / "input.ndjson"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"original data\n")
    item = dict(
        path="sales/old/input.ndjson",
        size=p.stat().st_size,
        sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    )
    output = io.BytesIO()
    export_bundle(root, [item], output)
    content = output.getvalue()
    assert verify_bundle(io.BytesIO(content)) == [item]
    dest = tmp_path / "restored"
    dest.mkdir()
    restore_bundle(io.BytesIO(content), dest)
    assert (dest / item["path"]).read_bytes() == b"original data\n"
    assert restore_bundle(io.BytesIO(content), dest) == 0
    (dest / item["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="ARCHIVE_EXISTS"):
        restore_bundle(io.BytesIO(content), dest)
    with pytest.raises((tarfile.ReadError, EOFError, ValueError)):
        verify_bundle(io.BytesIO(content[: len(content) // 2]))


def test_export_refuses_traversal_and_changed_input(tmp_path):
    from app.modules.archive_retention import export_bundle

    p = tmp_path / "a"
    p.write_bytes(b"new")
    with pytest.raises(ValueError, match="ARCHIVE_CHANGED"):
        export_bundle(tmp_path, [dict(path="a", size=3, sha256="0" * 64)], io.BytesIO())
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        export_bundle(tmp_path, [dict(path="../outside", size=3, sha256="0" * 64)], io.BytesIO())


def test_restore_complete_bundle_with_checksum_sidecars_into_empty_root(tmp_path):
    from app.modules.archive_retention import export_bundle, file_record, restore_bundle
    from app.modules.archive_store import write_archive

    root = tmp_path / "source" / "ingestion"
    root.mkdir(parents=True)
    write_archive(root / "sales" / "batch" / "input.ndjson", b"valid data")
    items = [file_record(root, p) for p in (root / "sales" / "batch").iterdir()]
    output = io.BytesIO()
    export_bundle(root, items, output)
    dest = tmp_path / "restored" / "ingestion"
    dest.mkdir(parents=True)
    restore_bundle(io.BytesIO(output.getvalue()), dest)
    assert (dest / "sales/batch/input.ndjson.sha256").read_bytes() == (
        root / "sales/batch/input.ndjson.sha256"
    ).read_bytes()
    assert not list(dest.rglob("*.sha256.sha256"))
