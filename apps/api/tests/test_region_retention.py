"""商品地区自动回收：随机真实PG，完整行证明、保护和恢复均经过数据库。"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from migrations_v2.baseline_20261028.schema import build_metadata
from tests.test_fact_content import seal
from tests.test_published_versions import publish_pointer

pytest_plugins = ["tests.sales_pg_support"]


def rows():
    return [
        dict(
            source_row_key=k,
            shop_match_status="not_provided",
            date_status="verified_period",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 1),
            actual_shipped_amount=Decimal("1.230000000000000001"),
            source_style_code="0007",
        )
        for k in ("a", "b", "c")
    ]


def put(engine, suffix, data=None, *, shared=False, current=True):
    from app.modules.fact_content import write_version

    vid = "product_regions-" + suffix
    publish_pointer(engine, "product_regions", suffix, published=False)
    with engine.begin() as c:
        data = rows() if data is None else data
        if shared:
            write_version(c, "product_region_records", vid, data)
        elif data:
            table = build_metadata().tables["public.product_region_records"]
            c.execute(
                table.insert(),
                [dict(r, id=vid + r["source_row_key"], dataset_version_id=vid) for r in data],
            )
        seal(c, vid)
        if current:
            c.execute(
                sa.text(
                    "INSERT INTO current_datasets(dataset_code,version_id) "
                    "VALUES ('product_regions',:v) ON CONFLICT(dataset_code) "
                    "DO UPDATE SET version_id=excluded.version_id"
                ),
                {"v": vid},
            )
    return vid


def age(engine):
    # 合成发布事件移到25小时前；生产入口绝不能更改不可变事件。
    with engine.begin() as c:
        c.exec_driver_sql(
            "ALTER TABLE dataset_publications DISABLE TRIGGER pdd_retention_immutable"
        )
        c.exec_driver_sql("UPDATE dataset_publications SET occurred_at=now()-interval '25 hours'")
        c.exec_driver_sql("ALTER TABLE dataset_publications ENABLE TRIGGER pdd_retention_immutable")


def sequence(engine, *, shared=False, changed=False):
    old = put(engine, "a", shared=shared)
    put(engine, "b", shared=True)
    data = rows()
    if changed:
        data[-1] = dict(data[-1], actual_shipped_amount=Decimal("9"))
    put(engine, "c", data, shared=True)
    age(engine)
    return old


def step(engine, n=2):
    from app.modules.region_retention import Policy
    from app.modules.region_retention import step as execute

    with engine.begin() as c:
        return execute(c, Policy(batch_rows=n))


def finish(engine):
    result = None
    for _ in range(20):
        result = step(engine)
        if result["status"] in ("idle", "retained", "reclaimed"):
            return result
    pytest.fail(str(result))


def test_region_retirement_database_entry_is_available(sales_pg):
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT to_regprocedure('public.pdd_region_retire_step(integer)')"))


def test_legacy_proof_precedes_bounded_delete_and_rebuild_preserves_identity(sales_pg):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    assert step(sales_pg)["status"] == "verifying"
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 3
    assert finish(sales_pg)["status"] == "reclaimed"
    with sales_pg.begin() as c:
        rebuild_version(c, old, rows(), content_fingerprint=old, row_schema_revision="test-v1")
        assert (
            c.scalar(sa.text("SELECT state FROM dataset_storage WHERE version_id=:v"), {"v": old})
            == "online"
        )
        assert (
            c.scalar(
                sa.text(
                    "SELECT count(*) FROM read_product_region_records WHERE dataset_version_id=:v"
                ),
                {"v": old},
            )
            == 3
        )
        assert (
            c.scalar(
                sa.text("SELECT content_fingerprint FROM dataset_versions WHERE id=:v"), {"v": old}
            )
            == old
        )
        assert c.scalar(sa.text("SELECT count(*) FROM retention_archives")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 3


def test_changed_value_keeps_entire_legacy_version(sales_pg):
    old = sequence(sales_pg, changed=True)
    assert finish(sales_pg)["status"] == "retained"
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT count(*) FROM product_region_records WHERE dataset_version_id=:v"),
                {"v": old},
            )
            == 3
        )


def test_unknown_exit_current_previous_and_grace_never_reclaimed(sales_pg):
    from app.modules.region_retention import inspect

    put(sales_pg, "unknown", current=False)
    for name in "abc":
        put(sales_pg, name, shared=True)
    with sales_pg.connect() as c:
        assert inspect(c)["candidates"] == []
    age(sales_pg)
    with sales_pg.connect() as c:
        assert inspect(c)["candidates"] == ["product_regions-a"]


@pytest.mark.parametrize("protection", ["pin", "input"])
def test_pin_and_external_recursive_inputs_protect_candidate(sales_pg, protection):
    sequence(sales_pg)
    if protection == "input":
        publish_pointer(sales_pg, "product_scores", "history", published=False)
    with sales_pg.begin() as c:
        if protection == "pin":
            c.exec_driver_sql(
                "UPDATE dataset_storage SET keep_online=true WHERE version_id='product_regions-a'"
            )
        else:
            c.exec_driver_sql(
                "INSERT INTO version_inputs(output_version_id,input_role,input_version_id) "
                "VALUES ('product_scores-history','product_history','product_regions-a')"
            )
    assert step(sales_pg)["status"] == "idle"


def test_shared_retirement_keeps_blocks_used_by_other_versions(sales_pg):
    sequence(sales_pg, shared=True)
    assert finish(sales_pg)["status"] == "reclaimed"
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 3
        assert c.scalar(sa.text("SELECT count(*) FROM read_product_region_records")) == 6


def test_direct_sql_cannot_delete_published_rows_or_advance_residency(sales_pg):
    sequence(sales_pg)
    for sql in (
        "DELETE FROM product_region_records",
        "UPDATE dataset_storage SET state='reclaimed' WHERE version_id='product_regions-a'",
    ):
        with pytest.raises(sa.exc.DBAPIError):
            with sales_pg.begin() as c:
                c.exec_driver_sql(sql)


def test_wrong_candidate_rebuild_rolls_back_and_keeps_current(sales_pg):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    finish(sales_pg)
    bad = rows()
    bad[0] = dict(bad[0], source_style_code=None)
    with pytest.raises((ValueError, sa.exc.DBAPIError), match="REGION_REBUILD_CONTENT"):
        with sales_pg.begin() as c:
            rebuild_version(c, old, bad, content_fingerprint=old, row_schema_revision="test-v1")
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='product_regions'"
                )
            )
            == "product_regions-c"
        )
        assert (
            c.scalar(sa.text("SELECT state FROM dataset_storage WHERE version_id=:v"), {"v": old})
            == "reclaimed"
        )


def test_writer_shared_lock_defers_gc(sales_pg):
    sequence(sales_pg)
    with sales_pg.begin() as c:
        c.exec_driver_sql(
            "SELECT pg_advisory_xact_lock_shared(hashtext('pdd-database-maintenance'))"
        )
        assert step(sales_pg)["status"] == "busy"


def test_restored_version_can_be_reclaimed_after_a_later_exit(sales_pg):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    finish(sales_pg)
    with sales_pg.begin() as c:
        rebuild_version(c, old, rows(), content_fingerprint=old, row_schema_revision="test-v1")
        c.exec_driver_sql("UPDATE current_datasets SET version_id='product_regions-a'")
    put(sales_pg, "d", shared=True)
    put(sales_pg, "e", shared=True)
    age(sales_pg)
    result = finish(sales_pg)
    assert result["status"] == "reclaimed" and result["version_id"] == old


def test_partial_legacy_retirement_can_be_rebuilt_atomically(sales_pg):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    assert step(sales_pg)["status"] == "verifying"
    assert step(sales_pg)["status"] == "reclaiming"
    assert step(sales_pg)["status"] == "reclaiming"
    with sales_pg.begin() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 1
        rebuild_version(c, old, rows(), content_fingerprint=old, row_schema_revision="test-v1")
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 0
        assert (
            c.scalar(
                sa.text(
                    "SELECT count(*) FROM read_product_region_records WHERE dataset_version_id=:v"
                ),
                {"v": old},
            )
            == 3
        )


@pytest.mark.parametrize("change", ["fingerprint", "revision", "duplicate", "missing"])
def test_rebuild_rejects_wrong_identity_and_incomplete_candidate(sales_pg, change):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    finish(sales_pg)
    data = rows()
    if change == "duplicate":
        data.append(data[0])
    if change == "missing":
        data.pop()
    with pytest.raises((ValueError, sa.exc.DBAPIError)):
        with sales_pg.begin() as c:
            rebuild_version(
                c,
                old,
                data,
                content_fingerprint="wrong" if change == "fingerprint" else old,
                row_schema_revision="wrong" if change == "revision" else "test-v1",
            )
    with sales_pg.connect() as c:
        assert (
            c.scalar(sa.text("SELECT state FROM dataset_storage WHERE version_id=:v"), {"v": old})
            == "reclaimed"
        )


def test_ingest_can_run_controlled_steps_but_cannot_fake_audit(sales_pg):
    from app.modules.region_retention import Policy
    from app.modules.region_retention import step as execute

    sequence(sales_pg)
    with sales_pg.begin() as c:
        c.exec_driver_sql("SET LOCAL ROLE pdd_ingest")
        assert execute(c, Policy(batch_rows=2))["status"] == "verifying"
    with pytest.raises(sa.exc.DBAPIError):
        with sales_pg.begin() as c:
            c.exec_driver_sql("SET LOCAL ROLE pdd_ingest")
            c.exec_driver_sql("SELECT set_config('pdd.region_version','product_regions-a',true)")
            c.exec_driver_sql(
                "UPDATE region_retirements SET status='reclaimed',transaction_id=txid_current()"
            )


def test_active_proof_protects_replacement_after_pointer_moves(sales_pg):
    sequence(sales_pg)
    assert step(sales_pg)["status"] == "verifying"
    put(sales_pg, "d", shared=True)
    put(sales_pg, "e", shared=True)
    age(sales_pg)
    with sales_pg.connect() as c:
        assert not c.scalar(sa.text("SELECT pdd_region_eligible('product_regions-c')"))
    assert finish(sales_pg)["status"] == "reclaimed"


def orphan(engine, count=1):
    """合成无成员完整块；不绕过任何封版或删除守卫。"""
    import hashlib

    key = "f" * 64
    source_key = "orphan"
    suffix = int(hashlib.md5(source_key.encode(), usedforsecurity=False).hexdigest()[:8], 16) % 64
    with engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO fact_content_blocks"
                "(id,table_name,dataset_code,row_schema_revision,bucket,row_count) "
                "VALUES(:id,'product_region_records','product_regions','test-v1',:bucket,:n)"
            ),
            {"id": key, "bucket": f"2026-10-01/2026-10-01:{suffix}", "n": count},
        )
        shared = sa.Table("shared_product_region_records", sa.MetaData(), autoload_with=c)
        # 大块测试使用同一hash桶的不同来源键。
        values = []
        i = 0
        while len(values) < count:
            row_key = source_key if i == 0 else f"orphan-{i}"
            i += 1
            if (
                int(hashlib.md5(row_key.encode(), usedforsecurity=False).hexdigest()[:8], 16) % 64
                != suffix
            ):
                continue
            values.append(dict(rows()[0], source_row_key=row_key, block_id=key))
        c.execute(shared.insert(), values)
        c.execute(
            sa.text("UPDATE fact_content_blocks SET state='sealed' WHERE id=:id"), {"id": key}
        )
    return key


def test_orphan_block_rows_and_header_are_removed_atomically(sales_pg):
    key = orphan(sales_pg)
    with pytest.raises(RuntimeError, match="crash"):
        with sales_pg.begin() as c:
            assert c.scalar(sa.text("SELECT pdd_region_gc_block(1000)")) == 1
            raise RuntimeError("crash")
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 1
        assert (
            c.scalar(sa.text("SELECT count(*) FROM fact_content_blocks WHERE id=:id"), {"id": key})
            == 1
        )
    assert step(sales_pg)["deleted_rows"] == 1
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM fact_content_blocks")) == 0


def test_gc_keeps_oversize_block_and_all_draft_references(sales_pg):
    from app.modules.fact_content import write_version

    orphan(sales_pg, 1001)
    publish_pointer(sales_pg, "product_regions", "draft", published=False)
    with sales_pg.begin() as c:
        write_version(c, "product_region_records", "product_regions-draft", rows())
    assert step(sales_pg)["deleted_rows"] == 0
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 1004


def test_fact_writer_holds_shared_lock_before_gc_can_examine_blocks(sales_pg):
    from app.modules.fact_content import write_version

    sequence(sales_pg)
    publish_pointer(sales_pg, "product_regions", "draft", published=False)
    with sales_pg.begin() as c:
        write_version(c, "product_region_records", "product_regions-draft", rows())
        assert step(sales_pg)["status"] == "busy"


def test_sales_a_b_c_a_reuses_original_version_without_new_archive(sales_pg, tmp_path, monkeypatch):
    from app.modules.sales.contracts import ReadWindow
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.source_policy import SHOPS
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_extended_sources import sample

    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    results, stores, candidates = [], [], []
    for day in (2, 3, 4):
        window = ReadWindow(date(2026, 10, 1), date(2026, 10, day), tuple(SHOPS))
        store = ShopDailyStore(
            sales_pg, archive_root=tmp_path, approved=window, spec=SPECS["product_regions"]
        )
        candidate = normalize_source(
            "product_regions", [sample("product_regions", dt="2026-10-01")], window
        )
        stores.append(store)
        candidates.append(candidate)
        results.append(store.publish_candidate(candidate))
    age(sales_pg)
    assert finish(sales_pg)["status"] == "reclaimed"
    before = set(tmp_path.rglob("*"))
    restored = stores[0].publish_candidate(candidates[0])
    assert restored.version_id == results[0].version_id and restored.reused
    assert set(tmp_path.rglob("*")) == before
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT count(*) FROM dataset_versions WHERE dataset_code='product_regions'"
                )
            )
            == 3
        )
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 1


def test_concurrent_rebuilds_share_one_complete_representation(sales_pg):
    from concurrent.futures import ThreadPoolExecutor

    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    finish(sales_pg)

    def rebuild():
        with sales_pg.begin() as c:
            c.exec_driver_sql("SET LOCAL statement_timeout='5s'")
            return rebuild_version(
                c, old, rows(), content_fingerprint=old, row_schema_revision="test-v1"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: rebuild(), range(2))) == [False, True]
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 3
        assert (
            c.scalar(
                sa.text("SELECT count(*) FROM fact_content_members WHERE version_id=:v"), {"v": old}
            )
            == 3
        )


def test_unknown_fact_consumer_blocks_retirement(sales_pg):
    sequence(sales_pg)
    with sales_pg.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE unknown_consumer(id text REFERENCES product_region_records(id))"
        )
    with pytest.raises(sa.exc.DBAPIError, match="REGION_UNKNOWN_REFERENCE"):
        step(sales_pg)


def test_partial_reclaim_and_rebuild_cannot_publish_or_commit_incomplete(sales_pg):
    old = sequence(sales_pg)
    step(sales_pg)
    assert step(sales_pg)["status"] == "reclaiming"
    with pytest.raises(sa.exc.DBAPIError, match="DATASET_RESTORE_REQUIRED"):
        with sales_pg.begin() as c:
            c.execute(sa.text("UPDATE current_datasets SET version_id=:v"), {"v": old})
    with pytest.raises(sa.exc.DBAPIError, match="REGION_REBUILD_INCOMPLETE"):
        with sales_pg.begin() as c:
            c.execute(sa.text("SELECT pdd_region_rebuild_begin(:v,:v,'test-v1')"), {"v": old})
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 3
        assert c.scalar(sa.text("SELECT version_id FROM current_datasets")) == "product_regions-c"


def test_rebuild_while_different_candidate_is_being_verified(sales_pg):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg)
    finish(sales_pg)
    put(sales_pg, "d", shared=True)
    age(sales_pg)
    assert step(sales_pg)["version_id"] == "product_regions-b"
    with sales_pg.begin() as c:
        assert rebuild_version(
            c, old, rows(), content_fingerprint=old, row_schema_revision="test-v1"
        )


def test_releasing_pin_after_started_proof_allows_reverification(sales_pg):
    sequence(sales_pg)
    step(sales_pg)
    with sales_pg.begin() as c:
        c.exec_driver_sql(
            "UPDATE dataset_storage SET keep_online=true WHERE version_id='product_regions-a'"
        )
    assert step(sales_pg)["status"] == "retained"
    with sales_pg.begin() as c:
        c.exec_driver_sql(
            "UPDATE dataset_storage SET keep_online=false WHERE version_id='product_regions-a'"
        )
    assert finish(sales_pg)["status"] == "reclaimed"


@pytest.mark.parametrize("function", ["retire_step", "gc_block"])
def test_database_rejects_null_batch_limit(sales_pg, function):
    sequence(sales_pg)
    with pytest.raises(sa.exc.DBAPIError, match="REGION_BATCH_LIMIT"):
        with sales_pg.begin() as c:
            c.execute(sa.text(f"SELECT pdd_region_{function}(NULL)"))


def test_owner_cannot_forge_proof_using_guc_and_ordinary_dml(sales_pg):
    sequence(sales_pg)
    assert step(sales_pg)["status"] == "verifying"
    with pytest.raises(sa.exc.DBAPIError, match="REGION_INTERNAL_ONLY"):
        with sales_pg.begin() as c:
            c.exec_driver_sql("SELECT set_config('pdd.region_version','product_regions-a',true)")
            c.exec_driver_sql(
                "UPDATE region_retirements SET status='reclaiming',original_rows=3,"
                "checked_rows=3,transaction_id=txid_current() WHERE version_id='product_regions-a'"
            )
            c.exec_driver_sql(
                "UPDATE dataset_storage SET state='reclaiming' WHERE version_id='product_regions-a'"
            )
            c.exec_driver_sql(
                "DELETE FROM product_region_records WHERE dataset_version_id='product_regions-a'"
            )
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 3


def test_owner_cannot_delete_extra_rows_after_a_valid_bounded_step(sales_pg):
    from app.modules.region_retention import Policy
    from app.modules.region_retention import step as execute

    sequence(sales_pg)
    step(sales_pg)
    step(sales_pg)
    with pytest.raises(sa.exc.DBAPIError):
        with sales_pg.begin() as c:
            assert execute(c, Policy(batch_rows=1))["deleted_rows"] == 1
            c.exec_driver_sql("SELECT set_config('pdd.region_version','product_regions-a',true)")
            c.exec_driver_sql(
                "DELETE FROM product_region_records WHERE dataset_version_id='product_regions-a'"
            )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "1", None])
def test_policy_rejects_nonfinite_or_non_numeric_wall_budget(value):
    from app.modules.region_retention import Policy

    with pytest.raises(ValueError, match="REGION_POLICY"):
        Policy(max_seconds=value)


def test_shared_rebuild_checks_business_values_even_if_block_identity_is_corrupt(sales_pg):
    from app.modules.region_retention import rebuild_version

    old = sequence(sales_pg, shared=True)
    finish(sales_pg)
    # 仅隔离测试模拟错误导入/块损坏；验证恢复不能只相信块ID和行数。
    with sales_pg.begin() as c:
        c.exec_driver_sql(
            "ALTER TABLE shared_product_region_records DISABLE TRIGGER pdd_content_guard"
        )
        c.exec_driver_sql(
            "UPDATE shared_product_region_records SET actual_shipped_amount=999 "
            "WHERE source_row_key='a'"
        )
        c.exec_driver_sql(
            "ALTER TABLE shared_product_region_records ENABLE TRIGGER pdd_content_guard"
        )
    with pytest.raises(sa.exc.DBAPIError, match="REGION_REBUILD_CONTENT"):
        with sales_pg.begin() as c:
            rebuild_version(c, old, rows(), content_fingerprint=old, row_schema_revision="test-v1")


def test_shared_proof_resumes_inside_a_block_without_skipping_rows(sales_pg):
    import hashlib

    data = []
    for n in range(2000):
        key = f"shared-page-{n}"
        if int(hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:8], 16) % 64 == 0:
            data.append(dict(rows()[0], source_row_key=key))
        if len(data) == 5:
            break
    for suffix in "abc":
        put(sales_pg, suffix, data, shared=True)
    age(sales_pg)
    first, second = step(sales_pg), step(sales_pg)
    assert first["status"] == second["status"] == "verifying"
    assert first["checked_rows"] == second["checked_rows"] == 2
    assert first["cursor"] != second["cursor"]
    assert step(sales_pg)["status"] == "reclaiming"
    assert finish(sales_pg)["status"] == "reclaimed"
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 5
        assert c.scalar(sa.text("SELECT original_rows FROM region_retirements")) == 5
