"""受控本机08→09日期索引升级/09→08回退，不改经营行、发布版本或权限。"""

import argparse
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url

OLD, NEW = "20261028_08", "20261031_09"
INDEXES = {
    "ix_product_promotion_records_version_period_start": "product_promotion_records",
    "ix_product_region_records_version_period_start": "product_region_records",
}


def runner():
    spec = importlib.util.spec_from_file_location(
        "v2_index_entry", ROOT / "scripts/db-v2.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(connection, revision):
    """检查精确版本、两条非唯一索引；不读经营内容。"""
    actual_revision = connection.exec_driver_sql(
        "SELECT version_num FROM public.alembic_version_v2"
    ).scalar_one()
    if actual_revision != revision:
        raise RuntimeError("项目revision不符合本入口边界")
    indexes = dict(
        connection.execute(
            sa.text(
                "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND indexname=ANY(:names)"
            ),
            {"names": list(INDEXES)},
        ).all()
    )
    expected = (
        {
            name: f"CREATE INDEX {name} ON public.{table} USING btree (dataset_version_id, period_start)"
            for name, table in INDEXES.items()
        }
        if revision == NEW
        else {}
    )
    if indexes != expected:
        raise RuntimeError("日期查询索引定义不符")
    invalid = connection.execute(
        sa.text(
            "SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid=i.indexrelid "
            "WHERE t.relname=ANY(:names) AND (NOT i.indisvalid OR NOT i.indisready)"
        ),
        {"names": list(INDEXES)},
    ).scalar_one()
    if invalid:
        raise RuntimeError("日期查询索引未完成或无效")


def permission_snapshot(connection):
    """保留既有ACL；确认运行角色没有DDL能力，不在索引迁移修正既有TEMP权限。"""
    for role in ("pdd_app", "pdd_ingest"):
        unsafe = connection.execute(
            sa.text(
                "SELECT has_database_privilege(:r,current_database(),'CREATE') OR "
                "has_schema_privilege(:r,'public','CREATE')"
            ),
            {"r": role},
        ).scalar_one()
        if unsafe:
            raise RuntimeError("运行角色具有DDL权限")
    return connection.execute(
        sa.text(
            "SELECT c.relname,c.relacl::text,a.attname,a.attacl::text "
            "FROM pg_class c LEFT JOIN pg_attribute a ON a.attrelid=c.oid "
            "AND a.attnum>0 AND NOT a.attisdropped WHERE c.relnamespace='public'::regnamespace "
            "AND c.relkind='r' ORDER BY c.relname,a.attnum"
        )
    ).all()


def main(action="upgrade"):
    """绑定本次完整隔离验证、schema备份、同步锁和事务；新连接再核验。"""
    module = runner()
    evidence = json.loads(module.EVIDENCE.read_text())
    if (
        evidence["result"] != "passed"
        or evidence["fingerprint"] != module.fingerprint()
    ):
        raise RuntimeError("须先运行与当前文件一致的db-v2 verify")
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    if ScriptDirectory.from_config(config).get_heads() != [NEW]:
        raise RuntimeError("本入口只接受09迁移head")
    start, target = (OLD, NEW) if action == "upgrade" else (NEW, OLD)
    engine = sa.create_engine(
        local_url("pdd_operations"),
        hide_parameters=True,
        connect_args={"options": "-c lock_timeout=10000 -c statement_timeout=120000"},
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        with engine.begin() as c:
            locked = c.execute(
                sa.text(
                    "SELECT pg_try_advisory_xact_lock(hashtext('project-source-sync'))"
                )
            ).scalar_one()
            if not locked:
                raise RuntimeError("项目同步正在运行，稍后重试")
            c.execute(
                sa.text(
                    "SELECT pg_advisory_xact_lock(hashtextextended('pdd-v2-schema', 0))"
                )
            )
            before = module.snapshot(c)
            actual = c.exec_driver_sql(
                "SELECT version_num FROM alembic_version_v2"
            ).scalar_one()
            if actual == target:
                verify(c, target)
                print(f"项目已是{target}且索引定义有效；未重复变更")
                return
            verify(c, start)
            module.inspect_roles(c)
            acl_before = permission_snapshot(c)
            backup = module.PRIVATE / (stamp + "-before-query-indexes.sql")
            module.private_write(backup, module.schema_backup(before["cluster"]))
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("隔离验证后文件变化")
            config.attributes["connection"] = c
            config.attributes["v2_expected_database"] = "pdd_operations"
            began = time.perf_counter()
            (command.upgrade if action == "upgrade" else command.downgrade)(
                config, target
            )
            elapsed = round(time.perf_counter() - began, 3)
            verify(c, target)
            if permission_snapshot(c) != acl_before:
                raise RuntimeError("索引迁移意外改变权限，回滚")
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("迁移期间文件变化，回滚")
        with engine.connect() as c:
            verify(c, target)
            after = module.snapshot(c)
            sizes = dict(
                c.execute(
                    sa.text(
                        "SELECT relname,pg_relation_size(oid) FROM pg_class WHERE relname=ANY(:names)"
                    ),
                    {"names": list(INDEXES)},
                ).all()
            )
        module.private_write(
            module.PRIVATE / (stamp + "-query-indexes-" + action + ".json"),
            json.dumps(
                {
                    "revision": target,
                    "action": action,
                    "elapsed_seconds": elapsed,
                    "fingerprint": evidence["fingerprint"],
                    "backup": str(backup),
                    "verification_log": evidence["test_log"],
                    "before": before,
                    "after": after,
                    "index_bytes": sizes,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        print(f"本机{target}已提交并独立核验；DDL耗时{elapsed}秒；索引字节数{sizes}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("upgrade", "downgrade"), nargs="?", default="upgrade"
    )
    args = parser.parse_args()
    try:
        main(args.action)
    except (SQLAlchemyError, OSError, ValueError, KeyError, RuntimeError) as exc:
        # 不泄漏连接地址、凭据或SQL参数；同步繁忙只提示重试。
        print(
            f"索引操作失败：{type(exc).__name__}；请检查隔离验证、同步状态与revision",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
