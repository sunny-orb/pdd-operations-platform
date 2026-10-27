"""M1首批接入：以独立PostgreSQL验证真正提交、重跑、拒绝及恢复。"""

import importlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from test_m1_database import pg as migration_fixture

pg = migration_fixture


def ingestion():
    spec = importlib.util.find_spec("app.modules.products.ingestion")
    assert spec is not None, "M1来源规范化尚未实现"
    return importlib.import_module("app.modules.products.ingestion")


def sample():
    return {
        "product_daily": [
            dict(
                z_id="p1",
                dt="2026-10-22 00:00:00",
                relevant_dt=None,
                time_granularity="day",
                shop_id="663599807",
                shop_name="合成店",
                spu_id="10000000000000000001",
                spu_name="合成商品",
                product_uv=0,
                product_pv=3,
                favorite_product_users=1,
                transaction_users=2,
                transaction_orders=3,
                transaction_products=4,
                transaction_amount="123.123456789",
                transaction_conversion_rate="1.5",
                create_time=None,
            )
        ],
        "promotion_records": [
            dict(
                id="a1",
                dt="2026-10-22 00:00:00",
                start_dt="2026-10-22 00:00:00",
                time_granularity="day",
                shop_id="663599807",
                shop_name="合成店",
                spu_id="10000000000000000001",
                spu_name="合成商品",
                promotion_name="方式一",
                spu_grouping=None,
                promotion_type=None,
                bidding_method=None,
                is_deleted="已删除",
                total_cost=None,
                transaction_cost=None,
                transaction_amount="9.20",
                transaction_count="0.5",
                net_transaction_amount="8.30",
                net_transaction_count="0.5",
                settlement_transaction_amount=None,
                settlement_transaction_count=None,
                impression_count="2100",
                click_count="3",
                actual_roi=None,
                net_actual_roi=None,
                settlement_roi=None,
                click_through_rate=None,
                click_conversion_rate=None,
                create_time=None,
            )
        ],
        "product_profile_source": [
            dict(
                __id="r1",
                spkh="款一",
                spmc="运营名",
                pl="帽子",
                cpdj="新品",
                fzyy="运营一",
                sjsjsj=None,
                zt1="在售",
                spigf="10000000000000000001",
                spifs=None,
                spihw=None,
                spiyj=None,
                spixb=None,
                spitztx=None,
            ),
            dict(
                __id="r2",
                spkh="款二",
                spmc="另一名称",
                pl="帽子",
                cpdj="A级",
                fzyy="运营二",
                sjsjsj=None,
                zt1=None,
                spigf="10000000000000000001",
                spifs="1002/1003",
                spihw=None,
                spiyj=None,
                spixb=None,
                spitztx=None,
            ),
        ],
    }


def windows():
    return [(date(2026, 8, 24), date(2026, 10, 22)), (date(2025, 8, 24), date(2025, 10, 22))]


def test_normalization_preserves_original_decimal_and_uncertain_profiles():
    module = ingestion()
    prepared = module.prepare(sample(), windows())
    row = prepared.rows["product_daily"][0]
    assert row["product_id"] == "10000000000000000001"
    assert row["product_revenue"] == Decimal("123.123456789")
    assert row["source_conversion_rate"] == Decimal("1.5")
    assert row["visitors"] == 0 and row["buyers"] == 2
    assert "buyers_exceed_visitors" in row["quality_flags"]
    assert len(prepared.links) == 4
    assert prepared.rows["promotion_records"][0]["spend"] is None
    assert prepared.rows["product_profile_source"][0]["source_listing_status"] == "在售"


def test_scope_duplicate_and_nonfinite_values_block_batch():
    module = ingestion()
    for change in ("duplicate", "outside", "nonfinite", "rolling", "float_id"):
        data = sample()
        if change == "duplicate":
            data["product_daily"] *= 2
        if change == "outside":
            data["product_daily"][0]["dt"] = "2026-07-01 00:00:00"
        if change == "nonfinite":
            data["product_daily"][0]["transaction_amount"] = "NaN"
        if change == "rolling":
            data["product_daily"][0]["time_granularity"] = "recent_7d"
        if change == "float_id":
            data["product_daily"][0]["spu_id"] = 1.1
        with pytest.raises(module.IngestionError):
            module.prepare(data, windows())


def test_initial_windows_are_two_exact_calendar_periods():
    module = ingestion()
    assert module.initial_windows(date(2026, 10, 22)) == windows()
    with pytest.raises(module.IngestionError):
        module.initial_windows(date(2024, 2, 29))


@pytest.fixture
def repo(pg):
    ingestion()
    repository = importlib.import_module("app.modules.products.ingestion_repository")
    from app.db.base import Base

    engine, _ = pg
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield repository.IngestionRepository(engine), engine
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


def run(repository, data=None):
    return repository.run(lambda: sample() if data is None else data, windows())


def test_publish_originals_and_idempotent_rerun(repo):
    repository, engine = repo
    first = run(repository)
    second = run(repository)
    assert first["publication_id"] == second["publication_id"]
    assert second["changed_datasets"] == []
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM product_days")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT count(*) FROM product_profile_links")).scalar_one() == 4
        )
        assert connection.execute(text("SELECT count(*) FROM product_profiles")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM dataset_versions")).scalar_one() == 4
        assert (
            connection.execute(
                text("SELECT count(*) FROM sync_runs WHERE status='succeeded'")
            ).scalar_one()
            == 8
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM dataset_coverage WHERE complete_through IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert connection.execute(
            text("SELECT product_revenue FROM product_days")
        ).scalar_one() == Decimal("123.123456789")


def test_revision_changes_one_dataset_without_duplicate_facts(repo):
    repository, engine = repo
    first = run(repository)
    data = sample()
    data["product_daily"][0]["transaction_amount"] = "200.000000001"
    second = run(repository, data)
    assert second["publication_id"] != first["publication_id"]
    assert second["changed_datasets"] == ["product_daily"]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM dataset_versions")).scalar_one() == 5
        assert connection.execute(text("SELECT count(*) FROM product_days")).scalar_one() == 1


def test_interrupted_attempt_is_failed_and_orphan_recovers_on_next_run(repo):
    repository, engine = repo

    def interrupted():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        repository.run(interrupted, windows())
    with engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM sync_runs WHERE status='failed'")
            ).scalar_one()
            == 4
        )
        # 模拟进程被杀，失主任务来不及自行记账；下一轮持独占锁后修复。
        connection.execute(text("UPDATE sync_runs SET status='running', finished_at=NULL"))
    run(repository)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM sync_runs WHERE status IN ('running','pending')")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM sync_runs WHERE error_code='INTERRUPTED_PREVIOUS_ATTEMPT'"
                )
            ).scalar_one()
            == 4
        )


def test_missing_old_key_or_reused_ad_id_keeps_current_publication(repo):
    repository, engine = repo
    first = run(repository)
    module = ingestion()
    data = sample()
    data["product_daily"] = []
    with pytest.raises(module.IngestionError):
        run(repository, data)
    data = sample()
    data["promotion_records"][0]["spu_id"] = "10000000000000000002"
    with pytest.raises(module.IngestionError):
        run(repository, data)
    with engine.connect() as connection:
        assert (
            str(
                connection.execute(
                    text("SELECT publication_id FROM publication_heads")
                ).scalar_one()
            )
            == first["publication_id"]
        )
        assert connection.execute(text("SELECT count(*) FROM product_days")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT count(*) FROM sync_runs WHERE status='failed'")
            ).scalar_one()
            == 8
        )


def test_invalid_source_retains_staging_and_failed_attempt_then_recovers(repo):
    repository, engine = repo
    module = ingestion()
    data = sample()
    data["product_daily"][0]["transaction_amount"] = "NaN"
    with pytest.raises(module.IngestionError):
        run(repository, data)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM product_days")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT count(*) FROM stg_product_records")).scalar_one() == 1
        )
        assert connection.execute(text("SELECT count(*) FROM publication_heads")).scalar_one() == 0
    assert run(repository)["changed_datasets"]


def test_session_lock_rejects_concurrent_ingestion_without_attempt(repo):
    repository, engine = repo
    module = ingestion()
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(hashtext('pdd:m1:ingestion'))"))
        other.commit()
        try:
            with pytest.raises(module.IngestionError):
                run(repository)
        finally:
            other.execute(text("SELECT pg_advisory_unlock(hashtext('pdd:m1:ingestion'))"))
            other.commit()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM sync_runs")).scalar_one() == 0


def test_database_failure_after_fact_write_rolls_back_entire_publication(repo):
    repository, engine = repo
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION reject_test_publication() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure'; END; $$; "
                "CREATE TRIGGER reject_test_publication BEFORE INSERT ON publication_heads "
                "FOR EACH ROW EXECUTE FUNCTION reject_test_publication();"
            )
        )
    try:
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            run(repository)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM product_days")).scalar_one() == 0
            assert (
                connection.execute(text("SELECT count(*) FROM dataset_versions")).scalar_one() == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM publication_heads")).scalar_one() == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM sync_runs WHERE status='failed'")
                ).scalar_one()
                == 4
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER reject_test_publication ON publication_heads; "
                    "DROP FUNCTION reject_test_publication();"
                )
            )
