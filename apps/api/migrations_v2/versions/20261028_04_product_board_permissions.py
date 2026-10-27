"""M1既有表最小运行权限；回退停任务后仅撤授权，不删除任何业务输入。"""

from alembic import op

revision = "20261028_04"
down_revision = "20261028_03"
branch_labels = None
depends_on = None
TABLES = (
    "listings",
    "profile_source_records",
    "profile_listing_links",
    "product_daily_facts",
    "product_promotion_records",
    "version_inputs",
    "product_scores",
    "product_score_items",
    "score_standards",
    "score_standard_items",
    "rule_sets",
)


def upgrade():
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT ON public.{table} TO pdd_ingest")


def downgrade():
    for table in TABLES:
        op.execute(f"REVOKE SELECT, INSERT ON public.{table} FROM pdd_ingest")
