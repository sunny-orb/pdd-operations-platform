"""在独立真实PostgreSQL测试库验证迁移和M1基础数据边界。"""

import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

EXPECTED = {
    "shops",
    "shop_products",
    "shop_source_mappings",
    "product_profile_records",
    "product_profile_links",
    "product_profiles",
    "product_days",
    "promotion_records",
    "stg_product_records",
    "stg_profile_records",
    "stg_promotion_records",
    "sync_runs",
    "dataset_versions",
    "dataset_coverage",
    "data_publications",
    "publication_datasets",
    "publication_heads",
}


@pytest.fixture(scope="module")
def pg():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("独立PostgreSQL集成测试需 npm run db:test；不使用SQLite替代")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "pdd_operations_test":
        pytest.fail("仅允许在独立pdd_operations_test库执行迁移回退和合成数据测试")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    engine = create_engine(url, hide_parameters=True)
    command.upgrade(config, "head")
    yield engine, config
    engine.dispose()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous


def test_00_migration_roundtrip_and_metadata_alignment(pg):
    engine, config = pg
    assert EXPECTED <= set(inspect(engine).get_table_names()), "M1基础表尚未迁移"
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(config, "head")
    command.check(config)


@pytest.fixture
def db(pg):
    engine, _ = pg
    with engine.connect() as connection:
        transaction = connection.begin()
        ids = {}
        for dataset in (
            "product_daily",
            "promotion_records",
            "product_profile_source",
            "product_profile",
            "shop_identity",
        ):
            run, version = uuid4(), uuid4()
            ids[dataset] = version
            ids[dataset + "_run"] = run
            connection.execute(
                text(
                    "INSERT INTO sync_runs (run_id,dataset_id,source_id) VALUES (:r,:d,'synthetic')"
                ),
                {"r": run, "d": dataset},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions "
                    "(version_id,dataset_id,source_id,sync_run_id) "
                    "VALUES (:v,:d,'synthetic',:r)"
                ),
                {"v": version, "d": dataset, "r": run},
            )
        for shop in ("test-shop-a", "test-shop-b"):
            connection.execute(
                text(
                    "INSERT INTO shops(platform_code,shop_id,shop_name) "
                    "VALUES ('pdd',:s,'合成店铺')"
                ),
                {"s": shop},
            )
            connection.execute(
                text(
                    "INSERT INTO shop_products(platform_code,shop_id,product_id) "
                    "VALUES ('pdd',:s,'10000000000000000001')"
                ),
                {"s": shop},
            )
        yield connection, ids
        transaction.rollback()


def product(connection, ids, **overrides):
    """写独立测试库合成商品日；每项期望值由测试独立给出。"""
    values = dict(
        shop="test-shop-a",
        day="2026-10-20",
        source="synthetic-row",
        revenue=Decimal("123456789.123456789"),
        visitors=0,
        buyers=2,
        rate=Decimal("1.5"),
        version=ids["product_daily"],
        run=ids["product_daily_run"],
    )
    values.update(overrides)
    connection.execute(
        text(
            "INSERT INTO product_days "
            "(platform_code,shop_id,product_id,business_date,source_id,"
            "source_record_id,source_dt,product_revenue,visitors,buyers,"
            "source_conversion_rate,last_changed_version_id,last_seen_run_id) "
            "VALUES ('pdd',:shop,'10000000000000000001',:day,'synthetic',"
            ":source,CAST(CAST(:day AS date) AS timestamp),:revenue,:visitors,:buyers,"
            ":rate,:version,:run)"
        ),
        values,
    )


def promotion(connection, ids, record="ad-one", name="推广方式一", spend="10.25"):
    connection.execute(
        text(
            "INSERT INTO promotion_records "
            "(source_id,source_record_id,platform_code,shop_id,product_id,"
            "business_date,source_dt,source_start_dt,promotion_name,spend,"
            "last_changed_version_id,last_seen_run_id) "
            "VALUES ('synthetic',:id,'pdd','test-shop-a','10000000000000000001',"
            "'2026-10-20','2026-10-20','2026-10-20',:name,:spend,:version,:run)"
        ),
        {
            "id": record,
            "name": name,
            "spend": spend,
            "version": ids["promotion_records"],
            "run": ids["promotion_records_run"],
        },
    )


def test_exact_decimal_null_and_abnormal_conversion_preserved(db):
    connection, ids = db
    product(connection, ids)
    row = connection.execute(
        text(
            "SELECT product_revenue,visitors,buyers,units,source_conversion_rate FROM product_days"
        )
    ).one()
    assert tuple(row) == (Decimal("123456789.123456789"), 0, 2, None, Decimal("1.5"))


def test_product_business_key_upsert_and_cross_shop_identity(db):
    connection, ids = db
    product(connection, ids)
    with pytest.raises(IntegrityError), connection.begin_nested():
        product(connection, ids, source="different-source-id")
    product(connection, ids, shop="test-shop-b")
    connection.execute(
        text(
            "INSERT INTO product_days SELECT * FROM product_days "
            "WHERE shop_id='test-shop-a' ON CONFLICT "
            "(platform_code,shop_id,product_id,business_date) "
            "DO UPDATE SET product_revenue=42.99"
        )
    )
    assert connection.execute(text("SELECT COUNT(*) FROM product_days")).scalar_one() == 2
    assert connection.execute(
        text("SELECT product_revenue FROM product_days WHERE shop_id='test-shop-a'")
    ).scalar_one() == Decimal("42.99")


def test_source_windows_and_nonfinite_numeric_rejected(db):
    connection, ids = db
    product(connection, ids)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE product_days SET source_window='recent_7d'"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE product_days SET product_revenue='NaN'"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE product_days SET source_dt='2026-10-21'"))


def test_promotion_keeps_two_modes_null_and_fractional_source_counts(db):
    connection, ids = db
    promotion(connection, ids)
    promotion(connection, ids, "ad-two", "推广方式二", None)
    connection.execute(
        text(
            "UPDATE promotion_records SET clicks=0.5,"
            "settled_attributed_revenue=NULL WHERE source_record_id='ad-two'"
        )
    )
    row = connection.execute(
        text("SELECT COUNT(*),COUNT(spend),SUM(spend),SUM(clicks) FROM promotion_records")
    ).one()
    assert tuple(row) == (2, 1, Decimal("10.25"), Decimal("0.5"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        promotion(connection, ids)


def test_promotion_reused_id_cannot_move_to_other_identity_or_day(db):
    connection, ids = db
    promotion(connection, ids)
    for change in (
        "shop_id='test-shop-b'",
        "business_date='2026-10-21',source_dt='2026-10-21',source_start_dt='2026-10-21'",
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text(f"UPDATE promotion_records SET {change}"))
    # 常规指标/名称修订允许，不把修改名称误判为来源ID重建。
    connection.execute(
        text("UPDATE promotion_records SET spend=12.75,promotion_name='推广名称修订'")
    )
    assert connection.execute(text("SELECT spend FROM promotion_records")).scalar_one() == Decimal(
        "12.75"
    )


def test_profile_many_links_but_only_one_resolved_row_per_product(db):
    connection, ids = db
    connection.execute(
        text(
            "INSERT INTO shop_source_mappings "
            "(source_id,source_shop_key,platform_code,shop_id,mapping_version) "
            "VALUES ('synthetic','spigf','pdd','test-shop-a',:v)"
        ),
        {"v": ids["shop_identity"]},
    )
    for raw_id in ("raw-one", "raw-two"):
        connection.execute(
            text(
                "INSERT INTO product_profile_records "
                "(source_id,source_profile_id,last_changed_version_id,last_seen_run_id) "
                "VALUES ('synthetic',:id,:v,:r)"
            ),
            {
                "id": raw_id,
                "v": ids["product_profile_source"],
                "r": ids["product_profile_source_run"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO product_profile_links "
                "(source_id,source_profile_id,source_column,platform_code,shop_id,product_id) "
                "VALUES ('synthetic',:id,'spigf','pdd','test-shop-a',"
                "'10000000000000000001')"
            ),
            {"id": raw_id},
        )
    connection.execute(
        text(
            "INSERT INTO product_profiles "
            "(platform_code,shop_id,product_id,source_id,last_changed_version_id,"
            "last_seen_run_id,field_statuses) VALUES ('pdd','test-shop-a',"
            "'10000000000000000001','synthetic',:v,:r,"
            '\'{"level":"conflict"}\')'
        ),
        {"v": ids["product_profile"], "r": ids["product_profile_run"]},
    )
    assert connection.execute(text("SELECT COUNT(*) FROM product_profile_links")).scalar_one() == 2
    assert connection.execute(text("SELECT level FROM product_profiles")).scalar_one() is None
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("INSERT INTO product_profiles SELECT * FROM product_profiles"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE product_profile_links SET shop_id='test-shop-b'"))


def test_publication_rejects_wrong_dataset_and_scope(db):
    connection, ids = db
    pub = uuid4()
    connection.execute(
        text("INSERT INTO data_publications(publication_id,scope) VALUES (:p,'M1')"), {"p": pub}
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO publication_datasets(publication_id,dataset_id,version_id) "
                "VALUES (:p,'promotion_records',:v)"
            ),
            {"p": pub, "v": ids["product_daily"]},
        )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("INSERT INTO publication_heads(scope,publication_id) VALUES ('M3',:p)"), {"p": pub}
        )


def test_failed_transaction_keeps_fact_and_publication_pointer(db):
    connection, ids = db
    product(connection, ids)
    old, new = uuid4(), uuid4()
    for pub in (old, new):
        connection.execute(
            text("INSERT INTO data_publications(publication_id,scope) VALUES (:p,'M1')"), {"p": pub}
        )
    connection.execute(
        text("INSERT INTO publication_heads(scope,publication_id) VALUES ('M1',:p)"), {"p": old}
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE product_days SET product_revenue=999"))
        connection.execute(
            text("UPDATE publication_heads SET publication_id=:p WHERE scope='M1'"), {"p": new}
        )
        connection.execute(
            text(
                "INSERT INTO publication_datasets(publication_id,dataset_id,version_id) "
                "VALUES (:p,'product_daily',:v)"
            ),
            {"p": new, "v": uuid4()},
        )
    assert connection.execute(
        text("SELECT product_revenue FROM product_days")
    ).scalar_one() == Decimal("123456789.123456789")
    assert (
        connection.execute(text("SELECT publication_id FROM publication_heads")).scalar_one() == old
    )


def test_coverage_does_not_claim_unknown_day_is_complete(db):
    connection, ids = db
    values = {"v": ids["product_daily"]}
    connection.execute(
        text(
            "INSERT INTO dataset_coverage "
            "(version_id,platform_code,shop_id,window_from,window_to) "
            "VALUES (:v,'pdd','test-shop-a','2026-10-14','2026-10-20')"
        ),
        values,
    )
    assert (
        connection.execute(text("SELECT complete_through FROM dataset_coverage")).scalar_one()
        is None
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text("UPDATE dataset_coverage SET completeness='complete'"))


def test_resolved_profile_mapping_version_must_be_identity_dataset(db):
    connection, ids = db
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(
                "INSERT INTO product_profiles "
                "(platform_code,shop_id,product_id,source_id,last_changed_version_id,"
                "last_seen_run_id,mapping_version) VALUES ('pdd','test-shop-a',"
                "'10000000000000000001','synthetic',:v,:r,:wrong)"
            ),
            {
                "v": ids["product_profile"],
                "r": ids["product_profile_run"],
                "wrong": ids["product_daily"],
            },
        )
