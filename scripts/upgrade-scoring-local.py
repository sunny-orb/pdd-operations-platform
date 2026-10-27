"""仅本机04→05评分列权限：隔离验证/同集群备份后升级，不导入业务数据。"""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url, migrate


def main():
    spec = importlib.util.spec_from_file_location(
        "v2_local_entry", ROOT / "scripts/db-v2.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = json.loads(module.EVIDENCE.read_text())
    if (
        evidence["fingerprint"] != module.fingerprint()
        or evidence["result"] != "passed"
    ):
        raise RuntimeError("须先运行独立v2隔离验证")
    engine = sa.create_engine(local_url("pdd_operations"), hide_parameters=True)
    try:
        with engine.connect() as c:
            before = module.snapshot(c)
            revision = c.execute(
                sa.text("SELECT version_num FROM alembic_version_v2")
            ).scalar_one()
            if revision == "20261028_05":
                print("评分列权限05已应用；未重复变更")
                return
            if revision != "20261028_04":
                raise RuntimeError("受控入口只允许04→05")
            grants = c.execute(
                sa.text(
                    "SELECT table_name,column_name FROM information_schema.column_privileges WHERE grantee='pdd_ingest' AND table_schema='public' AND table_name IN ('score_standards','product_scores') AND privilege_type='UPDATE'"
                )
            ).all()
            if grants:
                raise RuntimeError("发现基线外UPDATE列权限，拒绝覆盖")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = module.PRIVATE / (stamp + "-before-scoring-permissions.sql")
        module.private_write(backup, module.schema_backup(before["cluster"]))
        with engine.begin() as c:
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("验证后迁移内容已变化")
            migrate(c)
            if (
                c.execute(
                    sa.text("SELECT version_num FROM alembic_version_v2")
                ).scalar_one()
                != "20261028_05"
            ):
                raise RuntimeError("权限revision核对失败")
            for table in ("score_standards", "product_scores"):
                if not c.execute(
                    sa.text(
                        "SELECT has_column_privilege('pdd_ingest',:t,'id','UPDATE')"
                    ),
                    {"t": table},
                ).scalar_one():
                    raise RuntimeError("最小列权限未生效")
                if c.execute(
                    sa.text("SELECT has_table_privilege('pdd_ingest',:t,'UPDATE')"),
                    {"t": table},
                ).scalar_one():
                    raise RuntimeError("不允许扩大为表级UPDATE")
        module.private_write(
            module.PRIVATE / (stamp + "-scoring-permissions.json"),
            json.dumps(
                {"revision": "20261028_05", "before": before, "backup": str(backup)},
                ensure_ascii=False,
            ),
        )
        print("本机评分最小列权限05已应用；未改字段或业务数据")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
