"""来源检查与已接收标记；一来源一行，无经营事实，不改变既有发布约束。

回退先停检查任务；存在记录时拒绝删除，可停用监测并继续旧同步。
"""

from alembic import op

revision = "20261103_10"
down_revision = "20261031_09"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    """CREATE TABLE public.source_checkpoints (
      source_id TEXT PRIMARY KEY CHECK (btrim(source_id) <> ''),
      checked_at TIMESTAMPTZ NOT NULL,
      check_status TEXT NOT NULL CHECK (check_status IN ('checking','succeeded','failed')),
      observation JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(observation)='object'),
      platform_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(platform_snapshot)='object'),
      observed_fingerprint TEXT,
      accepted_fingerprint TEXT,
      accepted_at TIMESTAMPTZ,
      error_code TEXT,
      duration_ms BIGINT NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
      CHECK (check_status <> 'succeeded' OR observed_fingerprint IS NOT NULL),
      CHECK ((accepted_fingerprint IS NULL) = (accepted_at IS NULL))
    )""",
    "COMMENT ON TABLE public.source_checkpoints IS "
    "'实际来源的最近检查及已接收变化标记，不证明业务完整'",
    "GRANT SELECT ON public.source_checkpoints TO pdd_app",
    "GRANT SELECT,INSERT,UPDATE ON public.source_checkpoints TO pdd_ingest",
)


def upgrade():
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade():
    op.execute("LOCK TABLE public.source_checkpoints IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $empty$ BEGIN
      IF EXISTS(SELECT 1 FROM public.source_checkpoints) THEN
        RAISE EXCEPTION '拒绝删除来源检查记录；请停用监测并保留表';
      END IF; END $empty$""")
    op.execute("DROP TABLE public.source_checkpoints")
