"""保留M1必要来源店名与上架状态原值。

版本：20261023_03
上版：20261023_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20261023_03"
down_revision = "20261023_02"
branch_labels = None
depends_on = None


def upgrade():
    """新增可空原值列，不回填猜测名称/状态。"""
    op.add_column("product_days", sa.Column("source_shop_name", sa.Text, nullable=True))
    op.add_column("promotion_records", sa.Column("source_shop_name", sa.Text, nullable=True))
    op.add_column(
        "product_profile_records", sa.Column("source_listing_status", sa.Text, nullable=True)
    )


def downgrade():
    """移除本版三原值列；有输入时先备份或前向修复，不自动执行。"""
    op.drop_column("product_profile_records", "source_listing_status")
    op.drop_column("promotion_records", "source_shop_name")
    op.drop_column("product_days", "source_shop_name")
