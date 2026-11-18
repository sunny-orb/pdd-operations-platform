"""18只扩展受控维护身份；合成随机PG验证数据、目录及回退保护。"""

import re

import psycopg
import pytest

from tests.test_deployment_database import allocated_database, load_script
from tests.test_deployment_upgrade import data_snapshot, deployed12
from tests.test_return_summary_index import guard_definitions, install, revision_sql
from tests.test_schema_delivery import catalog

__all__ = ["allocated_database", "deployed12"]
PREVIOUS = "20261113_17"
HEAD = "20261114_18"


def test_upgrade18_changes_only_two_guards_and_preserves_data(allocated_database):
    from tests.test_aftersales_api import publish

    install(allocated_database, PREVIOUS)
    publish(allocated_database)
    with allocated_database.connect() as c:
        before = catalog(c)
        data = data_snapshot(c)
        guards = guard_definitions(c)
        c.connection.driver_connection.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
        after = catalog(c)
        assert {k: v for k, v in after.items() if k not in {"functions", "permissions"}} == {
            k: v for k, v in before.items() if k not in {"functions", "permissions"}
        }
        assert set(after["permissions"]) - set(before["permissions"]) == {
            ("alembic_version_v2", "version_num", "pdd_ingest", "SELECT")
        }
        assert not set(before["permissions"]) - set(after["permissions"])
        changed = {
            name
            for (name, old), (_, new) in zip(before["functions"], after["functions"], strict=True)
            if old != new
        }
        assert changed == {"pdd_retention_context", "pdd_retention_item_guard"}
        expected_guards = [
            (
                name,
                definition.replace(PREVIOUS, HEAD).replace(
                    "current_database() !~ '^pdd_operations_v2_test_[0-9a-f]{12}$'",
                    "NOT (current_database() ~ '^pdd_operations_v2_test_[0-9a-f]{12}$'"
                    " OR (current_database()='pdd_operations' AND session_user='pdd_admin'))",
                ),
            )
            for name, definition in guards
        ]
        assert guard_definitions(c) == expected_guards
        assert data_snapshot(c) == data
        c.connection.driver_connection.execute(
            revision_sql(HEAD, PREVIOUS, downgrade=True), prepare=False
        )
        assert guard_definitions(c) == guards
        assert catalog(c) == before
        assert data_snapshot(c) == data


@pytest.mark.parametrize("blocked_by", ["maintenance", "task_write", "running"])
def test_upgrade18_refuses_concurrent_maintenance_or_running_tasks(allocated_database, blocked_by):
    install(allocated_database, PREVIOUS)
    with allocated_database.connect() as c, allocated_database.connect() as blocker:
        if blocked_by == "running":
            c.exec_driver_sql("""INSERT INTO task_runs
                (id,job_code,request_ref,lock_key,status,queued_at,started_at,
                 worker_ref,lease_expires_at)
                VALUES ('running','sync_shop_daily','test','test','running',clock_timestamp(),
                        clock_timestamp(),'test',clock_timestamp()+interval '1 minute')""")
        with blocker.connection.driver_connection.transaction():
            if blocked_by == "maintenance":
                blocker.exec_driver_sql(
                    "SELECT pg_advisory_xact_lock(hashtext('pdd-database-maintenance'))"
                )
            if blocked_by == "task_write":
                blocker.exec_driver_sql("LOCK TABLE task_runs IN ROW EXCLUSIVE MODE")
            before = guard_definitions(c)
            with pytest.raises(
                psycopg.Error, match="LOCAL_STORAGE_MIGRATION_BUSY|could not obtain lock"
            ):
                c.connection.driver_connection.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
            c.connection.driver_connection.rollback()
            assert guard_definitions(c) == before
            assert (
                c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == PREVIOUS
            )


def test_controlled_upgrade17_preserves_company_objects_and_history(deployed12):
    from migrations_v2.company_db_objects import TARGET_REVISION
    from tests.test_aftersales_api import publish

    publish(deployed12)
    upgrade = load_script("local_storage_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        c.exec_driver_sql("SET ROLE pdd_app")
        body = re.sub(
            r"(?m)^GRANT [^;\n]+ TO pdd_(?:app|ingest);\n",
            "",
            revision_sql("20261104_12", PREVIOUS),
        )
        c.connection.driver_connection.execute(body, prepare=False)
        before = data_snapshot(c)
        assert upgrade.upgrade(c.connection.driver_connection, apply=False) == "ready"
        assert upgrade.upgrade(c.connection.driver_connection, apply=True) == "upgraded"
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == TARGET_REVISION
        )
        assert data_snapshot(c) == before
        assert upgrade.upgrade(c.connection.driver_connection, apply=True) == "already_upgraded"
        c.exec_driver_sql("RESET ROLE")
        assert c.exec_driver_sql("SELECT count(*) FROM aftersales_reports_rpa").scalar() == 1
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar() == "preserved"
        assert c.exec_driver_sql("SELECT value FROM extension_data").scalar() == "preserved"


def test_archive_head_transition_preserves_evidence_and_refuses_unsafe_downgrade(
    allocated_database, tmp_path, monkeypatch
):
    import os

    from app.modules import database_archive as archive
    from app.modules import database_retention as retention
    from tests.test_database_archive import old_candidate

    install(allocated_database, PREVIOUS)
    engine = allocated_database.execution_options(isolation_level="READ COMMITTED")
    old_candidate(engine)
    key = os.urandom(32)
    with monkeypatch.context() as previous:
        previous.setattr(archive, "HEAD", PREVIOUS)
        previous.setattr(retention, "HEAD", PREVIOUS)
        plan17 = retention.plan(engine)
        bundle17 = archive.export(engine, plan17, tmp_path / "head17", key)
        archive.drill(bundle17, key)
        assert retention.apply(engine, plan17, bundle17, key)["state"] == "offline"
        with allocated_database.connect() as c:
            guards = guard_definitions(c)
            with pytest.raises(
                psycopg.errors.RaiseException, match="LOCAL_STORAGE_RESTORE_REQUIRED"
            ):
                c.connection.driver_connection.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
            c.connection.driver_connection.rollback()
            assert guard_definitions(c) == guards
        assert retention.restore(engine, bundle17, key)["state"] == "online"
    with allocated_database.connect() as c:
        receipts17 = c.exec_driver_sql(
            "SELECT to_jsonb(a) FROM retention_archives a ORDER BY id"
        ).all()
        c.connection.driver_connection.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
        assert (
            c.exec_driver_sql("SELECT to_jsonb(a) FROM retention_archives a ORDER BY id").all()
            == receipts17
        )
    with pytest.raises(ValueError, match="ARCHIVE"):
        archive.verify(bundle17, key)
    selected = retention.plan(engine)
    bundle18 = archive.export(engine, selected, tmp_path / "head18", key)
    archive.drill(bundle18, key)
    assert retention.apply(engine, selected, bundle18, key)["state"] == "offline"
    for state in ("offline", "online"):
        with allocated_database.connect() as c:
            before = catalog(c)
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="LOCAL_STORAGE_RESTORE_REQUIRED|LOCAL_STORAGE_DOWNGRADE_EVIDENCE_EXISTS",
            ):
                c.connection.driver_connection.execute(
                    revision_sql(HEAD, PREVIOUS, downgrade=True), prepare=False
                )
            c.connection.driver_connection.rollback()
            assert catalog(c) == before
            assert c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == HEAD
        if state == "offline":
            assert retention.restore(engine, bundle18, key)["state"] == "online"


def test_database_guard_uses_real_database_and_session_identity():
    """无网络一次性PG同名合成库；不连接本机业务实例，也不伪造内置身份函数。"""
    import subprocess
    import time
    import uuid

    name = "pdd-storage-guard-" + uuid.uuid4().hex[:12]

    def docker(*args, input=None):
        return subprocess.run(
            ["docker", *args],
            input=input,
            text=True,
            capture_output=True,
            timeout=45,
        )

    def sql(database, user, statement, *, expected=None):
        result = docker(
            "exec",
            "-i",
            name,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
            input=statement,
        )
        if expected:
            assert result.returncode != 0 and expected in result.stderr, result.stderr
        else:
            assert result.returncode == 0, result.stderr
        return result

    created = docker(
        "run",
        "--detach",
        "--network",
        "none",
        "--name",
        name,
        "--tmpfs",
        "/var/lib/postgresql/data:rw",
        "--memory",
        "256m",
        "-e",
        "POSTGRES_USER=pdd_admin",
        "-e",
        "POSTGRES_DB=pdd_operations",
        "-e",
        "POSTGRES_HOST_AUTH_METHOD=trust",
        "postgres:17-bookworm",
    )
    assert created.returncode == 0, created.stderr
    try:
        for _ in range(150):
            if (
                docker(
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "pdd_admin",
                    "-d",
                    "pdd_operations",
                ).returncode
                == 0
            ):
                break
            time.sleep(0.1)
        else:
            pytest.fail("isolated PostgreSQL did not become ready")
        sql(
            "pdd_operations",
            "pdd_admin",
            """
            CREATE ROLE pdd_app LOGIN;
            CREATE ROLE pdd_ingest LOGIN;
            CREATE ROLE songbing LOGIN;
            GRANT pdd_admin TO pdd_ingest;
            CREATE DATABASE pdd_dashboard;
        """,
        )
        renderer = load_script("identity_guard_renderer", "render-db-schema.py")
        attempt = """INSERT INTO retention_operation_items
            (operation_id,version_id,table_name,row_count,content_sha256)
            VALUES ('missing','missing','product_daily_facts',0,repeat('0',64));"""
        for database in ("pdd_operations", "pdd_dashboard"):
            sql(database, "pdd_admin", renderer.render_sql(PREVIOUS))
            sql(
                database,
                "pdd_admin",
                "GRANT INSERT ON retention_operation_items TO pdd_ingest,songbing;",
            )
            sql(database, "pdd_admin", attempt, expected="RETENTION_PRODUCTION_DISABLED")
            sql(database, "pdd_admin", revision_sql(PREVIOUS, HEAD))
            sql(
                database,
                "pdd_admin",
                attempt,
                expected=(
                    "RETENTION_RECEIPT_REQUIRED"
                    if database == "pdd_operations"
                    else "RETENTION_PRODUCTION_DISABLED"
                ),
            )
            for user in ("pdd_ingest", "songbing"):
                sql(database, user, attempt, expected="RETENTION_PRODUCTION_DISABLED")
            sql(
                database,
                "pdd_ingest",
                "SET ROLE pdd_admin; " + attempt,
                expected="RETENTION_PRODUCTION_DISABLED",
            )
            sql(database, "pdd_admin", revision_sql(HEAD, PREVIOUS, downgrade=True))
            sql(database, "pdd_admin", attempt, expected="RETENTION_PRODUCTION_DISABLED")
    finally:
        removed = docker("rm", "--force", "--volumes", name)
        assert removed.returncode == 0, removed.stderr


def test_reviewed15_increment_remains_available_for_isolated_validation(allocated_database):
    from migrations_v2.company_db_objects import TARGET_REVISION, verify_application

    renderer = install(allocated_database, "20261111_15")
    with allocated_database.connect() as c:
        raw = c.connection.driver_connection
        with raw.transaction():
            raw.execute(renderer.render_upgrade_sql("20261111_15"), prepare=False)
        verify_application(raw, TARGET_REVISION, has_rpa=False)
