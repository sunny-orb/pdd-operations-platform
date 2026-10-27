"""限定解析资料的映射版本必须属于店铺身份数据集。

版本：20261023_02
上版：20261023_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20261023_02"
down_revision = "20261023_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加映射数据集约束；已有错误引用先拒绝迁移，不静默改写业务输入。"""
    op.add_column(
        "product_profiles",
        sa.Column("mapping_dataset_id", sa.Text, nullable=False, server_default="shop_identity"),
    )
    op.drop_constraint(
        "fk_product_profiles_mapping_version_dataset_versions",
        "product_profiles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_product_profiles_mapping_version_dataset_versions",
        "product_profiles",
        "dataset_versions",
        ["mapping_version", "mapping_dataset_id"],
        ["version_id", "dataset_id"],
    )
    op.create_check_constraint(
        op.f("ck_product_profiles_mapping_dataset"),
        "product_profiles",
        "mapping_dataset_id='shop_identity'",
    )


def downgrade() -> None:
    """恢复单列外键后删辅助列，不删除资料行；回退会失去数据集限定保护。"""
    op.drop_constraint(
        op.f("ck_product_profiles_mapping_dataset"), "product_profiles", type_="check"
    )
    op.drop_constraint(
        "fk_product_profiles_mapping_version_dataset_versions",
        "product_profiles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_product_profiles_mapping_version_dataset_versions",
        "product_profiles",
        "dataset_versions",
        ["mapping_version"],
        ["version_id"],
    )
    op.drop_column("product_profiles", "mapping_dataset_id")
