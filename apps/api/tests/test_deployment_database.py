"""公司单账号交付：非超级用户、既有监控对象与原子初始化。"""

import importlib.util
import uuid
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa

from migrations_v2.local import local_url

ROOT = Path(__file__).resolve().parents[3]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None and Path(spec.origin).exists()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("database", ["pdd_operations", "pdd_dashboard"])
def test_maintenance_connection_preserves_selected_project_database(database):
    initializer = load_script("relocation_initializer", "init-deployment-db.py")
    params = initializer.connection_parameters(
        f"postgresql+psycopg://songbing:example@pg.kktree.cn:5432/{database}?sslmode=require"
    )
    assert params["dbname"] == database
    assert params["sslmode"] == "require"
    assert params["user"] == "songbing"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://songbing:example@pg.kktree.cn/other_project",
        "postgresql+psycopg://postgres:example@pg.kktree.cn/pdd_dashboard",
        "postgresql://songbing:example@pg.kktree.cn/pdd_dashboard",
    ],
)
def test_maintenance_connection_rejects_unapproved_database_or_identity(url):
    initializer = load_script("relocation_initializer", "init-deployment-db.py")
    with pytest.raises(ValueError, match="DEPLOY_CONNECTION_INVALID"):
        initializer.connection_parameters(url)


@pytest.fixture
def allocated_database():
    """仅在随机本机库授权现有普通测试角色；不修改集群角色。"""
    name = "pdd_operations_v2_test_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(local_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
    engine = sa.create_engine(local_url(name), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as c:
            c.exec_driver_sql("CREATE SCHEMA monitor")
            c.exec_driver_sql("CREATE TABLE monitor.sentinel (value text)")
            c.exec_driver_sql("INSERT INTO monitor.sentinel VALUES ('preserved')")
            c.exec_driver_sql("REVOKE ALL ON SCHEMA monitor FROM PUBLIC")
            c.exec_driver_sql("GRANT USAGE, CREATE ON SCHEMA public TO pdd_app")
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


def test_single_account_initializes_without_database_ownership_and_preserves_monitor(
    allocated_database,
):
    renderer = load_script("deployment_schema_renderer", "render-db-schema.py")
    with allocated_database.connect() as c:
        acl = c.exec_driver_sql(
            "SELECT datacl::text FROM pg_database WHERE datname=current_database()"
        ).scalar()
        c.exec_driver_sql("SET ROLE pdd_app")
        c.connection.driver_connection.execute(
            renderer.render_sql(
                runtime_account="pdd_app", database=allocated_database.url.database
            ),
            prepare=False,
        )
        assert (
            c.exec_driver_sql("SELECT version_num FROM public.alembic_version_v2").scalar_one()
            == "20261114_18"
        )
        assert (
            c.exec_driver_sql(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name <> 'alembic_version_v2' "
                "AND table_name NOT LIKE 'read_%%'"
            ).scalar_one()
            == 719
        )
        assert (
            c.exec_driver_sql(
                "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='shops'"
            ).scalar_one()
            == "pdd_app"
        )
        assert c.exec_driver_sql(
            "SELECT has_table_privilege('public.shops','INSERT,UPDATE,DELETE')"
        ).scalar_one()
        c.exec_driver_sql("RESET ROLE")
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar_one() == "preserved"
        assert (
            c.exec_driver_sql(
                "SELECT datacl::text FROM pg_database WHERE datname=current_database()"
            ).scalar()
            == acl
        )
        assert (
            c.exec_driver_sql(
                "SELECT nspacl::text FROM pg_namespace WHERE nspname='monitor'"
            ).scalar_one()
            == "{pdd_admin=UC/pdd_admin}"
        )


@pytest.mark.parametrize("schema", ["public", "other_schema"])
def test_single_account_refuses_existing_objects_without_partial_changes(
    allocated_database, schema
):
    renderer = load_script("deployment_schema_renderer", "render-db-schema.py")
    with allocated_database.connect() as c:
        if schema != "public":
            c.exec_driver_sql(f"CREATE SCHEMA {schema}")
        c.exec_driver_sql(f"CREATE TYPE {schema}.sentinel AS ENUM ('preserved')")
        c.exec_driver_sql("SET ROLE pdd_app")
        with pytest.raises(psycopg.Error, match="仅允许应用空结构初始化"):
            c.connection.driver_connection.execute(
                renderer.render_sql(
                    runtime_account="pdd_app", database=allocated_database.url.database
                ),
                prepare=False,
            )
        c.exec_driver_sql("ROLLBACK")
        c.exec_driver_sql("RESET ROLE")
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar_one() == "preserved"


def test_single_account_rejects_wrong_identity(allocated_database):
    renderer = load_script("deployment_schema_renderer", "render-db-schema.py")
    with allocated_database.connect() as c:
        with pytest.raises(psycopg.Error, match="初始化身份不符"):
            c.connection.driver_connection.execute(
                renderer.render_sql(
                    runtime_account="pdd_app", database=allocated_database.url.database
                ),
                prepare=False,
            )
        c.exec_driver_sql("ROLLBACK")
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None


def test_deployment_check_is_read_only_and_apply_is_repeatable(allocated_database):
    initializer = load_script("deployment_initializer", "init-deployment-db.py")
    with allocated_database.connect() as c:
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        name = c.exec_driver_sql("SELECT current_database()").scalar_one()
        assert initializer.initialize(raw, database=name, account="pdd_app", apply=False) == "empty"
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None
        assert (
            initializer.initialize(raw, database=name, account="pdd_app", apply=True)
            == "initialized"
        )
        assert (
            initializer.initialize(raw, database=name, account="pdd_app", apply=True)
            == "already_initialized"
        )
        c.exec_driver_sql("UPDATE alembic_version_v2 SET version_num='20261031_09'")
        with pytest.raises(ValueError, match="DEPLOY_REVISION_MISMATCH"):
            initializer.initialize(raw, database=name, account="pdd_app", apply=True)


def test_sync_runtime_accepts_explicit_allocated_connection(allocated_database, monkeypatch):
    from app.jobs.sync_shop_daily import runtime_engine

    monkeypatch.setenv(
        "SYNC_DATABASE_URL", allocated_database.url.render_as_string(hide_password=False)
    )
    engine = runtime_engine()
    try:
        with engine.connect() as c:
            assert (
                c.exec_driver_sql("SELECT current_database()").scalar_one()
                == allocated_database.url.database
            )
    finally:
        engine.dispose()


def test_production_sync_requires_explicit_connection(monkeypatch):
    from app.jobs.sync_shop_daily import runtime_engine

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SYNC_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="SYNC_DATABASE_URL_REQUIRED"):
        runtime_engine()


def test_initializer_checks_target_before_writing(allocated_database):
    initializer = load_script("deployment_initializer", "init-deployment-db.py")
    with allocated_database.connect() as c:
        with pytest.raises(ValueError, match="DEPLOY_TARGET_MISMATCH"):
            initializer.initialize(
                c.connection.driver_connection,
                database="wrong_database",
                account="pdd_admin",
                apply=True,
            )
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None


def test_standalone_sql_refuses_wrong_database(allocated_database):
    renderer = load_script("deployment_schema_renderer", "render-db-schema.py")
    with allocated_database.connect() as c:
        c.exec_driver_sql("SET ROLE pdd_app")
        with pytest.raises(psycopg.Error, match="初始化数据库不符"):
            c.connection.driver_connection.execute(
                renderer.render_sql(runtime_account="pdd_app"), prepare=False
            )
        c.exec_driver_sql("ROLLBACK")
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None


def install_extensions(c, schema):
    if schema != "public":
        c.exec_driver_sql(f"CREATE SCHEMA {schema}")
    for extension in (
        "btree_gin",
        "btree_gist",
        "file_fdw",
        "intagg",
        "intarray",
        "pg_trgm",
        "postgres_fdw",
    ):
        c.exec_driver_sql(f"CREATE EXTENSION {extension} WITH SCHEMA {schema}")


def extension_snapshot(c):
    return c.exec_driver_sql(
        "SELECT e.oid, e.extname, e.extversion, e.extowner, e.extnamespace, "
        "d.classid, d.objid, d.objsubid, pg_describe_object(d.classid,d.objid,d.objsubid), "
        "to_jsonb(p),to_jsonb(t),to_jsonb(c) "
        "FROM pg_extension e JOIN pg_depend d ON d.refobjid=e.oid "
        "AND d.refclassid='pg_extension'::regclass AND d.deptype='e' "
        "LEFT JOIN pg_proc p ON d.classid='pg_proc'::regclass AND p.oid=d.objid "
        "LEFT JOIN pg_type t ON d.classid='pg_type'::regclass AND t.oid=d.objid "
        "LEFT JOIN pg_class c ON d.classid='pg_class'::regclass AND c.oid=d.objid "
        "ORDER BY e.oid,d.classid,d.objid,d.objsubid"
    ).all()


@pytest.mark.parametrize("schema", ["public", "company_extensions"])
@pytest.mark.parametrize("entrypoint", ["sql", "maintenance"])
def test_registered_extensions_survive_initialization(allocated_database, schema, entrypoint):
    initializer = load_script("extension_initializer", "init-deployment-db.py")
    with allocated_database.connect() as c:
        install_extensions(c, schema)
        c.exec_driver_sql(f"CREATE TABLE {schema}.extension_state (value text)")
        c.exec_driver_sql(f"INSERT INTO {schema}.extension_state VALUES ('preserved')")
        c.exec_driver_sql(f"ALTER EXTENSION pg_trgm ADD TABLE {schema}.extension_state")
        before = extension_snapshot(c)
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        args = {"database": allocated_database.url.database, "account": "pdd_app"}
        assert initializer.initialize(raw, **args) == "empty"
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None
        if entrypoint == "sql":
            raw.execute(
                initializer.renderer.render_sql(
                    runtime_account="pdd_app", database=args["database"]
                ),
                prepare=False,
            )
        else:
            assert initializer.initialize(raw, **args, apply=True) == "initialized"
        assert initializer.initialize(raw, **args) == "already_initialized"
        assert initializer.initialize(raw, **args, apply=True) == "already_initialized"
        c.exec_driver_sql("RESET ROLE")
        assert extension_snapshot(c) == before
        assert (
            c.exec_driver_sql(f"SELECT value FROM {schema}.extension_state").scalar_one()
            == "preserved"
        )
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar_one() == "preserved"
        assert (
            c.exec_driver_sql(
                "SELECT nspacl::text FROM pg_namespace WHERE nspname='monitor'"
            ).scalar_one()
            == "{pdd_admin=UC/pdd_admin}"
        )


@pytest.mark.parametrize(
    "object_sql",
    [
        "CREATE TABLE public.unexpected (value public.query_int)",
        "CREATE TYPE public.unexpected AS ENUM ('preserved')",
        "CREATE FUNCTION public.unexpected() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
        "CREATE FUNCTION company_extensions.unexpected() RETURNS integer "
        "LANGUAGE sql AS 'SELECT 1'",
        "CREATE FUNCTION public.unexpected() RETURNS integer LANGUAGE sql AS 'SELECT 1'; "
        "ALTER FUNCTION public.unexpected() DEPENDS ON EXTENSION pg_trgm",
    ],
)
def test_extensions_do_not_hide_unknown_objects(allocated_database, object_sql):
    initializer = load_script("extension_guard_initializer", "init-deployment-db.py")
    with allocated_database.connect() as c:
        install_extensions(c, "public")
        c.exec_driver_sql("CREATE SCHEMA company_extensions")
        c.exec_driver_sql(object_sql)
        before = extension_snapshot(c)
        c.exec_driver_sql("SET ROLE pdd_app")
        for apply in (False, True):
            with pytest.raises(psycopg.Error, match="仅允许应用空结构初始化"):
                initializer.initialize(
                    c.connection.driver_connection,
                    database=allocated_database.url.database,
                    account="pdd_app",
                    apply=apply,
                )
            assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None
        c.exec_driver_sql("RESET ROLE")
        assert extension_snapshot(c) == before


@pytest.mark.parametrize("entrypoint", ["sql", "maintenance"])
def test_initialization_ddl_failure_rolls_back_with_extensions(allocated_database, entrypoint):
    initializer = load_script("rollback_initializer", "init-deployment-db.py")
    with allocated_database.connect() as c:
        install_extensions(c, "public")
        c.exec_driver_sql(
            "CREATE FUNCTION monitor.reject_index() RETURNS event_trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected ddl failure'; END $$"
        )
        c.exec_driver_sql(
            "CREATE EVENT TRIGGER reject_index ON ddl_command_start "
            "WHEN TAG IN ('CREATE INDEX') EXECUTE FUNCTION monitor.reject_index()"
        )
        before = extension_snapshot(c)
        c.exec_driver_sql("SET ROLE pdd_app")
        with pytest.raises(psycopg.Error, match="injected ddl failure"):
            if entrypoint == "maintenance":
                initializer.initialize(
                    c.connection.driver_connection,
                    database=allocated_database.url.database,
                    account="pdd_app",
                    apply=True,
                )
            else:
                c.connection.driver_connection.execute(
                    initializer.renderer.render_sql(
                        runtime_account="pdd_app", database=allocated_database.url.database
                    ),
                    prepare=False,
                )
        c.exec_driver_sql("ROLLBACK")
        assert c.exec_driver_sql("SELECT to_regclass('public.shops')").scalar() is None
        assert c.exec_driver_sql("SELECT to_regclass('public.alembic_version_v2')").scalar() is None
        c.exec_driver_sql("RESET ROLE")
        assert extension_snapshot(c) == before
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar_one() == "preserved"
