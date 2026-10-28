"""M5既有原行及报告最小写入权限；回退停任务后仅撤销本次授予。"""

from alembic import op

revision = "20261028_06"
down_revision = "20261028_05"
branch_labels = None
depends_on = None
TABLES = ("return_records", "product_sales_records", "aftersales_reports")


def upgrade():
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT ON public.{table} TO pdd_ingest")


def downgrade():
    for table in TABLES:
        op.execute(f"REVOKE SELECT, INSERT ON public.{table} FROM pdd_ingest")
