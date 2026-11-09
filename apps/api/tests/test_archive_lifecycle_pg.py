"""归档缺陷回归：真实PG验证去重文件和缺档时不切换当前版本。"""

from datetime import date

import pytest
import sqlalchemy as sa

pytest_plugins = ["sales_pg_support"]


def publisher(kind, engine, root):
    if kind == "sales":
        from test_sales_source import raw_row

        from app.modules.sales.contracts import ReadWindow
        from app.modules.sales.normalization import normalize_shop_daily
        from app.modules.sales.source_policy import SHOPS
        from app.modules.sales.storage import ShopDailyStore

        window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
        store = ShopDailyStore(engine, archive_root=root, approved=window)
        return lambda: store.publish_candidate(
            normalize_shop_daily([raw_row()], window)
        ), "*.ndjson"
    if kind == "product":
        from test_product_board_storage import candidate

        from app.modules.product_board.storage import ProductBoardStore

        return lambda: ProductBoardStore(engine, root).run(
            lambda hb: candidate()
        ), "product_daily.json"
    from test_aftersales_ingestion import sample

    from app.modules.aftersales.publisher import publish_source

    return lambda: publish_source(
        engine, "jst_returns", lambda hb: sample(date(2026, 10, 27))("jst_returns"), root
    ), "*.ndjson"


@pytest.mark.parametrize("kind", ["sales", "product", "aftersales"])
def test_repeated_success_reuses_original_payload_without_new_copy(sales_pg, tmp_path, kind):
    publish, pattern = publisher(kind, sales_pg, tmp_path)
    publish()
    original = {str(p): p.read_bytes() for p in tmp_path.rglob(pattern)}
    assert original
    publish()
    assert {str(p): p.read_bytes() for p in tmp_path.rglob(pattern)} == original


@pytest.mark.parametrize("kind", ["sales", "product", "aftersales"])
def test_missing_retained_input_refuses_success_and_preserves_current(sales_pg, tmp_path, kind):
    publish, pattern = publisher(kind, sales_pg, tmp_path)
    publish()
    with sales_pg.connect() as c:
        before = c.execute(
            sa.text("SELECT dataset_code,version_id FROM current_datasets ORDER BY 1")
        ).all()
    next(tmp_path.rglob(pattern)).unlink()
    with pytest.raises(ValueError, match="ARCHIVE_RESTORE_REQUIRED"):
        publish()
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT dataset_code,version_id FROM current_datasets ORDER BY 1")
            ).all()
            == before
        )
        assert (
            c.execute(
                sa.text("SELECT status FROM task_runs ORDER BY queued_at DESC LIMIT 1")
            ).scalar_one()
            == "failed"
        )


def test_retention_keeps_current_previous_and_requires_verified_backup(sales_pg, tmp_path):
    from test_sales_source import raw_row

    from app.modules.archive_retention import apply_verified_plan, file_record, plan_retention
    from app.modules.sales.contracts import ReadWindow
    from app.modules.sales.normalization import normalize_shop_daily
    from app.modules.sales.source_policy import SHOPS
    from app.modules.sales.storage import ShopDailyStore

    root = tmp_path / "ingestion"
    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    store = ShopDailyStore(sales_pg, archive_root=root / "sales", approved=window)
    vids = []
    for value in ["11", "22", "33"]:
        candidate = normalize_shop_daily(
            [
                raw_row(
                    metrics={
                        "成交金额": value,
                        "店铺访客数": "10",
                        "成交买家数": "2",
                        "成交订单数": "3",
                    }
                )
            ],
            window,
        )
        vids.append(store.publish_candidate(candidate).version_id)
    plan = plan_retention(sales_pg, root)
    assert vids[-1] in plan["protected_versions"] and vids[-2] in plan["protected_versions"]
    assert vids[0] not in plan["protected_versions"]
    assert len(plan["candidates"]) == 1
    items = [file_record(root, root / name) for name in plan["candidates"]]
    with pytest.raises(ValueError, match="ARCHIVE_BACKUP_REQUIRED"):
        apply_verified_plan(sales_pg, root, plan, dict(items=items))
    assert (root / items[0]["path"]).exists()
    receipt = dict(
        items=items, verified=True, backup_sha256="a" * 64, local_path="/controlled/archive.tar.gz"
    )
    result = apply_verified_plan(sales_pg, root, plan, receipt)
    assert result["deleted"] == 1
    assert not (root / items[0]["path"]).exists()
    assert len(list(root.rglob("input.ndjson"))) == 2
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM dataset_versions")).scalar_one() == 3
        assert c.execute(sa.text("SELECT count(*) FROM shop_daily_facts")).scalar_one() == 3


def test_unknown_unregistered_input_is_not_a_retention_candidate(sales_pg, tmp_path):
    from app.modules.archive_retention import plan_retention

    unknown = tmp_path / "unknown" / "new" / "input.ndjson"
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"not a historical archive")
    assert plan_retention(sales_pg, tmp_path)["candidates"] == []


def test_selected_product_update_refuses_missing_unselected_input(sales_pg, tmp_path):
    from test_product_board_storage import candidate

    from app.modules.product_board.storage import ProductBoardStore

    store = ProductBoardStore(sales_pg, tmp_path)
    store.run(lambda hb: candidate())
    next(tmp_path.rglob("profile_records.json")).unlink()
    end, rows = candidate()
    rows["product_daily"][0]["product_revenue"] += 1
    with pytest.raises(ValueError, match="ARCHIVE_RESTORE_REQUIRED"):
        store.run_selected(
            ["product_daily"], lambda hb: (end, {"product_daily": rows["product_daily"]})
        )
