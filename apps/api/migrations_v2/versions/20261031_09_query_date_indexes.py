"""商品推广、产品地区实际查询以period_start定位；追加窄日期索引。

保留既有粒度/期末/链接索引，不改变业务唯一性、数据或权限。
普通事务性DDL；调用者先获取项目同步锁并设置锁等待预算。
"""

from alembic import op

revision = "20261031_09"
down_revision = "20261028_08"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_product_promotion_records_version_period_start", "product_promotion_records"),
    ("ix_product_region_records_version_period_start", "product_region_records"),
)


def upgrade():
    for name, table in INDEXES:
        op.create_index(name, table, ["dataset_version_id", "period_start"], schema="public")


def downgrade():
    for name, table in reversed(INDEXES):
        op.drop_index(name, table_name=table, schema="public")
