"""M3整页既有事实和目标表最小权限；回退仅撤新增授权，保留数据。"""

from alembic import op

revision = "20261028_03"
down_revision = "20261028_02"
branch_labels = None
depends_on = None
TABLES = (
    "shop_channel_promotion_daily",
    "shop_rating_daily",
    "shop_refund_records",
    "shop_region_daily",
    "product_region_records",
    "sales_targets",
    "sales_target_shops",
)


def upgrade():
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT ON public.{table} TO pdd_ingest")


def downgrade():
    for table in TABLES:
        op.execute(f"REVOKE SELECT, INSERT ON public.{table} FROM pdd_ingest")
