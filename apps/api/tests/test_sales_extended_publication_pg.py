"""整页新来源的真实PG发布反例，不写项目库。"""

import pytest
import sqlalchemy as sa
from test_sales_extended_sources import sample, window

from app.modules.sales.datasets import SPECS
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def test_two_channels_publish_without_replacing_each_other(sales_pg, tmp_path):
    ids = {}
    for code in (
        "shop_channel_product_promotion",
        "shop_channel_star_shop",
        "shop_ratings",
        "shop_regions",
        "shop_refunds",
        "product_regions",
    ):
        store = ShopDailyStore(
            sales_pg, archive_root=tmp_path / code, approved=window(), spec=SPECS[code]
        )
        candidate = normalize_source(code, [sample(code)], window())
        result = store.publish_candidate(candidate)
        assert result.status == "succeeded"
        from app.modules.sales.archive_replay import restore_candidate

        assert (
            restore_candidate(tmp_path / code / result.version_id).fingerprint
            == candidate.fingerprint
        )
        assert store.publish_candidate(candidate).version_id == result.version_id
        ids[code] = result.version_id
    with sales_pg.connect() as c:
        heads = dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        )
        assert heads == ids
        assert (
            c.execute(sa.text("SELECT count(*) FROM shop_channel_promotion_daily")).scalar_one()
            == 2
        )
        assert c.execute(sa.text("SELECT count(*) FROM shop_rating_daily")).scalar_one() == 1
        assert c.execute(sa.text("SELECT count(*) FROM shop_refund_records")).scalar_one() == 1


def test_partial_large_draft_never_replaces_successful_pointer(sales_pg, tmp_path):
    code = "product_regions"
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window(), spec=SPECS[code])
    first = store.publish_candidate(normalize_source(code, [sample(code)], window()))
    rows = [sample(code)] + [
        sample(code, id=f"extra-{i}", product_code=str(i)) for i in range(1000)
    ]
    inserts = 0

    def fail(conn, cursor, statement, params, context, many):
        nonlocal inserts
        if statement.startswith("INSERT INTO public.product_region_records"):
            inserts += 1
            if inserts == 2:
                raise RuntimeError("synthetic second batch failure")

    sa.event.listen(sales_pg, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="second batch"):
            store.publish_candidate(normalize_source(code, rows, window()))
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail)
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='product_regions'"
                )
            ).scalar_one()
            == first.version_id
        )
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM dataset_versions WHERE state='draft'")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")).scalar_one()
            == 1
        )
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM product_region_records WHERE dataset_version_id<>:v"),
                {"v": first.version_id},
            ).scalar_one()
            == 1000
        )


def test_new_runtime_permissions_stay_within_m3_tables(sales_pg):
    with sales_pg.connect() as c:
        for name in (
            "shop_channel_promotion_daily",
            "shop_rating_daily",
            "shop_refund_records",
            "shop_region_daily",
            "product_region_records",
            "sales_targets",
            "sales_target_shops",
        ):
            assert c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest',:table,'INSERT')"), {"table": name}
            ).scalar_one()
            assert not c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest',:table,'DELETE')"), {"table": name}
            ).scalar_one()
        assert not c.execute(
            sa.text("SELECT has_table_privilege('pdd_ingest','product_daily_facts','INSERT')")
        ).scalar_one()
