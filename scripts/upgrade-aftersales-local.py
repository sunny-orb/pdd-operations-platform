"""仅本机05→06售后运行权限：隔离验证/同集群备份后升级，不导入业务数据。"""

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
            if revision == "20261028_06":
                print("售后运行权限06已应用；未重复变更")
                return
            if revision != "20261028_05":
                raise RuntimeError("受控入口只允许05→06")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = module.PRIVATE / (stamp + "-before-aftersales-permissions.sql")
        module.private_write(backup, module.schema_backup(before["cluster"]))
        with engine.begin() as c:
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("验证后迁移内容已变化")
            migrate(c)
            if (
                c.execute(
                    sa.text("SELECT version_num FROM alembic_version_v2")
                ).scalar_one()
                != "20261028_06"
            ):
                raise RuntimeError("权限revision核对失败")
            for table in (
                "return_records",
                "product_sales_records",
                "aftersales_reports",
            ):
                if not c.execute(
                    sa.text("SELECT has_table_privilege('pdd_ingest',:t,'INSERT')"),
                    {"t": table},
                ).scalar_one():
                    raise RuntimeError("必要INSERT权限未生效")
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    if c.execute(
                        sa.text("SELECT has_table_privilege('pdd_ingest',:t,:p)"),
                        {"t": table, "p": privilege},
                    ).scalar_one():
                        raise RuntimeError("不允许扩大原行或报告写权限")
        module.private_write(
            module.PRIVATE / (stamp + "-aftersales-permissions.json"),
            json.dumps(
                {"revision": "20261028_06", "before": before, "backup": str(backup)},
                ensure_ascii=False,
            ),
        )
        print("本机售后最小运行权限06已应用；未改字段或业务数据")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
