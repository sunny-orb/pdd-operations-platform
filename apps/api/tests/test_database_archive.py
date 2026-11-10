"""加密版本包损坏/恢复验证，所有数据均为合成隔离PG数据。"""

import os
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from tests.test_published_versions import publish_pointer

pytest_plugins = ["tests.sales_pg_support"]


def old_candidate(engine, code="product_daily", timestamp=None):
    """合成已审核历史切换，绝不修改触发器或线上时钟。"""
    for suffix in "abc":
        publish_pointer(engine, code, suffix, published=False)
    from migrations_v2.baseline_20261028.schema import build_metadata
    from tests.test_product_board_storage import candidate

    tables = build_metadata().tables
    from app.modules.database_retention import TARGETS

    if code == "product_daily":
        row = candidate("123.45")[1]["product_daily"][0]
    elif code == "product_promotion":
        from datetime import date

        from app.modules.product_board.source import normalize

        row = normalize(
            code,
            [
                dict(
                    id="promo",
                    shop_id="663599807",
                    spu_id="123",
                    dt="2026-10-26",
                    start_dt="2026-10-26",
                    time_granularity="day",
                    promotion_name="方式A",
                    total_cost="123.456789",
                    transaction_count="1.5",
                    transaction_amount=None,
                )
            ],
            date(2026, 10, 26),
        )[0]
    else:
        from app.modules.sales.source_normalization import normalize_source
        from tests.test_sales_extended_sources import sample, window

        row = normalize_source(code, [sample(code)], window()).rows[0]
    if timestamp:
        row["source_updated_at" if code == "product_regions" else "source_created_at"] = timestamp
    shop_id = row.get("shop_id") or "663599807"
    with engine.begin() as c:
        c.execute(tables["public.shops"].insert().values(id=shop_id, status="active"))
        if row.get("listing_id"):
            c.execute(
                tables["public.listings"]
                .insert()
                .values(
                    id=row["listing_id"],
                    shop_id=shop_id,
                    platform_code="pdd",
                    platform_product_id="123",
                    status="active",
                )
            )
        for suffix in "abc":
            c.execute(
                tables["public." + TARGETS[code]]
                .insert()
                .values(**row, id="row-" + suffix, dataset_version_id=code + "-" + suffix)
            )
        for suffix in "abc":
            vid = code + "-" + suffix
            c.execute(
                sa.text(
                    "UPDATE dataset_versions SET state='validated', "
                    "content_fingerprint=:v,validation_evidence_ref='synthetic:verified', "
                    "received_at=clock_timestamp() WHERE id=:v"
                ),
                {"v": vid},
            )
            c.execute(
                sa.text("UPDATE dataset_versions SET state='published' WHERE id=:v"), {"v": vid}
            )
        c.execute(
            sa.text("INSERT INTO current_datasets(dataset_code,version_id) VALUES(:code,:head)"),
            {"code": code, "head": code + "-c"},
        )
        c.execute(
            sa.text(
                "INSERT INTO dataset_publications "
                "(id,dataset_code,revision,previous_version_id,version_id,occurred_at) "
                "VALUES('synthetic-exit',:code,100,:a,:b,:at)"
            ),
            {
                "code": code,
                "a": code + "-a",
                "b": code + "-b",
                "at": datetime.now(UTC) - timedelta(days=2),
            },
        )


def test_encrypted_stream_rejects_wrong_key_truncation_and_tampering(tmp_path):
    from app.modules.database_archive import decrypt, encrypt

    p = tmp_path / "data.enc"
    key = os.urandom(32)
    raw = [b"x" * 1000000, b"amount=123.45\nNULL=\\N\n"]
    encrypt(iter(raw), p, key)
    assert b"".join(decrypt(p, key)) == b"".join(raw)
    with pytest.raises(ValueError, match="ARCHIVE"):
        list(decrypt(p, os.urandom(32)))
    data = p.read_bytes()
    for broken in (data[:-1], data[:20], data[:30] + b"BAD" + data[33:]):
        p.write_bytes(broken)
        with pytest.raises(ValueError, match="ARCHIVE"):
            list(decrypt(p, key))


def test_archive_is_schema_bound_and_can_be_restored_in_fresh_pg(sales_pg, tmp_path):
    from app.modules.database_archive import drill, export, verify
    from app.modules.database_retention import plan

    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "bundle", key)
    verified = verify(bundle, key)
    assert verified["candidate"]["version_id"] == "product_daily-a"
    receipt = drill(bundle, key)
    assert receipt["restored"] is True
    assert receipt["production_approved"] is False


def test_offline_restore_keeps_current_and_rejects_stale_plan(sales_pg, tmp_path):
    from app.modules.database_archive import drill, export
    from app.modules.database_residency import ensure_online
    from app.modules.database_retention import apply, plan, restore

    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "roundtrip", key)
    drill(bundle, key)
    assert apply(sales_pg, selected, bundle, key)["state"] == "offline"
    with sales_pg.connect() as c:
        with pytest.raises(ValueError, match="DATASET_RESTORE_REQUIRED"):
            ensure_online(c, ["product_daily-a"])
        assert c.scalar(sa.text("SELECT version_id FROM current_datasets")) == "product_daily-c"
    with pytest.raises(sa.exc.DBAPIError, match="DATASET_RESTORE_REQUIRED"):
        with sales_pg.begin() as c:
            c.execute(sa.text("UPDATE current_datasets SET version_id='product_daily-a'"))
    assert restore(sales_pg, bundle, key)["state"] == "online"
    assert restore(sales_pg, bundle, key)["state"] == "online"
    with sales_pg.connect() as c:
        ensure_online(c, ["product_daily-a"])
        assert c.scalar(sa.text("SELECT version_id FROM current_datasets")) == "product_daily-c"


def test_real_archive_directory_keeps_manifest_producer_and_doc_locator(tmp_path):
    import hashlib
    import json

    from app.modules.archive_store import write_archive
    from app.modules.database_archive import evidence_files

    source = tmp_path / "source"
    source.mkdir()
    write_archive(source / "product_daily.json", '{"amount":"123.45"}')
    write_archive(source / "storage.py", "# original producer\n")
    members = {
        p.name: hashlib.sha256(
            json.dumps(
                p.read_text(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        for p in source.iterdir()
    }
    write_archive(source / "manifest.json", json.dumps({"files": members}))
    files, locators = evidence_files(
        {
            "retained_input_ref": str(source),
            "interpretation_ref": "docs/data-contract.md#351-m1",
            "dataset_code": "product_daily",
            "validation_evidence_ref": str(source / "manifest.json"),
        }
    )
    assert source / "storage.py" in files
    assert source / "product_daily.json" in files
    assert source / "manifest.json" in files
    assert locators == ["docs/data-contract.md#351-m1"]


@pytest.mark.parametrize("damage", ["missing_candidate", "traversal", "old_schema"])
def test_bundle_structure_is_checked_before_restore(sales_pg, tmp_path, damage):
    import json

    from app.modules.database_archive import decrypt, drill, encrypt, export, verify
    from app.modules.database_retention import plan

    old_candidate(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, plan(sales_pg), tmp_path / damage, key)
    manifest_path = bundle / "manifest.enc"
    data = json.loads(b"".join(decrypt(manifest_path, key)))
    if damage == "old_schema":
        data["schema_head"] = "20261104_12"
    elif damage == "traversal":
        data["files"]["../outside.enc"] = data["files"].pop("pre-data.enc")
    else:
        name = data["candidate"]["table_name"] + ".enc"
        del data["files"][name]
        (bundle / name).unlink()
    manifest_path.unlink()
    encrypt([json.dumps(data).encode()], manifest_path, key)
    with pytest.raises(ValueError, match="ARCHIVE"):
        verify(bundle, key)
    with pytest.raises(ValueError, match="ARCHIVE"):
        drill(bundle, key)


def test_production_writes_rejected_without_opening_connection():
    from app.modules.database_retention import require_local_test

    for url in [
        "postgresql+psycopg://songbing@pg.kktree.cn:5432/pdd_dashboard",
        "postgresql+psycopg://pdd_admin@127.0.0.1:55432/pdd_operations",
        "postgresql+psycopg://pdd_admin@127.0.0.1:5432/pdd_operations_v2_test_123456789abc",
    ]:
        engine = sa.create_engine(url)
        with pytest.raises(ValueError, match="RETENTION_PRODUCTION_DISABLED"):
            require_local_test(engine)
        engine.dispose()


def test_multiple_candidates_require_explicit_single_version_selection(sales_pg, tmp_path):
    from app.modules.database_archive import drill, export
    from app.modules.database_retention import apply, plan

    old_candidate(sales_pg)
    publish_pointer(sales_pg, "product_daily", "d", published=False)
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "UPDATE dataset_versions SET state='validated',content_fingerprint='synthetic:d', "
                "validation_evidence_ref='synthetic:ok',received_at=clock_timestamp() "
                "WHERE id='product_daily-d'"
            )
        )
        c.execute(
            sa.text("UPDATE dataset_versions SET state='published' WHERE id='product_daily-d'")
        )
        c.execute(
            sa.text(
                "INSERT INTO dataset_publications "
                "(id,dataset_code,revision,previous_version_id,version_id,occurred_at) "
                "VALUES('extra','product_daily',99,'product_daily-d','product_daily-b',:at)"
            ),
            {"at": datetime.now(UTC) - timedelta(days=3)},
        )
    selected = plan(sales_pg)
    assert len(selected["candidates"]) == 2
    key = os.urandom(32)
    with pytest.raises(ValueError, match="ARCHIVE_SELECT_ONE_VERSION"):
        export(sales_pg, selected, tmp_path / "ambiguous", key)
    bundle = export(sales_pg, selected, tmp_path / "chosen", key, version_id="product_daily-a")
    drill(bundle, key)
    apply(sales_pg, selected, bundle, key)
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT state FROM dataset_storage WHERE version_id='product_daily-d'")
            )
            == "online"
        )


@pytest.mark.parametrize("code", ["product_daily", "product_promotion", "product_regions"])
def test_three_fact_types_restore_precise_values_with_different_session_timezone(
    sales_pg, tmp_path, monkeypatch, code
):
    from app.modules import database_archive as archive
    from app.modules.database_retention import apply, plan, restore
    from migrations_v2.local import local_url

    old_candidate(sales_pg, code, datetime.fromisoformat("2026-10-26T14:30:12.123456+05:45"))
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = archive.export(sales_pg, selected, tmp_path / code, key)
    monkeypatch.setattr(
        archive,
        "local_url",
        lambda name: local_url(name).update_query_dict({"options": "-c timezone=Pacific/Honolulu"}),
    )
    archive.drill(bundle, key)
    apply(sales_pg, selected, bundle, key)
    restore(sales_pg, bundle, key)
    assert plan(sales_pg)["candidates"] == selected["candidates"]
