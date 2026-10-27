"""五看板30表独立空结构基线；2026-10-28用户确认先隔离验证再建本地空表。

原因：用户放弃旧M1表设计，确认30表494字段及50FK，要求迁移先行、
逐项审查后再确认建表。此独立根revision不接旧head、不导入旧业务模型。
边界：只建空结构，不迁移旧数据、导入来源、授权角色或启动任务。
回退：仅允许全空的本期业务表；有数据必须先做另行审批的保留/修复方案。
执行前须通过真实PG升级、约束、并发和回退验证；离线编译不能替代。
隔离首次执行在事务内失败、未应用revision：修正冻结CHECK生成器，使用
literal_column保留JSON常量冒号，防text绑定解析将数字/布尔/null损坏为NULL。
"""

import sqlalchemy as sa
from alembic import op

from migrations_v2.baseline_20261028.guards import (
    downgrade_statements,
    upgrade_statements,
)
from migrations_v2.baseline_20261028.schema import build_metadata

revision = "20261028_01"
down_revision = None
branch_labels = ("operations_v2",)
depends_on = None


def upgrade() -> None:
    """在已获准的空public结构创建基线；禁止混入旧迁移和同名函数。"""
    op.execute(
        sa.text("""
DO $preflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f')
          AND c.relname <> 'alembic_version_v2'
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname LIKE 'pdd_v2_%'
    ) THEN
        RAISE EXCEPTION 'v2基线只允许空public结构，拒绝混用既有对象';
    END IF;
END
$preflight$
""")
    )
    metadata = build_metadata()
    for table in metadata.sorted_tables:
        op.execute(sa.schema.CreateTable(table))
        for index in sorted(table.indexes, key=lambda item: item.name):
            op.execute(sa.schema.CreateIndex(index))
        op.execute(sa.schema.SetTableComment(table))
        for column in table.c:
            op.execute(sa.schema.SetColumnComment(column))
    for statement in upgrade_statements(metadata):
        op.execute(sa.text(statement))


def downgrade() -> None:
    """拒绝对有业务记录的结构回退；不递归删除其他对象。"""
    metadata = build_metadata()
    # 先阻断并发写入，避免空表检查与DROP之间出现新业务行。
    names = ", ".join("public." + table.name for table in metadata.sorted_tables)
    op.execute(sa.text("LOCK TABLE " + names + " IN ACCESS EXCLUSIVE MODE"))
    checks = "\n".join(
        f"IF EXISTS (SELECT 1 FROM public.{table.name} LIMIT 1) THEN "
        f"RAISE EXCEPTION '拒绝回退：{table.name}含记录'; END IF;"
        for table in metadata.sorted_tables
    )
    op.execute(sa.text("DO $empty$ BEGIN " + checks + " END $empty$"))
    for table in reversed(metadata.sorted_tables):
        op.execute(sa.schema.DropTable(table))
    for statement in downgrade_statements():
        op.execute(sa.text(statement))
