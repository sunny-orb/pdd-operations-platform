"""公司数据库独立维护入口；默认只检查，--apply才初始化应用空结构。"""

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "schema_renderer", ROOT / "scripts/render-db-schema.py"
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

from migrations_v2.company_db_objects import (
    TARGET_REVISION,
    rpa_manifest,
    verify_application,
)


def initialize(connection, *, database: str, account: str, apply: bool = False) -> str:
    """校验实际目标；仅初始化应用空结构，monitor和扩展保持原样，重复调用不重放DDL。"""
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='5s'")
        connection.execute("SET LOCAL statement_timeout='60s'")
        actual = connection.execute(
            "SELECT current_database(),current_user,current_setting('server_version_num')::int"
        ).fetchone()
        if actual[:2] != (database, account) or actual[2] // 10000 != 17:
            raise ValueError("DEPLOY_TARGET_MISMATCH")
        if apply:
            connection.execute("SELECT pg_advisory_xact_lock(726411,20261104)")
        else:
            connection.execute("SET TRANSACTION READ ONLY")
        exists = connection.execute(
            "SELECT to_regclass('public.alembic_version_v2')"
        ).fetchone()[0]
        if exists:
            head = TARGET_REVISION
            versions = connection.execute(
                "SELECT version_num FROM public.alembic_version_v2"
            ).fetchall()
            if versions != [(head,)]:
                raise ValueError("DEPLOY_REVISION_MISMATCH")
            connection.execute("SET LOCAL search_path=pg_catalog,public")
            owner = (
                "pdd_admin"
                if account == "pdd_app"
                and re.fullmatch(r"pdd_operations_v2_test_[0-9a-f]{12}", database)
                else "dbrole_admin"
            )
            manifest = rpa_manifest(connection, owner=owner)
            verify_application(connection, head, has_rpa=manifest is not None)
            return "already_initialized"
        # 与派生SQL共用前置检查；只读检查不申请事务写锁。
        preflight = renderer.deployment_preflight(account, database)
        if not apply:
            preflight = preflight.replace(
                "  PERFORM pg_advisory_xact_lock(726411, 20261104);\n", ""
            )
        connection.execute(preflight, prepare=False)
        if not apply:
            return "empty"
        sql = renderer.render_sql(TARGET_REVISION, runtime_account=account, database=database)
        body = renderer.transaction_body(sql[sql.index("BEGIN;") :].strip())
        connection.execute(body, prepare=False)
        return "initialized"


def connection_parameters(value: str) -> dict:
    """解析公司维护连接；只允许迁移前后两项目库，拒绝错误身份与驱动。"""
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.database not in ("pdd_operations", "pdd_dashboard")
        or url.username != "songbing"
    ):
        raise ValueError("DEPLOY_CONNECTION_INVALID")
    params = dict(url.query)
    params.update(
        host=url.host,
        port=url.port or 5432,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=10,
    )
    return params


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        params = connection_parameters(os.environ["DATABASE_URL"])
        with psycopg.connect(**params, autocommit=True) as connection:
            status = initialize(
                connection,
                database=params["dbname"],
                account="songbing",
                apply=args.apply,
            )
        print(json.dumps({"status": status}))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI仅输出异常类型，防止泄漏凭据
        # 不打印连接串、SQL参数或数据库异常正文。
        print(
            json.dumps({"status": "failed", "error": type(exc).__name__}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
