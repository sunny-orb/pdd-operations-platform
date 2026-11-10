"""公司数据库独立维护入口；默认只检查，--apply才初始化应用空结构。"""

import argparse
import importlib.util
import json
import os
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
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            head = ScriptDirectory.from_config(
                Config(str(ROOT / "apps/api/alembic-v2.ini"))
            ).get_current_head()
            versions = connection.execute(
                "SELECT version_num FROM public.alembic_version_v2"
            ).fetchall()
            if versions != [(head,)]:
                raise ValueError("DEPLOY_REVISION_MISMATCH")
            # 版本标记不足以证明结构完整；验证关键目录以及运行账号对象归属。
            tables, columns, owned = connection.execute(
                renderer.EXTENSION_MEMBERS
                + "SELECT count(DISTINCT c.oid), count(a.attnum), bool_and(c.relowner=current_user::regrole) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
                "WHERE n.nspname='public' AND c.relkind='r' AND c.relname <> 'alembic_version_v2' "
                "AND NOT EXISTS (SELECT 1 FROM extension_members e "
                "WHERE e.classid='pg_catalog.pg_class'::regclass AND e.objid=c.oid AND e.objsubid=0)"
            ).fetchone()
            if (tables, columns, owned) != (46, 718, True):
                raise ValueError("DEPLOY_SCHEMA_MISMATCH")
            views = connection.execute(
                "SELECT c.relname,count(a.attnum),bool_and(c.relowner=current_user::regrole) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
                "WHERE n.nspname='public' AND c.relkind='v' "
                "AND c.relname=ANY(%s) GROUP BY c.relname ORDER BY c.relname",
                (
                    [
                        "read_product_daily_facts",
                        "read_product_promotion_records",
                        "read_product_region_records",
                        "read_product_sales_records",
                        "read_return_records",
                    ],
                ),
            ).fetchall()
            if views != [
                ("read_product_daily_facts", 27, True),
                ("read_product_promotion_records", 38, True),
                ("read_product_region_records", 27, True),
                ("read_product_sales_records", 23, True),
                ("read_return_records", 27, True),
            ]:
                raise ValueError("DEPLOY_SCHEMA_MISMATCH")
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
        sql = renderer.render_sql(runtime_account=account, database=database)
        body = sql[sql.index("BEGIN;") + len("BEGIN;") : sql.rindex("COMMIT;")]
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
