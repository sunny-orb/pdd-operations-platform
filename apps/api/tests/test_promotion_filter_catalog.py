"""推广筛选目录真实PG验证：目录完整、日期边界与经营数据分离。"""

import pytest
from sqlalchemy import event

from app.modules.sales.source_policy import SHOPS
from tests.test_promotion_api import client_for, publish_products

pytest_plugins = ["tests.sales_pg_support"]


@pytest.fixture(params=[False, True], autouse=True)
def content_storage_mode(request, monkeypatch):
    """两种物理表示都必须遵守相同的字段/日期/行数读取边界。"""
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(request.param).lower())


def test_catalog_exposes_every_listing_without_business_metrics(sales_pg, tmp_path):
    rows = [
        dict(
            id=f"catalog-{i}",
            shop_id="663599807",
            spu_id=str(100000 + i),
            dt="2026-10-27",
            start_dt="2026-10-27",
            time_granularity="day",
            spu_name=f"商品{i}",
            total_cost="1",
            transaction_amount="2",
            impression_count="10",
            click_count="1",
            transaction_count="1",
        )
        for i in range(125)
    ]
    # 同商品另一推广方式不能重复成为候选；另一日不能混入当前日期目录。
    rows += [
        dict(rows[0], id="duplicate", promotion_name="另一方式"),
        dict(rows[0], id="other-day", spu_id="999999", dt="2026-10-26", start_dt="2026-10-26"),
    ]
    publish_products(sales_pg, tmp_path, rows)
    with client_for(sales_pg) as client:
        version = client.get("/api/v1/promotion/meta?mode=product").json()["meta"]["data_version"]
        params = dict(
            mode="product", date_from="2026-10-27", date_to="2026-10-27", data_version=version
        )
        legacy = client.get("/api/v1/promotion/options", params=params).json()["data"]
        assert legacy["total"] == 125 and len(legacy["products"]) == 100
        result = client.get("/api/v1/promotion/options", params={**params, "catalog": True})
        assert result.status_code == 200, result.text
        catalog = result.json()["data"]
        assert catalog["total"] == len(catalog["products"]) == 125
        assert {s["value"] for s in catalog["shops"]} == set(SHOPS)
        assert len({p["value"] for p in catalog["products"]}) == 125
        assert {p["product_id"] for p in catalog["products"]} == {
            str(100000 + i) for i in range(125)
        }
        for item in catalog["products"]:
            assert set(item) == {
                "value",
                "listing_id",
                "label",
                "product_id",
                "product_name",
                "shop_id",
                "category",
                "level",
                "level_details",
                "operator",
                "search_names",
            }
        stale = client.get(
            "/api/v1/promotion/options", params={**params, "catalog": True, "data_version": "old"}
        )
        assert stale.status_code == 409


def test_catalog_keeps_cross_shop_identity_and_does_not_change_metrics(sales_pg, tmp_path):
    publish_products(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product").json()
        params = dict(
            mode="product",
            date_from="2026-10-27",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        before = client.get("/api/v1/promotion/overview", params=params).json()["data"]
        catalog = client.get(
            "/api/v1/promotion/options", params={**params, "catalog": True}
        ).json()["data"]
        assert len(catalog["products"]) == 2
        assert {p["product_id"] for p in catalog["products"]} == {"123"}
        assert len({p["value"] for p in catalog["products"]}) == 2
        for item in catalog["products"]:
            result = client.get(
                "/api/v1/promotion/overview", params={**params, "listing_ids": item["value"]}
            ).json()["data"]
            assert result["current_record_count"] in ("1", "2")
        after = client.get("/api/v1/promotion/overview", params=params).json()["data"]
        assert after == before


def test_catalog_keeps_empty_shops_selectable_without_inventing_records(sales_pg, tmp_path):
    publish_products(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product").json()
        params = dict(
            mode="product",
            date_from="2026-10-27",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        catalog = client.get(
            "/api/v1/promotion/options", params={**params, "catalog": True, "keyword": "123"}
        ).json()["data"]
        assert {s["value"] for s in catalog["shops"]} == set(SHOPS)
        for shop_id in set(SHOPS) - {"663599807", "799193231"}:
            query = {**params, "shop_id": shop_id}
            overview = client.get("/api/v1/promotion/overview", params=query).json()["data"]
            days = client.get("/api/v1/promotion/days", params=query).json()["data"]
            assert overview["current_record_count"] == "0"
            assert all(m["value"] is None for m in overview["metrics"].values())
            assert days["items"] == [] and days["total"] == 0


def test_filter_bootstrap_does_not_load_full_business_rows(sales_pg, tmp_path):
    """元信息和目录不能为生成选项而读取整批金额、原始推广负载。"""
    publish_products(sales_pg, tmp_path)
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(sales_pg, "before_cursor_execute", record_sql)
    try:
        with client_for(sales_pg) as client:
            meta = client.get("/api/v1/promotion/meta?mode=product").json()
            result = client.get(
                "/api/v1/promotion/options",
                params=dict(
                    mode="product",
                    date_from="2026-10-27",
                    date_to="2026-10-27",
                    data_version=meta["meta"]["data_version"],
                    catalog=True,
                ),
            )
            assert result.status_code == 200, result.text
            assert meta["data"]["observed_rows"] == "3"
            assert result.json()["data"]["total"] == 2
    finally:
        event.remove(sales_pg, "before_cursor_execute", record_sql)
    fact_queries = [s for s in statements if "FROM public.read_product_promotion_records" in s]
    assert fact_queries
    assert all("SELECT *" not in s and "source_payload" not in s for s in fact_queries)
