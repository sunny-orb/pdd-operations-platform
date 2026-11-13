"""仅扩展本地管理员的保留入口；业务表、内容和既有保护不变。"""

from importlib import import_module

from alembic import op

revision = "20261114_18"
down_revision = "20261113_17"
branch_labels = depends_on = None
_CONTENT = import_module("migrations_v2.versions.20261110_14_content_reuse")
_TEST_ONLY = "current_database() !~ '^pdd_operations_v2_test_[0-9a-f]{12}$'"
_LOCAL_ADMIN = (
    "NOT (current_database() ~ '^pdd_operations_v2_test_[0-9a-f]{12}$'"
    " OR (current_database()='pdd_operations' AND session_user='pdd_admin'))"
)


def _lock():
    """事务持维护互斥及任务表锁，拒绝运行任务和待恢复离线版本。"""
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RAISE EXCEPTION 'LOCAL_STORAGE_MIGRATION_BUSY';
      END IF;
    END $$""")
    op.execute("LOCK TABLE public.task_runs IN SHARE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.task_runs WHERE status='running') THEN
        RAISE EXCEPTION 'LOCAL_STORAGE_MIGRATION_BUSY';
      END IF;
      IF EXISTS (SELECT 1 FROM public.dataset_storage WHERE state='offline') THEN
        RAISE EXCEPTION 'LOCAL_STORAGE_RESTORE_REQUIRED';
      END IF;
    END $$""")


def upgrade():
    _lock()
    op.execute(_CONTENT.context(revision))
    guard = _CONTENT.item_guard(revision, _CONTENT.PROTECTED_SQL)
    if guard.count(_TEST_ONLY) != 1:
        raise RuntimeError("LOCAL_STORAGE_GUARD_CHANGED")
    op.execute(guard.replace(_TEST_ONLY, _LOCAL_ADMIN, 1))
    op.execute("GRANT SELECT(version_num) ON public.alembic_version_v2 TO pdd_ingest")


def downgrade():
    _lock()
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.retention_archives WHERE schema_head='20261114_18') THEN
        RAISE EXCEPTION 'LOCAL_STORAGE_DOWNGRADE_EVIDENCE_EXISTS';
      END IF;
    END $$""")
    op.execute("REVOKE SELECT(version_num) ON public.alembic_version_v2 FROM pdd_ingest")
    op.execute(_CONTENT.context(down_revision))
    op.execute(_CONTENT.item_guard(down_revision, _CONTENT.PROTECTED_SQL))
