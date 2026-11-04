"""离线生成现行v2完整空库初始化SQL；不读凭据、不连接数据库。"""

import argparse
import hashlib
import io
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/api"
sys.path.insert(0, str(API))

OUTPUT = ROOT / "docs/database-schema/schema.sql"


def runtime_permissions() -> str:
    """沿原本地建库入口的最小权限；迁移链负责各域任务写权限。"""
    from migrations_v2.baseline_20261028.schema import build_metadata

    return "\n".join(
        [
            "DO $permissions$ BEGIN",
            "  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());",
            (
                "  EXECUTE format('GRANT CONNECT ON DATABASE %I TO pdd_app,pdd_ingest', "
                "current_database());"
            ),
            "END $permissions$;",
            "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
            "GRANT USAGE ON SCHEMA public TO pdd_app,pdd_ingest;",
            *(
                f"GRANT SELECT ON public.{table.name} TO pdd_app;"
                for table in build_metadata().sorted_tables
            ),
        ]
    )


PREFLIGHT = """DO $preflight$ BEGIN
  IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
    RAISE EXCEPTION '初始化文件要求PostgreSQL 17';
  END IF;
  IF EXISTS(
    SELECT 1 FROM pg_namespace n
    WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
      AND (EXISTS(SELECT 1 FROM pg_class WHERE relnamespace=n.oid)
        OR EXISTS(SELECT 1 FROM pg_proc WHERE pronamespace=n.oid)
        OR EXISTS(SELECT 1 FROM pg_type WHERE typnamespace=n.oid))
  ) THEN
    RAISE EXCEPTION '仅允许空库初始化；已有库须按Alembic当前版本增量升级';
  END IF;
  IF (SELECT count(*) FROM pg_roles WHERE rolname IN ('pdd_app','pdd_ingest')
      AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) <> 2
     OR EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
               WHERE r.rolname IN ('pdd_app','pdd_ingest')) THEN
    RAISE EXCEPTION '须先准备独立最小权限角色pdd_app和pdd_ingest';
  END IF;
END $preflight$;
"""


def render_sql(target: str = "head") -> str:
    """完整迁移有序渲染并绑定内容摘要；head附空库保护和原运行权限。"""
    output = io.StringIO()
    config = Config(str(API / "alembic-v2.ini"), output_buffer=output)
    scripts = ScriptDirectory.from_config(config)
    head = scripts.get_current_head()
    files = (
        sorted((API / "migrations_v2/versions").glob("*.py"))
        + sorted((API / "migrations_v2/baseline_20261028").glob("*.py"))
        + [
            API / "migrations_v2/env.py",
            API / "alembic-v2.ini",
            Path(__file__).resolve(),
        ]
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    command.upgrade(config, target, sql=True)
    sql = output.getvalue().strip()
    if target == "head":
        # Alembic单事务离线输出：在同一事务内加前置保护及部署运行权限。
        if not sql.startswith("BEGIN;") or not sql.endswith("COMMIT;"):
            raise RuntimeError("迁移输出事务边界变化，须先核对生成器")
        sql = "BEGIN;\n\n" + PREFLIGHT + sql[len("BEGIN;") : -len("COMMIT;")]
        sql += runtime_permissions() + "\n\nCOMMIT;"
    header = (
        "-- 现行v2完整空库初始化文件；由scripts/render-db-schema.py生成，请勿手改。\n"
        f"-- 迁移范围：base → {head if target == 'head' else target}；包含Alembic版本表。\n"
        f"-- 迁移、冻结实现及生成器SHA-256：{digest.hexdigest()}\n"
        "-- 仅用于PostgreSQL17空库；先准备pdd_app/pdd_ingest角色和目标库。\n"
        "-- 使用数据库所有者执行；不含凭据/业务数据；已有库禁止重放。\n"
        "-- 使用方式和后续升级见docs/database-schema/migration-plan.md。\n\n"
    )
    return (
        "\n".join(line.rstrip() for line in (header + sql).splitlines()).rstrip() + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="检查完整SQL与当前迁移链一致"
    )
    args = parser.parse_args()
    sql = render_sql()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != sql:
            raise SystemExit(
                "schema.sql缺失或未覆盖当前迁移，请运行scripts/render-db-schema.py"
            )
        print("schema.sql与现行完整迁移一致；未连接或操作数据库")
    else:
        OUTPUT.write_text(sql, encoding="utf-8")
        print(f"已离线生成 {OUTPUT.relative_to(ROOT)}；未连接或操作数据库")
