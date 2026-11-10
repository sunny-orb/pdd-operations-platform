"""部署SQL必须与现行完整迁移一致；真实PG验证空库、权限及重复执行拒绝。"""

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from migrations_v2.local import local_url, migrate

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "schema_renderer", ROOT / "scripts/render-db-schema.py"
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def test_delivery_is_current_and_offline():
    with patch("sqlalchemy.create_engine", side_effect=AssertionError("禁止接库")):
        sql = renderer.render_sql()
    assert sql == (ROOT / "docs/database-schema/schema.sql").read_text()
    assert "CREATE TABLE public.weather_publications" in sql
    assert "scheduled_slot_at" in sql
    assert "alembic_version_v2" in sql
    assert "CREATE ROLE" not in sql


def catalog(c):
    """比较完整列/约束/索引/函数/触发器/表及列权限；排除库名和对象OID。"""
    queries = {
        "columns": "SELECT table_name,column_name,data_type,is_nullable,column_default "
        "FROM information_schema.columns WHERE table_schema='public' ORDER BY 1,2",
        "views": "SELECT viewname,definition FROM pg_views WHERE schemaname='public' ORDER BY 1",
        "constraints": "SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2",
        "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes "
        "WHERE schemaname='public' ORDER BY 1,2",
        "functions": "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
        "WHERE pronamespace='public'::regnamespace ORDER BY 1,2",
        "triggers": "SELECT tgrelid::regclass::text,tgname,pg_get_triggerdef(oid) "
        "FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN "
        "(SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace) ORDER BY 1,2",
        "permissions": "SELECT table_name,column_name,grantee,privilege_type "
        "FROM information_schema.column_privileges WHERE table_schema='public' "
        "AND grantee IN ('pdd_app','pdd_ingest') ORDER BY 1,2,3,4",
    }
    return {key: c.exec_driver_sql(sql).all() for key, sql in queries.items()}


def test_complete_sql_matches_migrations_on_real_pg():
    names = ["pdd_operations_v2_test_" + uuid.uuid4().hex[:12] for _ in range(2)]
    admin = sa.create_engine(local_url("postgres"), isolation_level="AUTOCOMMIT")
    engines = []
    created = []
    try:
        for name in names:
            with admin.connect() as c:
                c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
            created.append(name)
            engines.append(sa.create_engine(local_url(name)))
        with engines[0].connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.connection.driver_connection.execute(renderer.render_sql(), prepare=False)
        with engines[1].begin() as c:
            migrate(c)
            c.connection.driver_connection.execute(renderer.runtime_permissions(), prepare=False)
        with engines[0].connect() as a, engines[1].connect() as b:
            actual = catalog(a)
            assert actual == catalog(b)
            domain_columns = [
                row
                for row in actual["columns"]
                if row[0] != "alembic_version_v2" and not row[0].startswith("read_")
            ]
            assert len({row[0] for row in domain_columns}) == 45
            assert len(domain_columns) == 711
            assert len(actual["views"]) == 5
            assert sum("FOREIGN KEY" in row[2] for row in actual["constraints"]) == 71
            head = ScriptDirectory.from_config(
                Config(str(ROOT / "apps/api/alembic-v2.ini"))
            ).get_current_head()
            assert a.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == head
            assert a.scalar(sa.text("SELECT has_table_privilege('pdd_app','shops','SELECT')"))
            assert not a.scalar(sa.text("SELECT has_table_privilege('pdd_app','shops','INSERT')"))
        with engines[0].connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            with pytest.raises(psycopg.Error, match="仅允许空库初始化"):
                c.connection.driver_connection.execute(renderer.render_sql(), prepare=False)
            c.exec_driver_sql("ROLLBACK")
            assert c.scalar(sa.text("SELECT count(*) FROM alembic_version_v2")) == 1
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as c:
            for name in created:
                c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


@pytest.mark.parametrize(
    ("schema", "kind"),
    [
        ("other_schema", "table"),
        ("other_schema", "function"),
        ("other_schema", "type"),
        ("public", "type"),
    ],
)
def test_initialization_rejects_existing_objects_without_changing_data_or_permissions(schema, kind):
    """public为空也不能初始化已有库；独立类型同样属于既有结构。"""
    name = "pdd_operations_v2_test_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(local_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
    engine = sa.create_engine(local_url(name), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as c:
            if schema != "public":
                c.exec_driver_sql(f"CREATE SCHEMA {schema}")
            if kind == "table":
                c.exec_driver_sql(f"CREATE TABLE {schema}.sentinel (value TEXT)")
                c.exec_driver_sql(f"INSERT INTO {schema}.sentinel VALUES ('preserved')")
                probe = f"SELECT value FROM {schema}.sentinel"
            elif kind == "function":
                c.exec_driver_sql(
                    f"CREATE FUNCTION {schema}.sentinel() RETURNS TEXT "
                    "LANGUAGE SQL AS $$ SELECT 'preserved'::TEXT $$"
                )
                probe = f"SELECT {schema}.sentinel()"
            else:
                c.exec_driver_sql(f"CREATE TYPE {schema}.sentinel AS ENUM ('preserved')")
                probe = f"SELECT 'preserved'::{schema}.sentinel::TEXT"
            acl_sql = "SELECT datacl::TEXT FROM pg_database WHERE datname=current_database()"
            public_acl_sql = "SELECT nspacl::TEXT FROM pg_namespace WHERE nspname='public'"
            before_acl = c.scalar(sa.text(acl_sql))
            before_public_acl = c.scalar(sa.text(public_acl_sql))
            with pytest.raises(psycopg.Error, match="仅允许空库初始化"):
                c.connection.driver_connection.execute(renderer.render_sql(), prepare=False)
            c.exec_driver_sql("ROLLBACK")
            assert c.scalar(sa.text(probe)) == "preserved"
            assert c.scalar(sa.text(acl_sql)) == before_acl
            assert c.scalar(sa.text(public_acl_sql)) == before_public_acl
            assert c.scalar(sa.text("SELECT to_regclass('public.alembic_version_v2')")) is None
            assert c.scalar(sa.text("SELECT to_regclass('public.shops')")) is None
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()
