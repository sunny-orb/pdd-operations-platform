"""公司PG17现有库的12/16/17/18/19→20升级。默认只读核验，--apply执行固定增量。"""

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.pq import TransactionStatus
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "schema_renderer", ROOT / "scripts/render-db-schema.py"
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

from migrations_v2.company_db_objects import (
    TARGET_REVISION,
    UPGRADE_STARTS,
    application_acl,
    protected_catalog,
    rpa_manifest,
    verify_application,
    verify_company_protections,
)

HOST = "pg.kktree.cn"
DATABASE = renderer.DEPLOY_DATABASE
ACCOUNT = "songbing"


def connection_parameters(value: str) -> dict:
    """CLI只接受指定公司连接，拒绝额外libpq查询参数及路由覆盖。"""
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or (url.username, url.host, url.database) != (ACCOUNT, HOST, DATABASE)
        or url.port not in (None, 5432)
        or dict(url.query) != {"sslmode": "require"}
    ):
        raise ValueError("DEPLOY_CONNECTION_INVALID")
    return {
        "host": HOST,
        "port": 5432,
        "dbname": DATABASE,
        "user": ACCOUNT,
        "password": url.password,
        "sslmode": "require",
        "gssencmode": "disable",
        "connect_timeout": 10,
    }


def target_rpa_owner(connection) -> str:
    """同时验证实际库、登录身份、执行身份和PG版本；隔离测试无CLI入口。"""
    database, account, login, version = connection.execute(
        "SELECT current_database(),current_user,session_user,"
        "current_setting('server_version_num')::int"
    ).fetchone()
    endpoint = (connection.info.host, connection.info.port, database, account, login)
    if version // 10000 == 17:
        if (
            endpoint == (HOST, 5432, DATABASE, ACCOUNT, ACCOUNT)
            and connection.pgconn.ssl_in_use
        ):
            return "dbrole_admin"
        if (
            endpoint[:2] == ("127.0.0.1", 55432)
            and endpoint[3:] == ("pdd_app", "pdd_admin")
            and re.fullmatch(r"pdd_operations_v2_test_[0-9a-f]{12}", database)
        ):
            return "pdd_admin"
    raise ValueError("DEPLOY_TARGET_MISMATCH")


def prepare_transaction(connection, *, read_only):
    if read_only:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    connection.execute("SET LOCAL lock_timeout='5s'")
    connection.execute("SET LOCAL statement_timeout='60s'")
    connection.execute("SET LOCAL search_path=pg_catalog,public")
    connection.execute("SET LOCAL TimeZone='UTC'")


def check_revision(connection):
    if not connection.execute(
        "SELECT to_regclass('public.alembic_version_v2')"
    ).fetchone()[0]:
        raise ValueError("DEPLOY_REVISION_MISMATCH")
    versions = connection.execute(
        "SELECT version_num FROM public.alembic_version_v2"
    ).fetchall()
    if len(versions) != 1 or versions[0][0] not in (*UPGRADE_STARTS, TARGET_REVISION):
        raise ValueError("DEPLOY_REVISION_MISMATCH")
    return versions[0][0]


def verify_objects(connection, revision, owner):
    manifest = rpa_manifest(connection, owner=owner)
    if owner == "dbrole_admin":
        if manifest is None:
            raise ValueError("DEPLOY_RPA_MISMATCH")
        verify_company_protections(connection)
    verify_application(connection, revision, has_rpa=manifest is not None)
    return manifest


def require_idle_tasks(connection):
    running = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM public.task_runs WHERE status='running')"
    ).fetchone()[0]
    if running:
        raise ValueError("DEPLOY_UPGRADE_RUNNING_TASKS")


def acquire_locks(connection):
    # 沿保留维护的写入口锁顺序，最后取得初始化使用的独立锁。
    from app.modules.sales.datasets import SPECS

    for key in (
        "project-source-sync",
        "m1-product-board",
        "m5-source-report",
        "nmc-weather-publication",
        *sorted({"m3-" + code for code in ("shop_daily", "sales_targets", *SPECS)}),
        "pdd-database-maintenance",
    ):
        if not connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))", (key,)
        ).fetchone()[0]:
            raise ValueError("DEPLOY_UPGRADE_BUSY")
    if not connection.execute(
        "SELECT pg_try_advisory_xact_lock(726411,20261104)"
    ).fetchone()[0]:
        raise ValueError("DEPLOY_UPGRADE_BUSY")


def publication_snapshot(connection):
    """只核对发布指针和历史关系的内容，避免迁移中全量扫描经营事实。"""
    return connection.execute("""
        SELECT
          (SELECT jsonb_agg(to_jsonb(d) ORDER BY dataset_code) FROM public.current_datasets d),
          (SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM public.aftersales_reports r),
          (SELECT jsonb_agg(to_jsonb(i) ORDER BY output_version_id,input_role)
           FROM public.version_inputs i)
        """).fetchone()


def upgrade(connection, *, apply=False) -> str:
    """在专用空闲连接先只读验证；写入时在同一事务重验、迁移、复核。"""
    if (
        not connection.autocommit
        or connection.info.transaction_status != TransactionStatus.IDLE
    ):
        raise ValueError("DEPLOY_CONNECTION_NOT_IDLE")
    with connection.transaction():
        prepare_transaction(connection, read_only=True)
        owner = target_rpa_owner(connection)
        revision = check_revision(connection)
        verify_objects(connection, revision, owner)
        if revision == TARGET_REVISION:
            return "already_upgraded"
        require_idle_tasks(connection)
    if not apply:
        return "ready"
    sql = renderer.render_upgrade_sql(revision)
    with connection.transaction():
        prepare_transaction(connection, read_only=False)
        if target_rpa_owner(connection) != owner:
            raise ValueError("DEPLOY_TARGET_MISMATCH")
        acquire_locks(connection)
        if check_revision(connection) != revision:
            raise ValueError("DEPLOY_REVISION_MISMATCH")
        before_rpa = verify_objects(connection, revision, owner)
        connection.execute("""
            LOCK TABLE public.alembic_version_v2, public.task_runs, public.dataset_versions,
                public.current_datasets, public.aftersales_reports, public.version_inputs
            IN SHARE MODE NOWAIT
            """)
        require_idle_tasks(connection)
        has_rpa = before_rpa is not None
        before_acl = application_acl(connection, has_rpa=has_rpa)
        before_protected = protected_catalog(connection)
        before_publications = publication_snapshot(connection)
        connection.execute(sql, prepare=False)
        if check_revision(connection) != TARGET_REVISION:
            raise ValueError("DEPLOY_REVISION_MISMATCH")
        if verify_objects(connection, TARGET_REVISION, owner) != before_rpa:
            raise ValueError("DEPLOY_RPA_CHANGED")
        if protected_catalog(connection) != before_protected:
            raise ValueError("DEPLOY_COMPANY_OBJECTS_CHANGED")
        after_acl = application_acl(connection, has_rpa=has_rpa)
        if any(
            key not in after_acl or after_acl[key] != value
            for key, value in before_acl.items()
        ):
            raise ValueError("DEPLOY_ACL_CHANGED")
        if publication_snapshot(connection) != before_publications:
            raise ValueError("DEPLOY_PUBLICATIONS_CHANGED")
    return "upgraded"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if any(key in os.environ for key in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS")):
            raise ValueError("DEPLOY_CONNECTION_INVALID")
        params = connection_parameters(os.environ["DATABASE_URL"])
        with psycopg.connect(**params, autocommit=True) as connection:
            status = upgrade(connection, apply=args.apply)
        print(json.dumps({"status": status, "target": TARGET_REVISION}))
        return 0
    except Exception as exc:  # noqa: BLE001 - 只记录受控错误码，不输出连接或数据库异常正文
        error = (
            str(exc)
            if type(exc) in (ValueError, RuntimeError)
            and re.fullmatch(r"DEPLOY_[A-Z_]+(?::[a-z_,]+)?", str(exc))
            else type(exc).__name__
        )
        print(json.dumps({"status": "failed", "error": error}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
