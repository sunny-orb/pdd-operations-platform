"""退款目标支持完整区间，保留旧单值目标与既有不可变保护。

有区间、新适用策略或非默认指标时拒绝降级；不删行、不改写目标值。
"""

from alembic import op

revision = "20261028_08"
down_revision = "20261028_07"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    "ALTER TABLE public.refund_targets ADD COLUMN metric_code TEXT NOT NULL "
    "DEFAULT 'total_refund_rate'",
    "ALTER TABLE public.refund_targets ADD COLUMN lower_rate NUMERIC NULL",
    "ALTER TABLE public.refund_targets ADD COLUMN upper_rate NUMERIC NULL",
    "ALTER TABLE public.refund_targets ADD COLUMN application_policy_code TEXT NULL",
    "ALTER TABLE public.refund_targets ALTER COLUMN target_rate DROP NOT NULL",
    "ALTER TABLE public.refund_targets ADD CONSTRAINT ck_refund_targets_metric_code_values "
    "CHECK (metric_code IN ('total_refund_rate','aftersale_refund_rate'))",
    "ALTER TABLE public.refund_targets ADD CONSTRAINT "
    "ck_refund_targets_application_policy_code_values CHECK "
    "(application_policy_code IN ('rolling_30d_and_natural_month'))",
    """ALTER TABLE public.refund_targets ADD CONSTRAINT ck_refund_targets_scalar_or_range
CHECK (
 (target_rate IS NOT NULL AND lower_rate IS NULL AND upper_rate IS NULL)
 OR
 (target_rate IS NULL AND lower_rate IS NOT NULL AND upper_rate IS NOT NULL
  AND lower_rate NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND upper_rate NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND 0 <= lower_rate AND lower_rate <= upper_rate AND upper_rate <= 1
  AND application_policy_code IS NOT NULL)
)""",
    "COMMENT ON COLUMN public.refund_targets.metric_code IS '退款率指标代码'",
    "COMMENT ON COLUMN public.refund_targets.lower_rate IS '退款率目标下限'",
    "COMMENT ON COLUMN public.refund_targets.upper_rate IS '退款率目标上限'",
    "COMMENT ON COLUMN public.refund_targets.application_policy_code IS '滚动30天与自然月适用策略'",
    "GRANT SELECT ON public.sales_targets, public.sales_target_shops, "
    "public.refund_targets TO pdd_app",
    "GRANT SELECT, INSERT ON public.sales_targets, public.sales_target_shops, "
    "public.refund_targets TO pdd_ingest",
    # 成员触发器FOR SHARE父行需要任一列UPDATE；原封版/身份保护仍然生效。
    "GRANT UPDATE(id) ON public.sales_targets TO pdd_ingest",
)


def upgrade():
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade():
    op.execute("LOCK TABLE public.refund_targets IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $preserve$ BEGIN
 IF EXISTS (
  SELECT 1 FROM public.refund_targets WHERE lower_rate IS NOT NULL OR upper_rate IS NOT NULL
  OR application_policy_code IS NOT NULL OR metric_code IS DISTINCT FROM 'total_refund_rate'
  OR target_rate IS NULL
 ) THEN
  RAISE EXCEPTION '拒绝回退：退款目标含区间、新适用策略或非默认指标，须先制定保留方案';
 END IF;
END $preserve$""")
    op.execute("REVOKE UPDATE(id) ON public.sales_targets FROM pdd_ingest")
    # sales两表SELECT/INSERT属于03；API SELECT是既定职责，回退均保留。
    op.execute("REVOKE SELECT, INSERT ON public.refund_targets FROM pdd_ingest")
    for name in (
        "ck_refund_targets_scalar_or_range",
        "ck_refund_targets_application_policy_code_values",
        "ck_refund_targets_metric_code_values",
    ):
        op.execute(f"ALTER TABLE public.refund_targets DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE public.refund_targets ALTER COLUMN target_rate SET NOT NULL")
    for column in ("application_policy_code", "upper_rate", "lower_rate", "metric_code"):
        op.execute(f"ALTER TABLE public.refund_targets DROP COLUMN {column}")
