"""在既有 F01 原行保留销售收入和做单原值，旧发布版保持空值。"""

from alembic import op

revision = "20261127_22"
down_revision = "20261127_21"
branch_labels = depends_on = None


def _lock():
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(hashtext('project-source-sync'))
         OR NOT pg_try_advisory_xact_lock(hashtext('m3-shop_refunds'))
         OR NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RAISE EXCEPTION 'F01_GMV_MIGRATION_BUSY';
      END IF;
    END $$""")


def upgrade():
    _lock()
    op.execute("""ALTER TABLE public.shop_refund_records
      ADD COLUMN source_reported_gmv numeric,
      ADD COLUMN source_adjustment_amount numeric,
      ADD CONSTRAINT ck_shop_refund_records_source_reported_gmv_finite
        CHECK (source_reported_gmv NOT IN
          ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
      ADD CONSTRAINT ck_shop_refund_records_source_adjustment_amount_finite
        CHECK (source_adjustment_amount NOT IN
          ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric))""")
    op.execute("""COMMENT ON COLUMN public.shop_refund_records.source_reported_gmv
      IS 'F01销售收入（GMV）源公式原值，元；基础金额空时不计入预测'""")
    op.execute("""COMMENT ON COLUMN public.shop_refund_records.source_adjustment_amount
      IS 'F01做单金额来源原值，元；空值保留，不从来源GMV再次扣减'""")


def downgrade():
    _lock()
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.shop_refund_records
        WHERE source_reported_gmv IS NOT NULL OR source_adjustment_amount IS NOT NULL)
        OR EXISTS (SELECT 1 FROM public.dataset_versions
        WHERE dataset_code='shop_refunds' AND row_schema_revision='f01-reported-sales-gmv-v1') THEN
        RAISE EXCEPTION 'F01_GMV_DOWNGRADE_HAS_DATA';
      END IF;
    END $$""")
    op.execute("""ALTER TABLE public.shop_refund_records
      DROP COLUMN source_reported_gmv, DROP COLUMN source_adjustment_amount""")
