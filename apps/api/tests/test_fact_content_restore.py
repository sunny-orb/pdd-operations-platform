"""共享版本保护、完整PG备份与隔离恢复；绝不触碰普通项目数据库。"""

import subprocess
from uuid import uuid4

import pytest
import sqlalchemy as sa

from migrations_v2.local import ROOT, local_url
from tests.test_fact_content import rows, seal, version

pytest_plugins = ["tests.sales_pg_support"]


def test_shared_versions_and_all_new_guards_are_in_maintenance_catalog(sales_pg):
    from app.modules.database_retention import plan, validate_schema
    from app.modules.fact_content import write_version

    a = version(sales_pg, "a")
    with sales_pg.begin() as c:
        write_version(c, "product_sales_records", a, rows())
        seal(c, a)
        validate_schema(c)
    result = plan(sales_pg)
    assert any(r["version_id"] == a and r["reason"] == "protected" for r in result["kept"])
    with sales_pg.begin() as c:
        c.execute(sa.text("DROP TRIGGER pdd_content_guard ON shared_return_records"))
    with pytest.raises(ValueError, match="RETENTION_SCHEMA_GUARDS"):
        with sales_pg.connect() as c:
            validate_schema(c)


def test_complete_pg_dump_preserves_both_formats_and_shared_members(sales_pg, tmp_path):
    from app.modules.database_retention import require_local_test
    from app.modules.fact_content import write_version
    from tests.test_promotion_api import publish_products
    from tests.test_schema_delivery import catalog

    publish_products(sales_pg, tmp_path)
    a, b = version(sales_pg, "a"), version(sales_pg, "b")
    with sales_pg.begin() as c:
        for vid in (a, b):
            write_version(c, "product_sales_records", vid, rows())
            seal(c, vid)
    name = "pdd_operations_v2_test_" + uuid4().hex[:12]
    restored = sa.create_engine(local_url(name), hide_parameters=True)
    require_local_test(restored)
    admin = sa.create_engine(
        local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    prefix = [
        "docker",
        "compose",
        "--project-name",
        "pdd-operations-platform",
        "--env-file",
        str(ROOT / "var/local-db/docker.env"),
        "-f",
        str(ROOT / "compose.yaml"),
        "exec",
        "-T",
        "postgres",
    ]
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
    try:
        with restored.begin() as c:
            assert (
                c.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace"
                    )
                )
                == 0
            )
            c.execute(sa.text("DROP SCHEMA public"))  # 仅本测试刚创建的空库；不覆盖既有对象。
        dump = subprocess.run(
            [
                *prefix,
                "pg_dump",
                "-U",
                "pdd_admin",
                "-d",
                sales_pg.url.database,
                "--schema=public",
                "--no-owner",
                "--no-acl",
                "--format=custom",
            ],
            check=True,
            capture_output=True,
        ).stdout
        outcome = subprocess.run(
            [
                *prefix,
                "pg_restore",
                "-U",
                "pdd_admin",
                "-d",
                name,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
            ],
            input=dump,
            check=False,
            capture_output=True,
        )
        assert outcome.returncode == 0, outcome.stderr.decode()
        with sales_pg.connect() as source, restored.connect() as target:
            for table in (
                "read_product_sales_records",
                "read_product_daily_facts",
                "fact_content_members",
                "fact_content_blocks",
            ):
                assert (
                    source.execute(sa.text(f"SELECT * FROM {table} ORDER BY 1,2")).all()
                    == target.execute(sa.text(f"SELECT * FROM {table} ORDER BY 1,2")).all()
                )
            assert target.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 2
            # --no-acl 不恢复运行角色授权，其余列/函数/视图/保护均须一致。
            left, right = catalog(source), catalog(target)
            left.pop("permissions")
            right.pop("permissions")
            assert left == right
    finally:
        restored.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


def test_initializer_rejects_missing_shared_read_view(sales_pg):
    from migrations_v2.company_db_objects import TARGET_REVISION
    from tests.sales_pg_support import downgrade_to
    from tests.test_deployment_database import load_script

    script = load_script("content_initializer", "init-deployment-db.py")
    with sales_pg.begin() as c:
        # 先使用公司入口当前目标，再验证缺视图的目录保护。
        downgrade_to(c, TARGET_REVISION)
        c.execute(sa.text("DROP VIEW read_product_daily_facts"))
    with sales_pg.connect() as c, pytest.raises(ValueError, match="DEPLOY_SCHEMA_MISMATCH"):
        script.initialize(
            c.connection.driver_connection, database=sales_pg.url.database, account="pdd_admin"
        )


def test_company_initializer_accepts_reviewed_current_schema_without_changing_it(sales_pg):
    from migrations_v2.company_db_objects import TARGET_REVISION
    from tests.sales_pg_support import downgrade_to
    from tests.test_deployment_database import load_script

    script = load_script("local_comment_initializer", "init-deployment-db.py")
    with sales_pg.begin() as c:
        downgrade_to(c, TARGET_REVISION)
    with sales_pg.connect() as c:
        assert (
            script.initialize(
                c.connection.driver_connection, database=sales_pg.url.database, account="pdd_admin"
            )
            == "already_initialized"
        )
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == "20261118_20"
        assert c.scalar(sa.text("SELECT to_regclass('public.read_product_daily_facts')"))


@pytest.mark.parametrize(
    "table,code",
    [
        ("product_daily_facts", "product_daily"),
        ("product_promotion_records", "product_promotion"),
        ("product_region_records", "product_regions"),
    ],
)
def test_shared_target_versions_are_never_phase_one_candidates(sales_pg, table, code):
    from datetime import UTC, datetime, timedelta

    from app.modules.database_retention import plan
    from app.modules.fact_content import write_version
    from tests.test_published_versions import publish_pointer

    publish_pointer(sales_pg, code, "shared", published=False)
    with sales_pg.begin() as c:
        write_version(c, table, code + "-shared", [])
        seal(c, code + "-shared")
    for suffix in ("newer", "latest"):
        publish_pointer(sales_pg, code, suffix)
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO dataset_publications "
                "(id,dataset_code,revision,previous_version_id,version_id,occurred_at) "
                "VALUES('exit',:code,100,:a,:b,:time)"
            ),
            dict(
                code=code,
                a=code + "-shared",
                b=code + "-newer",
                time=datetime.now(UTC) - timedelta(days=2),
            ),
        )
    result = plan(sales_pg)
    assert code + "-shared" in result["protected"]
    assert not result["candidates"]


def downgrade_to_13(c):
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    config.attributes.update(connection=c, v2_expected_database=c.engine.url.database)
    command.downgrade(config, "20261110_13")


def test_upgrade_and_empty_shared_downgrade_preserve_legacy_fact_ids(sales_pg):
    from migrations_v2.baseline_20261028.schema import build_metadata
    from migrations_v2.local import migrate

    with sales_pg.begin() as c:
        downgrade_to_13(c)
    a = version(sales_pg, "a")
    with sales_pg.begin() as c:
        c.execute(
            build_metadata().tables["public.product_sales_records"].insert(),
            [dict(rows()[0], id="original-row-id", dataset_version_id=a)],
        )
        seal(c, a)
        original = c.execute(sa.text("SELECT * FROM product_sales_records")).all()
        migrate(c)
        assert c.execute(sa.text("SELECT * FROM read_product_sales_records")).all() == original
        downgrade_to_13(c)
        assert c.execute(sa.text("SELECT * FROM product_sales_records")).all() == original
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == "20261110_13"


def test_nonempty_shared_downgrade_refuses_without_deleting_data(sales_pg):
    from app.modules.fact_content import write_version

    a = version(sales_pg, "a")
    with sales_pg.begin() as c:
        before_revision = c.scalar(sa.text("SELECT version_num FROM alembic_version_v2"))
        write_version(c, "product_sales_records", a, rows())
        seal(c, a)
    with pytest.raises(sa.exc.DBAPIError, match="CONTENT_DOWNGRADE_REQUIRES_EMPTY"):
        with sales_pg.begin() as c:
            downgrade_to_13(c)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM read_product_sales_records")) == 2
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == before_revision
