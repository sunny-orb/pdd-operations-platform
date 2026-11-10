"""独立保留八个数仓来源每周对账状态。"""

from importlib import import_module

from alembic import op

revision = "20261111_15"
down_revision = "20261110_14"
branch_labels = None
depends_on = None

_CONTENT = import_module("migrations_v2.versions.20261110_14_content_reuse")
UPGRADE_SQL = (
    """CREATE TABLE public.source_reconciliations (
      source_id TEXT PRIMARY KEY CHECK (source_id IN (
        'shop_daily','product_daily','product_promotion','profile_records','shop_ratings',
        'shop_channel_product_promotion','shop_channel_star_shop','shop_regions'
      )),
      cycle_at TIMESTAMPTZ NOT NULL,
      status TEXT NOT NULL,
      last_attempt_at TIMESTAMPTZ,
      last_finished_at TIMESTAMPTZ,
      last_success_at TIMESTAMPTZ,
      legacy_task_id TEXT REFERENCES public.task_runs(id) ON DELETE SET NULL,
      error_code TEXT CHECK (error_code IN (
        'CHECK_FAILED','WAREHOUSE_WEB_LOGIN_REQUIRED','SYNC_FAILED','SOURCE_CHANGED_DURING_SYNC',
        'WORKER_LOST','MAINTENANCE_DEFERRED','DATASET_RESTORE_REQUIRED'
      )),
      CONSTRAINT source_reconciliation_state CHECK (
        (status='waiting' AND last_attempt_at IS NULL AND last_finished_at IS NULL
          AND last_success_at IS NULL AND legacy_task_id IS NULL AND error_code IS NULL)
        OR (last_attempt_at IS NOT NULL AND last_attempt_at >= cycle_at AND (
          (status='running' AND last_finished_at IS NULL)
          OR (last_finished_at IS NOT NULL AND last_finished_at >= last_attempt_at AND (
            (status='succeeded' AND last_success_at IS NOT NULL
              AND last_success_at=last_finished_at AND error_code IS NULL)
            OR (status='failed' AND error_code IS NOT NULL
              AND error_code NOT IN ('MAINTENANCE_DEFERRED','DATASET_RESTORE_REQUIRED'))
            OR (status='deferred' AND error_code IS NOT NULL
              AND error_code IN ('MAINTENANCE_DEFERRED','DATASET_RESTORE_REQUIRED'))
          ))
        ))
      ),
      CONSTRAINT source_reconciliation_success_time CHECK (
        status='succeeded' OR last_success_at IS NULL OR last_success_at < cycle_at
      )
    )""",
    "COMMENT ON TABLE public.source_reconciliations IS "
    "'每物理来源每周对账，独立于普通变化接收状态'",
    "GRANT SELECT ON public.source_reconciliations TO pdd_app",
    "GRANT SELECT,INSERT,UPDATE ON public.source_reconciliations TO pdd_ingest",
)


def _lock():
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(hashtext('project-source-sync')) THEN
        RAISE EXCEPTION 'RECONCILIATION_MIGRATION_BUSY';
      END IF;
    END $$""")


def upgrade():
    _lock()
    # 旧归档回执仍绑定原结构，先恢复离线事实再跨结构升级。
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.dataset_storage WHERE state='offline') THEN
        RAISE EXCEPTION 'RECONCILIATION_UPGRADE_RESTORE_REQUIRED';
      END IF;
    END $$""")
    for statement in UPGRADE_SQL:
        op.execute(statement)
    op.execute(_CONTENT.context(revision))
    op.execute(_CONTENT.item_guard(revision, _CONTENT.PROTECTED_SQL))


def downgrade():
    _lock()
    op.execute("LOCK TABLE public.source_reconciliations IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.source_reconciliations)
         OR EXISTS (SELECT 1 FROM public.retention_archives WHERE schema_head='20261111_15') THEN
        RAISE EXCEPTION '保留每周对账证据；回退应用但保留状态表';
      END IF;
    END $$""")
    op.execute("DROP TABLE public.source_reconciliations")
    op.execute(_CONTENT.context(down_revision))
    op.execute(_CONTENT.item_guard(down_revision, _CONTENT.PROTECTED_SQL))
