"""保留网页更新时间证据与最近成功观测时刻；不改变接收指纹或经营版本。"""

from alembic import op

revision = "20261104_11"
down_revision = "20261103_10"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    "ALTER TABLE public.source_checkpoints ADD COLUMN observed_at TIMESTAMPTZ, "
    "ADD COLUMN web_updated_at TEXT, ADD COLUMN web_checked_at TIMESTAMPTZ, "
    "ADD CONSTRAINT source_web_time_pair CHECK "
    "((web_updated_at IS NULL) = (web_checked_at IS NULL)), "
    "ADD CONSTRAINT source_web_time_format CHECK "
    "(web_updated_at IS NULL OR web_updated_at ~ "
    "'^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$')",
    "UPDATE public.source_checkpoints SET observed_at=checked_at WHERE check_status='succeeded'",
)


def upgrade():
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade():
    op.execute("LOCK TABLE public.source_checkpoints IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $preserve$ BEGIN
      IF EXISTS(SELECT 1 FROM public.source_checkpoints
                WHERE web_checked_at IS NOT NULL OR observed_at IS NOT NULL) THEN
        RAISE EXCEPTION '拒绝删除时间证据；回退应用并保留新增列';
      END IF; END $preserve$""")
    op.execute(
        "ALTER TABLE public.source_checkpoints DROP COLUMN observed_at, "
        "DROP COLUMN web_updated_at, DROP COLUMN web_checked_at"
    )
