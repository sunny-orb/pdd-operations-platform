"""历史逻辑字节不变、重复执行不增长，失败不丢输入；真实PG锁隔离。"""

import hashlib
import io

import pytest
import sqlalchemy as sa

pytest_plugins = ["sales_pg_support"]


def test_scheduled_maintenance_cooldown_skips_scan_across_invocations(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_payload import open_payload
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root)
    assert compact_archives(sales_pg, root, scheduled=True)["converted"] == 1
    with open_payload(path) as source:
        assert source.read() == data

    def unwanted_scan(*args, **kwargs):
        raise AssertionError("cooldown must not rescan archives")

    monkeypatch.setattr(ArchiveStore, "usage", unwanted_scan)
    assert compact_archives(sales_pg, root, scheduled=True)["status"] == "not_due"


def test_maintenance_zero_budget_does_not_start_full_tree_scan(sales_pg, tmp_path, monkeypatch):
    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root)

    def unwanted_scan(*args, **kwargs):
        raise AssertionError("exhausted budget still scanned the archive tree")

    monkeypatch.setattr(ArchiveStore, "usage", unwanted_scan)
    assert compact_archives(sales_pg, root, max_seconds=0)["status"] == "budget"
    assert path.read_bytes() == data


def test_maintenance_removes_only_reserved_interrupted_temporary_files(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root, data=b"small retained history")
    pending = path.parent / ".pending-ab12_cd3"
    pending.write_bytes(b"interrupted write")
    unknown = path.parent / "notes.tmp"
    unknown.write_bytes(b"unrelated retain")
    compact_archives(sales_pg, root)
    assert not pending.exists()
    assert path.read_bytes() == data and unknown.read_bytes() == b"unrelated retain"


@pytest.mark.parametrize("scheduled", [False, True])
def test_interrupted_link_commit_is_repaired_without_allowing_external_hardlinks(
    sales_pg, tmp_path, scheduled
):
    import os

    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_store import ArchiveStore

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root, data=b"retained canonical")
    pending = path.parent / ".pending-ab12_cd3"
    os.link(path, pending)
    compact_archives(sales_pg, root, scheduled=scheduled)
    assert path.read_bytes() == data and path.stat().st_nlink == 1
    assert not pending.exists()
    assert ArchiveStore(root).usage() > 0
    external = tmp_path / "outside"
    os.link(path, external)
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        compact_archives(sales_pg, root)
    assert path.read_bytes() == external.read_bytes() == data


def test_resumed_large_directory_repairs_block_link_without_rescanning_prefix(
    tmp_path, monkeypatch
):
    import os

    from app.modules.archive_budget import maintenance_budget, resume_inventory

    root = tmp_path / "ingestion"
    directory = root / ".payloads/ab"
    directory.mkdir(parents=True)
    for i in range(10001):
        (directory / f"old-{i:05d}").touch()
    target = directory / "retained.gz"
    target.write_bytes(b"retained content")
    pending = directory / ".pending-abcdefgh"
    os.link(target, pending)
    # 文件系统顺序不保证词法顺序，游标按真实scandir序号保存。
    with os.scandir(directory) as entries:
        names = [e.name for e in entries]
    offset = min(names.index(target.name), names.index(pending.name))
    state = {"scan_stack": [{"path": ".payloads/ab", "offset": offset}]}
    progress = {}
    with maintenance_budget(1, entries_per_second=100):
        for _path, _ in resume_inventory(root, state, progress):
            if target.stat().st_nlink == 1:
                break
    assert target.read_bytes() == b"retained content" and not pending.exists()
    assert progress["removed_temporary_files"] == 1


def test_scheduled_batches_advance_when_entire_tree_exceeds_scan_budget(
    sales_pg, tmp_path, monkeypatch
):
    import time
    import types

    from app.modules import archive_compaction as module

    root = tmp_path / "ingestion"
    root.mkdir()
    policy(tmp_path)
    config = tmp_path / "config/archive.toml"
    config.write_text(
        config.read_text() + "maintenance_max_seconds=0.1\nmaintenance_entries_per_second=100\n"
    )
    for i in range(30):
        (root / f".pending-{i:08d}").write_bytes(b"incomplete")
    module.compact_archives(sales_pg, root, scheduled=True)
    remaining = len(list(root.glob(".pending-*")))
    assert 0 < remaining < 30
    now = time.time()
    monkeypatch.setattr(
        module, "time", types.SimpleNamespace(time=lambda: now + 3601, monotonic=time.monotonic)
    )
    module.compact_archives(sales_pg, root, scheduled=True)
    assert len(list(root.glob(".pending-*"))) < remaining


def test_budget_failure_is_cooled_down_and_later_resumes_without_a_new_copy(
    sales_pg, tmp_path, monkeypatch
):
    import time
    import types

    from app.modules import archive_compaction as module
    from app.modules.archive_payload import open_payload

    root = tmp_path / "ingestion"
    policy(tmp_path)
    config = tmp_path / "config/archive.toml"
    config.write_text(config.read_text() + "maintenance_bytes_per_second=1\n")
    path, data = raw_archive(root)
    assert module.compact_archives(sales_pg, root, scheduled=True)["status"] == "budget"
    assert path.read_bytes() == data
    assert not list(root.rglob(".pending-*"))
    assert module.compact_archives(sales_pg, root, scheduled=True)["status"] == "not_due"
    config.write_text(
        config.read_text().replace(
            "maintenance_bytes_per_second=1", "maintenance_bytes_per_second=4194304"
        )
    )
    now = time.time()
    monkeypatch.setattr(
        module, "time", types.SimpleNamespace(time=lambda: now + 3601, monotonic=time.monotonic)
    )
    assert module.compact_archives(sales_pg, root, scheduled=True)["converted"] == 1
    with open_payload(path) as source:
        assert source.read() == data
    assert len(list(root.rglob("input.ndjson"))) == 1


def raw_archive(root, name="a", data=None):
    data = data or b'{"id":"123","history":"same","amount":"10.50"}\n' * 30000
    path = root / "source" / name / "input.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.with_name(path.name + ".sha256").write_text(hashlib.sha256(data).hexdigest())
    return path, data


def policy(tmp_path, quota=12 * 1024**3):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/archive.toml").write_text(
        f"payload_dedup_enabled=true\nmax_archive_bytes={quota}\nmin_free_bytes=0\n"
    )


def test_compaction_preserves_history_and_export_is_self_contained(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_payload import open_payload
    from app.modules.archive_retention import export_bundle, file_record, restore_bundle

    root = tmp_path / "ingestion"
    policy(tmp_path)
    a, data = raw_archive(root)
    b, _ = raw_archive(root, "b")
    before = file_record(root, a)
    with sales_pg.connect() as c:
        tables = c.exec_driver_sql("SELECT count(*) FROM dataset_versions").scalar()
    result = compact_archives(sales_pg, root)
    assert result["converted"] == 2 and result["after_bytes"] < result["before_bytes"] // 10
    assert file_record(root, a) == before
    with open_payload(b) as f:
        assert f.read() == data
    stable = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert compact_archives(sales_pg, root)["converted"] == 0
    assert stable == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    output = io.BytesIO()
    export_bundle(root, [before], output)
    output.seek(0)
    restored = tmp_path / "restored" / "ingestion"
    assert restore_bundle(output, restored) == 1
    assert (restored / before["path"]).read_bytes() == data
    with sales_pg.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM dataset_versions").scalar() == tables


def test_quota_full_can_compact_but_new_writes_cannot_exceed_quota(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_store import write_archive_stream

    root = tmp_path / "ingestion"
    path, data = raw_archive(root)
    policy(tmp_path, len(data) + 64)
    with pytest.raises(ValueError, match="ARCHIVE_CAPACITY"):
        write_archive_stream(root / "source" / "b" / "input.ndjson", [b"x" * 10000])
    assert compact_archives(sales_pg, root)["converted"] == 1
    write_archive_stream(root / "source" / "b" / "input.ndjson", [data])
    assert path.exists()


def test_busy_sync_skips_without_changing_any_payload(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root)
    with sales_pg.connect() as c:
        c.execute(sa.text("SELECT pg_advisory_lock(hashtext('project-source-sync'))"))
        try:
            result = compact_archives(sales_pg, root)
            assert result["status"] == "busy" and path.read_bytes() == data
        finally:
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))


def test_bad_checksum_fails_without_replacing_original(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root)
    path.with_name(path.name + ".sha256").write_text("0" * 64)
    with pytest.raises(ValueError, match="ARCHIVE_CHECKSUM"):
        compact_archives(sales_pg, root)
    assert path.read_bytes() == data


def test_only_unreferenced_blocks_collected_and_links_rejected(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, _ = raw_archive(root)
    compact_archives(sales_pg, root)
    orphan = root / ".payloads" / "00" / ("0" * 64 + ".gz")
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b"orphan")
    assert compact_archives(sales_pg, root)["collected_blocks"] == 1
    assert not orphan.exists()
    outside = tmp_path / "external"
    outside.write_text("unchanged")
    (root / "unsafe").symlink_to(outside)
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        compact_archives(sales_pg, root)
    assert outside.read_text() == "unchanged"


def test_wrong_database_and_disabled_policy_do_not_touch_files(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives

    root = tmp_path / "ingestion"
    path, data = raw_archive(root)
    assert compact_archives(sales_pg, root)["status"] == "disabled"
    assert path.read_bytes() == data


@pytest.mark.parametrize("kind", ["sales", "product", "aftersales"])
def test_all_publishers_reuse_compressed_history_and_keep_pg_bindings(sales_pg, tmp_path, kind):
    from test_archive_lifecycle_pg import publisher

    from app.modules.archive_payload import open_payload
    from app.modules.archive_retention import export_bundle, file_record, restore_bundle
    from app.modules.archive_store import require_archive

    root = tmp_path / "ingestion"
    policy(tmp_path)
    publish, pattern = publisher(kind, sales_pg, root)
    publish()
    with sales_pg.connect() as c:
        before = c.execute(
            sa.text("SELECT dataset_code,version_id FROM current_datasets ORDER BY 1")
        ).all()
        versions = c.execute(
            sa.text(
                "SELECT id,content_fingerprint,retained_input_ref FROM dataset_versions ORDER BY id"
            )
        ).all()
    payloads = list(root.rglob(pattern))
    assert payloads
    contents = {str(p): file_record(root, p) for p in payloads}
    for p in payloads:
        require_archive(p)
        with open_payload(p) as f:
            assert f.read() and p.read_bytes().startswith(b"PDD-ARCHIVE-CHUNKS-V1")
    if kind == "sales":
        from app.modules.sales.archive_replay import restore_candidate

        restored = restore_candidate(payloads[0].parent)
        assert restored.fingerprint in {r.content_fingerprint for r in versions}
    publish()
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT dataset_code,version_id FROM current_datasets ORDER BY 1")
            ).all()
            == before
        )
        assert (
            c.execute(
                sa.text(
                    "SELECT id,content_fingerprint,retained_input_ref "
                    "FROM dataset_versions ORDER BY id"
                )
            ).all()
            == versions
        )
    assert contents == {str(p): file_record(root, p) for p in root.rglob(pattern)}
    output = io.BytesIO()
    export_bundle(root, list(contents.values()), output)
    output.seek(0)
    target = tmp_path / "restored" / "ingestion"
    assert restore_bundle(output, target) == len(contents)
    assert [file_record(target, target / x["path"]) for x in contents.values()] == list(
        contents.values()
    )


def test_production_identity_is_fixed_before_any_changes(sales_pg, tmp_path, monkeypatch):
    from app.modules.archive_compaction import compact_archives

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="ARCHIVE_IDENTITY"):
        compact_archives(sales_pg, root)
    assert path.read_bytes() == data


def test_target_new_version_with_shared_payloads(sales_pg, tmp_path):
    from app.modules.archive_payload import open_payload
    from app.modules.sales.targets import SalesTargetStore
    from tests.test_sales_targets import candidate

    policy(tmp_path)
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "ingestion" / "sales_targets")
    from test_archive_lifecycle_pg import publisher

    publish, _ = publisher("sales", sales_pg, tmp_path / "ingestion")
    publish()
    result = store.publish_candidate(candidate())
    assert result
    for path in (tmp_path / "ingestion").rglob("*.ndjson"):
        with open_payload(path) as f:
            assert f.read()


def test_damaged_pointer_cannot_cause_collection_of_its_unique_blocks(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_payload import open_payload

    root = tmp_path / "ingestion"
    policy(tmp_path)
    path, data = raw_archive(root)
    compact_archives(sales_pg, root)
    original = path.read_bytes()
    blocks = {str(p): p.read_bytes() for p in (root / ".payloads").rglob("*.gz")}
    path.write_bytes(b"X" + original[1:])
    with pytest.raises(ValueError, match="ARCHIVE_CHECKSUM"):
        compact_archives(sales_pg, root, max_files=0)
    assert blocks == {str(p): p.read_bytes() for p in (root / ".payloads").rglob("*.gz")}
    path.write_bytes(original)
    with open_payload(path) as f:
        assert f.read() == data


def test_legacy_unverified_input_is_preserved_without_blocking_verified_files(sales_pg, tmp_path):
    from app.modules.archive_compaction import compact_archives
    from app.modules.archive_payload import open_payload

    root = tmp_path / "ingestion"
    policy(tmp_path)
    old, data = raw_archive(root, "legacy")
    old.with_name(old.name + ".sha256").unlink()
    current, _ = raw_archive(root, "current")
    result = compact_archives(sales_pg, root)
    assert result["converted"] == 1 and result["unverified_files"] == 1
    assert old.read_bytes() == data
    with open_payload(current) as f:
        assert f.read() == data
    orphan = root / ".payloads" / "00" / ("0" * 64 + ".gz")
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b"keep until all verified")
    result = compact_archives(sales_pg, root)
    assert result["collected_blocks"] == 0 and orphan.is_file()


def test_gc_checks_all_digest_metadata_before_decoding_any_payload(tmp_path, monkeypatch):
    """缺可信摘要的小旧文件也应先禁止GC，不先解压前面的完整历史。"""
    import app.modules.archive_compaction as compaction
    from app.modules.archive_store import ArchiveStore, write_archive_stream

    root = tmp_path / "ingestion"
    policy(tmp_path)
    verified = root / "source/a/input.ndjson"
    write_archive_stream(verified, [b"verified history\n" * 10000])
    unknown = root / "source/z/input.ndjson"
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"legacy without independent digest")
    orphan = root / ".payloads/00" / ("0" * 64 + ".gz")
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b"retain until every input can be verified")

    def unexpected_decode(*args, **kwargs):
        raise AssertionError("GC decoded history before discovering missing digest metadata")

    monkeypatch.setattr(compaction, "_verify", unexpected_decode)
    result = compaction._compact_locked(ArchiveStore(root, min_free_bytes=0), 0, 20)
    assert result["collected_blocks"] == 0 and result["unverified_files"] == 1
    assert orphan.exists() and unknown.read_bytes() == b"legacy without independent digest"
