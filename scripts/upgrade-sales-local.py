"""仅应用本机M3权限revision；验证摘要匹配且留存同集群结构备份后执行。"""

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
        raise RuntimeError("须先重新运行独立v2隔离验证")
    engine = sa.create_engine(local_url("pdd_operations"), hide_parameters=True)
    try:
        with engine.connect() as c:
            before = module.snapshot(c)
            revision = c.execute(
                sa.text("SELECT version_num FROM alembic_version_v2")
            ).scalar_one()
            if revision == "20261028_02":
                print("M3权限revision已应用；未重复变更")
                return
            if revision != "20261028_01":
                raise RuntimeError("当前版本不是已验证的初始v2基线")
            grants = c.execute(
                sa.text(
                    "SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='pdd_ingest' AND table_schema='public'"
                )
            ).scalar_one()
            if grants:
                raise RuntimeError("发现基线外既有任务权限，拒绝覆盖")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        module.private_write(
            module.PRIVATE / (stamp + "-before-sales-permissions.sql"),
            module.schema_backup(before["cluster"]),
        )
        with engine.begin() as c:
            migrate(c)
            if (
                c.execute(
                    sa.text("SELECT version_num FROM alembic_version_v2")
                ).scalar_one()
                != "20261028_02"
            ):
                raise RuntimeError("权限revision核对失败")
        module.private_write(
            module.PRIVATE / (stamp + "-sales-permissions.json"),
            json.dumps(
                {"revision": "20261028_02", "before": before}, ensure_ascii=False
            ),
        )
        print("本机M3最小权限revision已应用；未导入数据或改变业务字段")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
