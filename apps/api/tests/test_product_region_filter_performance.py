"""候选目录先压缩重复资料；真实PG计划与业务反例，不读上游。"""

import pytest
import sqlalchemy as sa
from test_sales_extended_sources import sample, window
from test_sales_source import raw_row

from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.region_queries import RegionQueries
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def publish(pg, root, rows):
    return ShopDailyStore(
        pg,
        archive_root=root,
        approved=window(),
        spec=SPECS["product_regions"],
    ).publish_candidate(normalize_source("product_regions", rows, window()))


def nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


@pytest.mark.parametrize("reuse", [False, True])
def test_directory_compresses_repeated_names_before_materialization(
    sales_pg, tmp_path, reuse, monkeypatch
):
    """回归：历史地区行不得全量物化后再次为相同目录资料排序。"""
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(reuse).lower())
    ShopDailyStore(sales_pg, archive_root=tmp_path / "daily", approved=window()).publish_candidate(
        normalize_shop_daily([raw_row()], window())
    )
    publish(
        sales_pg,
        tmp_path / "regions",
        [sample("product_regions", id=str(i), product_name=f"Name{i % 2}") for i in range(2000)],
    )
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "jsonb_agg" in statement:
            captured.append((statement, parameters))

    sa.event.listen(sales_pg, "after_cursor_execute", capture)
    try:
        result = RegionQueries(sales_pg).filters(None, "", "", 0, 100)
    finally:
        sa.event.remove(sales_pg, "after_cursor_execute", capture)
    assert result["data"]["total"] == 1
    assert result["data"]["products"][0]["name"] == "Name0 / Name1"
    with sales_pg.connect() as c:
        statement, parameters = captured[0]
        plan = c.exec_driver_sql("EXPLAIN (ANALYZE, FORMAT JSON) " + statement, parameters).scalar()
    materialized_rows = [
        n["Actual Rows"] for n in nodes(plan[0]["Plan"]) if n["Node Type"] == "CTE Scan"
    ]
    assert max(materialized_rows, default=0) <= 2, materialized_rows


@pytest.mark.parametrize("reuse", [False, True])
def test_directory_keeps_identity_names_search_pages_and_version(
    sales_pg, tmp_path, reuse, monkeypatch
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(reuse).lower())
    ShopDailyStore(sales_pg, archive_root=tmp_path / "daily", approved=window()).publish_candidate(
        normalize_shop_daily([raw_row()], window())
    )
    rows = [
        sample("product_regions", id="a", style_code="A", product_name="Alpha"),
        sample(
            "product_regions",
            id="b",
            style_code=" A ",
            product_name="Beta",
            shop_id="799193231",
            shop_name="kocotree运动户外旗舰店",
        ),
        sample("product_regions", id="c", style_code=None, product_code="A", product_name=None),
        sample(
            "product_regions", id="d", style_code="A", product_category="鞋", product_name="Alpha"
        ),
        sample(
            "product_regions",
            id="e",
            style_code=None,
            product_code=None,
            product_category="无编码类",
        ),
        sample("product_regions", id="f", product_category=" 包-材 "),
    ]
    publish(sales_pg, tmp_path / "regions", rows)
    service = RegionQueries(sales_pg)
    result = service.filters(None, "", "", 0, 100)
    version = result["meta"]["data_version"]
    data = result["data"]
    assert data["total"] == 3
    assert set(data["categories"]) == {"雨具", "鞋", "无编码类"}
    assert {p["key"]: p["name"] for p in data["products"]} == {
        '["雨具","style","A"]': "Alpha / Beta",
        '["雨具","sku","A"]': "名称缺失",
        '["鞋","style","A"]': "Alpha",
    }
    pages = [service.filters(version, "", "", i, 1)["data"] for i in range(4)]
    assert [p for page in pages for p in page["products"]] == data["products"]
    assert all(page["total"] == 3 and page["categories"] == data["categories"] for page in pages)
    searched = service.filters(version, "雨具", "bEtA", 0, 100)["data"]
    assert searched["total"] == 1
    assert searched["products"][0]["name"] == "Alpha / Beta"
    assert searched["categories"] == data["categories"]
    publish(sales_pg, tmp_path / "regions-next", [dict(r, product_name="Revised") for r in rows])
    with pytest.raises(SalesError) as error:
        service.filters(version, "", "", 0, 100)
    assert error.value.code == "DATA_VERSION_CHANGED"
