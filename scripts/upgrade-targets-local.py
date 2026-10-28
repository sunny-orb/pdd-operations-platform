"""仅交付受控本机07→08入口；先隔离验证、同集群结构/授权备份和项目同步锁。

不导入目标或发布报告；有真实数据时不得使用db-v2 create替代本入口。
"""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.baseline_20261028.schema import build_metadata  # noqa: E402
from migrations_v2.local import local_url, migrate  # noqa: E402

OLD, NEW = "20261028_07", "20261028_08"
TABLES = ("sales_targets", "sales_target_shops", "refund_targets")
EXTRA_CHECKS = {
    "ck_refund_targets_metric_code_values",
    "ck_refund_targets_application_policy_code_values",
    "ck_refund_targets_scalar_or_range",
}
EXTRA_COLUMNS = (
    ("metric_code", "TEXT", False, "'total_refund_rate'::text", "退款率指标代码"),
    ("lower_rate", "NUMERIC", True, None, "退款率目标下限"),
    ("upper_rate", "NUMERIC", True, None, "退款率目标上限"),
    ("application_policy_code", "TEXT", True, None, "滚动30天与自然月适用策略"),
)


def _runner():
    spec = importlib.util.spec_from_file_location(
        "v2_target_entry", ROOT / "scripts/db-v2.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(c, *, extended=True):
    """只读核验07/08精确列及目标最小权限；不修改结构、授权或业务行。"""
    revision = c.execute(
        sa.text("SELECT version_num FROM public.alembic_version_v2")
    ).scalar_one()
    if revision != (NEW if extended else OLD):
        raise RuntimeError("目标revision不符")
    baseline = build_metadata().tables["public.refund_targets"]
    inspector = sa.inspect(c)
    columns = inspector.get_columns("refund_targets", schema="public")
    expected_names = [
        *baseline.c.keys(),
        *([x[0] for x in EXTRA_COLUMNS] if extended else []),
    ]
    if [x["name"] for x in columns] != expected_names:
        raise RuntimeError("退款目标字段清单不符")
    for actual, expected in zip(columns[: len(baseline.c)], baseline.c, strict=True):
        expected_nullable = (
            True if extended and expected.name == "target_rate" else expected.nullable
        )
        if (
            actual["nullable"] != expected_nullable
            or actual["comment"] != expected.comment
            or str(actual["type"].compile(dialect=c.dialect))
            != str(expected.type.compile(dialect=c.dialect))
            or actual["default"] is not None
        ):
            raise RuntimeError("退款目标原字段定义不符")
    if extended and [
        (x["name"], str(x["type"]), x["nullable"], x["default"], x["comment"])
        for x in columns[-4:]
    ] != list(EXTRA_COLUMNS):
        raise RuntimeError("退款目标区间字段定义不符")
    checks = {
        x["name"]
        for x in inspector.get_check_constraints("refund_targets", schema="public")
    }
    expected_checks = {
        x.name for x in baseline.constraints if isinstance(x, sa.CheckConstraint)
    }
    if checks != expected_checks | (EXTRA_CHECKS if extended else set()):
        raise RuntimeError("退款目标CHECK清单不符")
    for role in ("pdd_app", "pdd_ingest"):
        for table in TABLES:
            for permission in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                actual = c.execute(
                    sa.text("SELECT has_table_privilege(:r,:t,:p)"),
                    dict(r=role, t=table, p=permission),
                ).scalar_one()
                # 07可能靠库级默认权限给API只读；08才显式保证这三表的SELECT下限。
                if not extended and role == "pdd_app" and permission == "SELECT":
                    continue
                expected = permission == "SELECT" and (
                    role == "pdd_app" or table != "refund_targets" or extended
                )
                expected |= (
                    permission == "INSERT"
                    and role == "pdd_ingest"
                    and (table != "refund_targets" or extended)
                )
                if actual != expected:
                    raise RuntimeError("目标表权限不符合最小授权")
            for column in inspector.get_columns(table, schema="public"):
                actual = c.execute(
                    sa.text("SELECT has_column_privilege(:r,:t,:col,'UPDATE')"),
                    dict(r=role, t=table, col=column["name"]),
                ).scalar_one()
                if actual != (
                    extended
                    and role == "pdd_ingest"
                    and table == "sales_targets"
                    and column["name"] == "id"
                ):
                    raise RuntimeError("目标列写权限不符合父行锁最小授权")
        if c.execute(
            sa.text("SELECT has_schema_privilege(:r,'public','CREATE')"), dict(r=role)
        ).scalar_one():
            raise RuntimeError("目标运行身份不得具有DDL权限")


def main():
    module = _runner()
    evidence = json.loads(module.EVIDENCE.read_text())
    if (
        evidence["result"] != "passed"
        or evidence["fingerprint"] != module.fingerprint()
    ):
        raise RuntimeError("须先运行与当前文件一致的独立v2隔离验证")
    if ScriptDirectory.from_config(
        Config(str(ROOT / "apps/api/alembic-v2.ini"))
    ).get_heads() != [NEW]:
        raise RuntimeError("目标升级入口仅允许07→08，不能跟随其他迁移head")
    engine = sa.create_engine(
        local_url("pdd_operations"),
        hide_parameters=True,
        connect_args={"options": "-c lock_timeout=10000 -c statement_timeout=30000"},
    )
    try:
        with engine.connect() as c:
            before = module.snapshot(c)
            revision = c.execute(
                sa.text("SELECT version_num FROM public.alembic_version_v2")
            ).scalar_one()
            if revision == NEW:
                verify(c)
                print("目标08已应用并核验；未重复变更")
                return
            verify(c, extended=False)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = module.PRIVATE / (stamp + "-before-target-ranges.sql")
        module.private_write(backup, module.schema_backup(before["cluster"]))
        with engine.begin() as c:
            c.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext('project-source-sync'))")
            )
            locked = module.snapshot(c)
            if locked != before:
                raise RuntimeError("备份后项目结构或集群已变化，拒绝继续")
            verify(c, extended=False)
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("验证后目标迁移文件已变化")
            migrate(c)
            verify(c)
            if evidence["fingerprint"] != module.fingerprint():
                raise RuntimeError("目标迁移期间文件已变化，事务回滚")
        with engine.connect() as c:
            verify(c)
            after = module.snapshot(c)
        module.private_write(
            module.PRIVATE / (stamp + "-target-ranges-upgraded.json"),
            json.dumps(
                {
                    "revision": NEW,
                    "fingerprint": evidence["fingerprint"],
                    "verification_log": evidence["test_log"],
                    "backup": str(backup),
                    "before": before,
                    "after": after,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        print("本机08已提交并独立核验：退款目标区间及最小权限；未导入目标或发布报告")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (SQLAlchemyError, OSError, ValueError, KeyError, RuntimeError):
        print(
            "目标升级失败；未继续后续阶段，请核对隔离验证、项目revision及私有备份",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
