"""评分父行锁的最小列权限；回退只撤授权，不删除标准或评分。"""

from alembic import op

revision = "20261028_05"
down_revision = "20261028_04"
branch_labels = None
depends_on = None
TABLES = ("score_standards", "product_scores")


def upgrade():
    # PostgreSQL FOR SHARE需任一列UPDATE；封版不可变触发器仍限制实际UPDATE。
    for table in TABLES:
        op.execute(f"GRANT UPDATE(id) ON public.{table} TO pdd_ingest")


def downgrade():
    for table in TABLES:
        op.execute(f"REVOKE UPDATE(id) ON public.{table} FROM pdd_ingest")
