"""按物理来源记录固定槽和接收结果，避免组合任务污染未拉取来源的状态。"""

from alembic import op

revision = "20261104_12"
down_revision = "20261104_11"
branch_labels = None
depends_on = None
UPGRADE_SQL = (
    "ALTER TABLE public.source_checkpoints ADD COLUMN scheduled_slot_at TIMESTAMPTZ, "
    "ADD COLUMN scheduled_attempts INTEGER NOT NULL DEFAULT 0 "
    "CHECK (scheduled_attempts BETWEEN 0 AND 2), "
    "ADD COLUMN sync_started_at TIMESTAMPTZ, ADD COLUMN sync_finished_at TIMESTAMPTZ, "
    "ADD COLUMN sync_status TEXT CHECK(sync_status IN ('running','succeeded','failed')), "
    "ADD CONSTRAINT source_sync_times CHECK ("
    "(sync_status IS NULL AND sync_started_at IS NULL AND sync_finished_at IS NULL) OR "
    "(sync_status IS NOT NULL AND ((sync_status='running' "
    "AND sync_started_at IS NOT NULL AND sync_finished_at IS NULL) OR "
    "(sync_status IN ('succeeded','failed') AND sync_started_at IS NOT NULL "
    "AND sync_finished_at IS NOT NULL AND sync_finished_at>=sync_started_at))))",
)


def upgrade():
    for sql in UPGRADE_SQL:
        op.execute(sql)


def downgrade():
    op.execute("LOCK TABLE public.source_checkpoints IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.source_checkpoints WHERE scheduled_slot_at IS NOT NULL
                OR sync_status IS NOT NULL) THEN
        RAISE EXCEPTION '保留来源调度证据；回退应用但保留新增列';
      END IF; END $$""")
    op.execute(
        "ALTER TABLE public.source_checkpoints DROP CONSTRAINT source_sync_times, "
        "DROP COLUMN scheduled_slot_at, DROP COLUMN scheduled_attempts, "
        "DROP COLUMN sync_started_at, DROP COLUMN sync_finished_at, DROP COLUMN sync_status"
    )
