"""离线生成现行v2完整空库初始化SQL；不读凭据、不连接数据库。"""

import argparse
import hashlib
import io
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/api"
sys.path.insert(0, str(API))

OUTPUT = ROOT / "docs/database-schema/schema.sql"
DEPLOY_OUTPUT = ROOT / "docs/database-schema/schema-songbing.sql"


from migrations_v2.company_db_objects import (
    EXTENSION_MEMBERS,
    START_REVISION,
    TARGET_REVISION,
)

DEPLOY_DATABASE = "pdd_dashboard"
# 精确离线增量：公司CLI允许12/16/17→18，15仅用于隔离副本验证。
UPGRADE_SQL_SHA256 = {
    "20261104_12": "3896140dacd1ced70611877d4f758a07110f54b6f9340c5e3d82af10bace7b39",
    "20261111_15": "a2fa717e3f8ff82929945a099dab29abe23743b4d5fba3bc20222e1369939104",
    "20261111_16": "593974ad7961f70881af10958a8854f7605bf5b6bc5f4be31eeaca28cfccff24",
    "20261113_17": "d70ff79fe83b0962bc94b2d8a5f4551a795a760bda2981b5f1179b2a53fc1e46",
}


def transaction_body(sql: str) -> str:
    """只接受Alembic单事务外壳；实际边界交给调用连接。"""
    if not sql.startswith("BEGIN;") or not sql.endswith("COMMIT;"):
        raise RuntimeError("迁移输出事务边界变化，须先核对生成器")
    return sql[len("BEGIN;") : -len("COMMIT;")]


def render_upgrade_sql(start_revision: str = START_REVISION) -> str:
    """只生成12/15/16/17→18精确增量；起点与正文均校验。"""
    if start_revision not in UPGRADE_SQL_SHA256:
        raise RuntimeError("DEPLOY_MIGRATION_START_INVALID")
    output = io.StringIO()
    config = Config(str(API / "alembic-v2.ini"), output_buffer=output)
    scripts = ScriptDirectory.from_config(config)
    # 公司已批准增量仍止于18；后续本地head不能自动扩大线上目标。
    if TARGET_REVISION not in {
        revision.revision for revision in scripts.iterate_revisions("head", "base")
    }:
        raise RuntimeError("DEPLOY_MIGRATION_HEAD_CHANGED")
    command.upgrade(config, f"{start_revision}:{TARGET_REVISION}", sql=True)
    body = transaction_body(output.getvalue().strip())
    if hashlib.sha256(body.encode()).hexdigest() != UPGRADE_SQL_SHA256[start_revision]:
        raise RuntimeError("DEPLOY_MIGRATION_SQL_CHANGED")
    body, count = re.subn(
        r"(?m)^GRANT (?:SELECT|SELECT\(version_num\)|INSERT|UPDATE\(state\)|SELECT,INSERT,UPDATE) "
        r"ON public\.[a-z_][a-z0-9_]* TO pdd_(?:app|ingest);\n",
        "",
        body,
    )
    expected_grants = 48 if start_revision == START_REVISION else 1
    if count != expected_grants or re.search(r"\bpdd_(?:app|ingest)\b", body):
        raise RuntimeError("DEPLOY_MIGRATION_PERMISSIONS_CHANGED")
    return body


def deployment_preflight(account: str, database: str = "pdd_operations") -> str:
    """单账号仅检查并初始化public；保留公司monitor及数据库级权限。"""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", account):
        raise ValueError("DEPLOY_ACCOUNT_INVALID")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database):
        raise ValueError("DEPLOY_DATABASE_INVALID")
    return f"""DO $preflight$ BEGIN
  IF current_database() <> '{database}' THEN
    RAISE EXCEPTION '初始化数据库不符';
  END IF;
  IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
    RAISE EXCEPTION '初始化文件要求PostgreSQL 17';
  END IF;
  IF current_user <> '{account}' THEN
    RAISE EXCEPTION '初始化身份不符';
  END IF;
  PERFORM pg_advisory_xact_lock(726411, 20261104);
  IF EXISTS(
    {EXTENSION_MEMBERS}
    SELECT 1 FROM (
      SELECT 'pg_catalog.pg_class'::regclass AS classid,oid,relnamespace AS namespace
      FROM pg_catalog.pg_class
      UNION ALL
      SELECT 'pg_catalog.pg_proc'::regclass,oid,pronamespace FROM pg_catalog.pg_proc
      UNION ALL
      SELECT 'pg_catalog.pg_type'::regclass,oid,typnamespace FROM pg_catalog.pg_type
    ) o JOIN pg_catalog.pg_namespace n ON n.oid=o.namespace
    WHERE n.nspname NOT IN ('information_schema', 'monitor') AND n.nspname !~ '^pg_'
      AND NOT EXISTS (SELECT 1 FROM extension_members e
                      WHERE e.classid=o.classid AND e.objid=o.oid AND e.objsubid=0)
  ) THEN
    RAISE EXCEPTION '仅允许应用空结构初始化；保留monitor，不重放已有应用库';
  END IF;
  IF NOT has_schema_privilege(current_user, 'public', 'USAGE')
     OR NOT has_schema_privilege(current_user, 'public', 'CREATE') THEN
    RAISE EXCEPTION 'public创建权限不足';
  END IF;
END $preflight$;
"""


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


def render_sql(
    target: str = "head",
    *,
    runtime_account: str | None = None,
    database: str = "pdd_operations",
) -> str:
    """完整迁移有序渲染；head或单账号明确历史目标均附空库保护。"""
    if runtime_account is not None and target not in ("head", TARGET_REVISION):
        raise ValueError("单账号交付只允许base到head或已批准公司目标初始化")
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
            API / "migrations_v2/company_db_objects.py",
        ]
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    command.upgrade(config, target, sql=True)
    sql = output.getvalue().strip()
    if target == "head" or runtime_account is not None:
        # Alembic单事务离线输出：在同一事务内加前置保护及部署运行权限。
        body = transaction_body(sql)
        if runtime_account is None:
            sql = "BEGIN;\n\n" + PREFLIGHT + body
            sql += runtime_permissions() + "\n\nCOMMIT;"
        else:
            # 基线的public关系检查同样区分扩展；精确匹配旧片段，变化时拒绝生成。
            # 保留检查本身及同名pdd_v2函数保护，不改写历史revision。
            baseline_relations = """SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f')
          AND c.relname <> 'alembic_version_v2'"""
            if body.count(baseline_relations) != 1:
                raise RuntimeError("基线空结构检查变化，须先核对扩展适配")
            body = body.replace(
                baseline_relations,
                EXTENSION_MEMBERS
                + baseline_relations
                + " AND NOT EXISTS (SELECT 1 FROM extension_members e "
                "WHERE e.classid='pg_catalog.pg_class'::regclass "
                "AND e.objid=c.oid AND e.objsubid=0)",
            )
            # 仅移除Alembic离线输出的完整单行角色GRANT；历史revision不改写。
            # 若未来授权格式变化，拒绝生成，防止静默遗漏角色依赖。
            body = re.sub(r"(?m)^GRANT [^;\n]+ TO pdd_(?:app|ingest);\n", "", body)
            if re.search(r"\bpdd_(?:app|ingest)\b", body):
                raise RuntimeError("部署授权输出变化，须先核对生成器")
            sql = (
                "BEGIN;\n\n"
                + deployment_preflight(runtime_account, database)
                + body
                + "\nCOMMIT;"
            )
    usage = (
        "-- 仅用于PostgreSQL17空库；先准备pdd_app/pdd_ingest角色和目标库。\n"
        "-- 使用数据库所有者执行；不含凭据/业务数据；已有库禁止重放。\n"
        if runtime_account is None
        else f"-- 公司单账号：{runtime_account}/{database}；应用结构须空；保留monitor及登记的扩展成员。\n"
        "-- 不创建角色、不修改数据库或monitor权限；应用对象由执行账号拥有。\n"
    )
    header = (
        "-- 现行v2完整空库初始化文件；由scripts/render-db-schema.py生成，请勿手改。\n"
        f"-- 迁移范围：base → {head if target == 'head' else target}；包含Alembic版本表。\n"
        f"-- 迁移、冻结实现及生成器SHA-256：{digest.hexdigest()}\n"
        + usage
        + "-- 使用方式和后续升级见docs/database-schema/migration-plan.md。\n\n"
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
    outputs = {
        OUTPUT: render_sql(),
        DEPLOY_OUTPUT: render_sql(runtime_account="songbing", database=DEPLOY_DATABASE),
    }
    if args.check:
        if any(
            not path.exists() or path.read_text(encoding="utf-8") != sql
            for path, sql in outputs.items()
        ):
            raise SystemExit(
                "schema.sql缺失或未覆盖当前迁移，请运行scripts/render-db-schema.py"
            )
        print("schema.sql与现行完整迁移一致；未连接或操作数据库")
    else:
        for path, sql in outputs.items():
            path.write_text(sql, encoding="utf-8")
        print("已离线生成本机与songbing初始化SQL；未连接或操作数据库")
