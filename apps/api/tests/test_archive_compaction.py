"""历史逻辑字节不变、重复执行不增长，失败不丢输入；真实PG锁隔离。"""

import hashlib
import io

import pytest
import sqlalchemy as sa

pytest_plugins = ["sales_pg_support"]


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
