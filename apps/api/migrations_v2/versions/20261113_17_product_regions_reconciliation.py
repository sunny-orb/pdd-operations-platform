"""商品地区实发实退加入周对账；仅扩展来源约束，保留全部事实及状态。"""

from importlib import import_module

from alembic import op

revision = "20261113_17"
down_revision = "20261111_16"
branch_labels = None
depends_on = None
_CONTENT = import_module("migrations_v2.versions.20261110_14_content_reuse")
ORIGINAL = (
    "'shop_daily','product_daily','product_promotion','profile_records','shop_ratings',"
    "'shop_channel_product_promotion','shop_channel_star_shop','shop_regions'"
)


def _lock():
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(hashtext('project-source-sync'))
         OR NOT pg_try_advisory_xact_lock(hashtext('m3-product_regions')) THEN
        RAISE EXCEPTION 'PRODUCT_REGIONS_MIGRATION_BUSY';
      END IF;
      IF EXISTS (SELECT 1 FROM public.dataset_storage WHERE state='offline') THEN
        RAISE EXCEPTION 'PRODUCT_REGIONS_RESTORE_REQUIRED';
      END IF;
    END $$""")


def _constraint(values):
    op.execute(
        "ALTER TABLE public.source_reconciliations DROP CONSTRAINT "
        "source_reconciliations_source_id_check"
    )
    op.execute(
        "ALTER TABLE public.source_reconciliations ADD CONSTRAINT "
        "source_reconciliations_source_id_check CHECK (source_id IN ("
        + values
        + "))"
    )


def _head(head):
    op.execute(_CONTENT.context(head))
    op.execute(_CONTENT.item_guard(head, _CONTENT.PROTECTED_SQL))


def upgrade():
    _lock()
    _constraint(ORIGINAL + ",'product_regions'")
    _head(revision)


def downgrade():
    _lock()
    op.execute("LOCK TABLE public.source_reconciliations IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM public.source_reconciliations WHERE source_id='product_regions')
         OR EXISTS (SELECT 1 FROM public.retention_archives WHERE schema_head='20261113_17') THEN
        RAISE EXCEPTION 'PRODUCT_REGIONS_DOWNGRADE_EVIDENCE_EXISTS';
      END IF;
    END $$""")
    _constraint(ORIGINAL)
    _head(down_revision)
