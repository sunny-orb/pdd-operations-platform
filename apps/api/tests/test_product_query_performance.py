"""相同只读请求内消除重复SQL，真实PG验证查询隔离和原值不变。"""

from copy import deepcopy
from datetime import date

import sqlalchemy as sa
from test_m1_ingestion import pg, repo  # noqa: F401
from test_products_api import QUERY, client_db, get  # noqa: F401

from app.modules.products.query import ProductQuery
from app.modules.products.repository import ProductRepository


def test_default_filter_catalog_and_dimensions_share_one_identity_query(client_db):  # noqa: F811
    client, _, engine, _ = client_db
    selects = []

    def count_query(connection, cursor, statement, parameters, context, executemany):
        if "SELECT DISTINCT ON" in statement and "product_days" in statement:
            selects.append(statement)

    sa.event.listen(engine, "before_cursor_execute", count_query)
    # TestClient owns its own engine; inspect that application's actual SQL, not a mock.
    api_engine = client.app.state.engine
    sa.event.listen(api_engine, "before_cursor_execute", count_query)
    try:
        result = get(client, "filters", QUERY)["data"]
        assert result["product_total"] == 1
        assert len(result["products"]) == 1
        assert result["products"][0]["shop_id"] == "663599807"
        assert len(selects) == 1
    finally:
        sa.event.remove(engine, "before_cursor_execute", count_query)
        sa.event.remove(api_engine, "before_cursor_execute", count_query)


def test_catalog_reuse_isolated_by_dates_shop_and_criteria_within_snapshot(client_db):  # noqa: F811
    _, _, engine, _ = client_db
    query = ProductQuery(shop_id="663599807")
    dates = [date(2026, 10, 22)]
    with ProductRepository(engine).snapshot() as view:
        first = view.products(query, dates)
        assert len(first) == 1
        original = deepcopy(first)
        first[next(iter(first))]["product_name"] = "调用方修改不能污染复用值"
        first[next(iter(first))]["field_statuses"]["product_name"] = "changed"
        assert not view.products(query.model_copy(update={"keyword": "没有此商品"}), dates)
        assert not view.products(query.model_copy(update={"operator": "运营一"}), dates)
        other = view.products(query.model_copy(update={"shop_id": "191467219"}), dates)
        assert len(other) == 1 and next(iter(other))[1] == "191467219"
        assert not view.products(query, [date(2026, 10, 23)])
        assert view.products(query.model_copy(update={"page": 2}), dates) == original
    # 新请求没有沿用上次实例的可变缓存。
    with ProductRepository(engine).snapshot() as next_view:
        assert next_view.products(query, dates) == original
