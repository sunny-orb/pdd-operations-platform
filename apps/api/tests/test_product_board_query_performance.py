"""真实PG风险查询：最小必要日期窗与原全窗规则结果逐字段对照。"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board import query
from app.modules.product_board.repository import snapshot, wire
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


@pytest.fixture(params=[False, True], autouse=True)
def content_storage_mode(request, monkeypatch):
    """两种物理表示都必须遵守相同的字段/日期/行数读取边界。"""
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(request.param).lower())


@pytest.mark.parametrize("offset,days", [(0, 1), (0, 31), (0, 60), (35, 1)])
def test_risks_only_read_period_and_baseline_without_changing_full_scope_result(
    sales_pg, tmp_path, offset, days
):
    end, sources = candidate()
    raw = [
        dict(
            z_id=1 + i * 3 + p,
            shop_id="663599807",
            spu_id=str(123 + p),
            spu_name=f"商品{p}" if i < 20 else f"历史名{p}",
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount=str(100 - i) if i != 10 else None,
            product_uv=100 if i else 5,
            transaction_orders=10 if i else 0,
        )
        for i in range(60)
        for p in range(3)
        if not (p == 2 and i == 3)
    ]
    sources["product_daily"] = normalize("product_daily", raw, end)
    sources["profile_records"] = normalize(
        "profile_records",
        [dict(__id=p + 1, spigf=str(123 + p), cpdj="A级", pl="衣") for p in range(3)],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, sources))
    finish = end - timedelta(days=offset)
    start = finish - timedelta(days=days - 1)
    q = query.BoardQuery(start, finish)
    with snapshot(sales_pg) as repo:
        info, groups, _, rows, period_info = query.scope(repo, q, with_members=True)
        expected, counts = query.collect_risks(
            info, groups, query.risk_top_products(period_info, rows, q), q
        )

    fetched = []
    projections = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        if "FROM public.read_product_daily_facts" in statement and "product_revenue" in statement:
            fetched.append(cursor.rowcount)
            projections.append(statement.split(" FROM ", 1)[0])

    event.listen(sales_pg, "after_cursor_execute", record_sql)
    try:
        with snapshot(sales_pg) as repo:
            result = query.risk_feed(repo, q)
            assert result["risks"] == expected
            assert result["counts"] == counts
            assert result["period"] == {"start": start, "end": finish}
            assert result["latest_data_date"] == end
    finally:
        event.remove(sales_pg, "after_cursor_execute", record_sql)
    needed = max(end - timedelta(days=59), min(start, finish - timedelta(days=7)))
    expected_rows = sum(needed.isoformat() <= row["dt"] <= finish.isoformat() for row in raw)
    assert fetched and sum(fetched) == expected_rows
    assert all(
        field not in projection
        for projection in projections
        for field in ("views", "favorites", "buyers", "units", "source_payload")
    ), "风险只读取身份、日期、访客、订单和交易额，其他度量不参与规则"


@pytest.mark.parametrize("keyword,count", [("糖豆", "0"), ("新名字", "1")])
def test_keyword_overview_reads_measures_only_for_possible_matches(
    sales_pg, tmp_path, keyword, count
):
    end, sources = candidate()
    raw = [
        dict(
            z_id=f"{i}-{p}",
            shop_id="663599807",
            spu_id=str(2000 + p),
            spu_name=("糖豆" if i > 15 else "新名字") if p == 0 else f"其他{p}",
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount=str(100 - p),
            product_uv=100,
            transaction_orders=2,
        )
        for i in range(65)
        for p in range(12)
    ]
    sources["product_daily"] = normalize("product_daily", raw, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, sources))
    start = end - timedelta(days=30)
    q = query.BoardQuery(start, end, keyword=keyword)
    with snapshot(sales_pg) as repo:
        expected = wire(query.overview(repo, q))
        token = repo.token
    assert expected["product_count"] == count
    if keyword == "糖豆":
        assert expected["windows"][2]["metrics"]["revenue"]["previous"] is not None
    rows_read = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        if (
            "FROM public.read_product_daily_facts" in statement
            and "visitors,favorites" in statement
        ):
            rows_read.append(cursor.rowcount)

    event.listen(sales_pg, "after_cursor_execute", record_sql)
    try:
        app = create_app(Settings(_env_file=None, database_url=""))
        with TestClient(app) as client:
            app.state.engine = sales_pg
            response = client.get(
                "/api/v1/products/overview",
                params=dict(
                    date_from=start,
                    date_to=end,
                    keyword=q.keyword,
                    data_version=token,
                ),
            )
        assert response.status_code == 200
        assert response.json()["data"] == expected
    finally:
        event.remove(sales_pg, "after_cursor_execute", record_sql)
    assert rows_read and sum(rows_read) <= 65
