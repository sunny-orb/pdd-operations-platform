"""售后历史固定版本/期间汇总的部分期间索引；不改变事实或指标语义。

保留款码索引，仅legacy return_records受益。普通事务DDL；调用者保持
停写锁、锁等待及维护语句预算，失败连同版本更新整体回滚。
"""

from importlib import import_module

import sqlalchemy as sa
from alembic import op

revision = "20261111_16"
down_revision = "20261111_15"
branch_labels = None
depends_on = None

INDEX = "ix_return_records_summary_window"
_CONTENT = import_module("migrations_v2.versions.20261110_14_content_reuse")


def _lock():
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(hashtext('project-source-sync')) THEN
        RAISE EXCEPTION 'SUMMARY_INDEX_MIGRATION_BUSY';
      END IF;
    END $$""")


def _bind_retention_head(head):
    # 冻结保护逻辑只替换结构绑定；生产写入硬关闭及权限不变。
    op.execute(_CONTENT.context(head))
    op.execute(_CONTENT.item_guard(head, _CONTENT.PROTECTED_SQL))


def upgrade():
    _lock()
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.dataset_storage WHERE state='offline') THEN
        RAISE EXCEPTION 'SUMMARY_INDEX_UPGRADE_RESTORE_REQUIRED';
      END IF;
    END $$""")
    _bind_retention_head(revision)
    op.create_index(
        INDEX,
        "return_records",
        ["dataset_version_id", "period_start", "period_end"],
        schema="public",
        postgresql_where=sa.text(
            "row_kind = 'detail' AND period_start IS NOT NULL AND period_end IS NOT NULL"
        ),
    )


def downgrade():
    _lock()
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.dataset_storage WHERE state='offline')
         OR EXISTS (SELECT 1 FROM public.retention_archives WHERE schema_head='20261111_16') THEN
        RAISE EXCEPTION 'SUMMARY_INDEX_DOWNGRADE_RESTORE_REQUIRED';
      END IF;
    END $$""")
    op.drop_index(INDEX, table_name="return_records", schema="public")
    _bind_retention_head(down_revision)
