"""仅本机06→07天气归档：先隔离验证/结构授权备份，不改原30表字段。"""

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
    engine = sa.create_engine(
        local_url("pdd_operations"),
        hide_parameters=True,
        connect_args={"options": "-c lock_timeout=10000 -c statement_timeout=30000"},
    )
    try:
        with engine.connect() as c:
            before = module.snapshot(c)
            revision = c.execute(
                sa.text("SELECT version_num FROM alembic_version_v2")
            ).scalar_one()
            if revision == "20261028_07":
                verify(c)
                print("天气07已应用并核验；未重复变更")
                return
            if revision != "20261028_06" or sa.inspect(c).has_table(
                "weather_publications", schema="public"
            ):
                raise RuntimeError("受控入口只允许无天气表的06→07")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = module.PRIVATE / (stamp + "-before-weather.sql")
        module.private_write(backup, module.schema_backup(before["cluster"]))
        with engine.begin() as c:
            c.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext('project-source-sync'))")
            )
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("验证后迁移内容已变化")
            migrate(c)
            verify(c)
        module.private_write(
            module.PRIVATE / (stamp + "-weather-created.json"),
            json.dumps(
                {"revision": "20261028_07", "backup": str(backup), "before": before},
                ensure_ascii=False,
            ),
        )
        print("本机07已应用；新增天气发布表/保护与最小权限，未改原30表字段或导入来源")
    finally:
        engine.dispose()


def verify(c):
    if (
        c.execute(sa.text("SELECT version_num FROM alembic_version_v2")).scalar_one()
        != "20261028_07"
    ):
        raise RuntimeError("天气revision不符")
    actual = sa.inspect(c).get_columns("weather_publications", schema="public")
    if [x["name"] for x in actual] != [
        "id",
        "issue_date",
        "revision_no",
        "source_system",
        "content_sha256",
        "parser_revision",
        "document",
        "received_at",
    ]:
        raise RuntimeError("天气字段不符")
    for role in ("pdd_app", "pdd_ingest"):
        if not c.execute(
            sa.text("SELECT has_table_privilege(:r,:t,'SELECT')"),
            {"r": role, "t": "weather_publications"},
        ).scalar_one():
            raise RuntimeError("天气SELECT权限缺失")
        for permission in ("UPDATE", "DELETE", "TRUNCATE"):
            if c.execute(
                sa.text("SELECT has_table_privilege(:r,:t,:p)"),
                {"r": role, "t": "weather_publications", "p": permission},
            ).scalar_one():
                raise RuntimeError("禁止扩大天气写入权限")
    if not c.execute(
        sa.text(
            "SELECT has_table_privilege('pdd_ingest','weather_publications','INSERT')"
        )
    ).scalar_one():
        raise RuntimeError("天气writer INSERT权限缺失")
    if c.execute(
        sa.text("SELECT has_table_privilege('pdd_app','weather_publications','INSERT')")
    ).scalar_one():
        raise RuntimeError("API不允许天气写入")


if __name__ == "__main__":
    main()
