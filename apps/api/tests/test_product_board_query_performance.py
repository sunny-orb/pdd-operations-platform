"""真实PG风险查询：最小必要日期窗与原全窗规则结果逐字段对照。"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board import query
from app.modules.product_board.repository import snapshot, wire
from app.modules.product_board.source import identity, normalize
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


@pytest.mark.parametrize(
    "keyword,products,count,expected_fact_rows",
    [
        pytest.param("糖豆", (), "0", 65, id="previous-name"),
        pytest.param("新名字", (), "1", 65, id="current-name"),
        pytest.param("", ("2000", "2001"), "2", 130, id="multi-select"),
        pytest.param("新名字", ("2000", "2001"), "1", 130, id="combined"),
    ],
)
def test_filtered_overview_reads_measures_only_for_possible_matches(
    sales_pg, tmp_path, keyword, products, count, expected_fact_rows
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
    q = query.BoardQuery(
        start, end, products=tuple(identity("663599807", p) for p in products), keyword=keyword
    )
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
                params={
                    "date_from": start,
                    "date_to": end,
                    "keyword": q.keyword,
                    "listing_ids": q.products,
                    "data_version": token,
                },
            )
        assert response.status_code == 200
        assert response.json()["data"] == expected
    finally:
        event.remove(sales_pg, "after_cursor_execute", record_sql)
    assert rows_read and sum(rows_read) <= expected_fact_rows


def test_repeated_dimension_query_unions_values_and_narrows_facts(sales_pg, tmp_path):
    end, sources = candidate()
    sources["product_daily"] = normalize(
        "product_daily",
        [
            dict(
                z_id=f"{i}-{p}",
                shop_id=shop,
                spu_id=str(2000 + p),
                spu_name=f"商品{p}",
                dt=str(end - timedelta(days=i)),
                time_granularity="day",
                transaction_amount="50",
                product_uv=100,
                transaction_orders=2,
            )
            for i in range(60)
            for p, shop in enumerate(("663599807", "799193231", "191467219"))
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, sources))
    start = end - timedelta(days=30)
    with snapshot(sales_pg) as repo:
        token = repo.token
    reads = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        if (
            "FROM public.read_product_daily_facts" in statement
            and "visitors,favorites" in statement
        ):
            reads.append(cursor.rowcount)

    event.listen(sales_pg, "after_cursor_execute", record_sql)
    try:
        app = create_app(Settings(_env_file=None, database_url=""))
        with TestClient(app) as client:
            app.state.engine = sales_pg
            response = client.get(
                "/api/v1/products/overview",
                params=[
                    ("date_from", start),
                    ("date_to", end),
                    ("shop_id", "663599807"),
                    ("shop_id", "799193231"),
                    ("data_version", token),
                ],
            )
        assert response.status_code == 200
        assert response.json()["data"]["product_count"] == "2"
    finally:
        event.remove(sales_pg, "after_cursor_execute", record_sql)
    assert reads and sum(reads) == 120
    with snapshot(sales_pg) as repo:
        expected = wire(
            query.overview(repo, query.BoardQuery(start, end, shop=("663599807", "799193231")))
        )
    assert response.json()["data"] == expected


def test_overview_skips_storage_row_identity_but_detail_keeps_stable_ids(sales_pg, tmp_path):
    """汇总不构造共享事实的长行ID；逐日分页仍使用原来的稳定ID。"""
    from app.modules.product_board.source import previous_year

    end, sources = candidate(duplicate_profile=False)
    dates = [end - timedelta(days=i) for i in range(62)]
    dates += [previous_year(end - timedelta(days=i)) for i in range(31)]
    sources["product_daily"] = normalize(
        "product_daily",
        [
            dict(
                z_id=i + 1,
                shop_id="663599807",
                spu_id="123",
                dt=str(day),
                time_granularity="day",
                transaction_amount="100",
                product_uv=100,
                transaction_users=10,
                transaction_orders=10,
                transaction_products=10,
                favorite_product_users=2,
            )
            for i, day in enumerate(dates)
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, sources))
    projections = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM public.read_product_daily_facts" in statement and "product_revenue" in statement:
            projections.append(statement.split(" FROM ", 1)[0].removeprefix("SELECT ").split(","))

    event.listen(sales_pg, "after_cursor_execute", capture)
    try:
        with snapshot(sales_pg) as repo:
            result = query.overview(repo, query.BoardQuery(end - timedelta(days=30), end))
            assert result["metrics"]["revenue"]["value"] == 3100
            assert result["metrics"]["revenue"]["previous"] == 3100
            assert result["metrics"]["revenue"]["previous_period"] == 3100
    finally:
        event.remove(sales_pg, "after_cursor_execute", capture)
    with snapshot(sales_pg) as repo:
        ids = list(repo.profiles())
        first, count = repo.page_rows(end - timedelta(days=30), end, ids, 1, 25)
        again, _ = repo.page_rows(end - timedelta(days=30), end, ids, 1, 25)
        assert count == 31 and len(first) == 25
        assert len({r["id"] for r in first}) == 25
        assert [r["id"] for r in first] == [r["id"] for r in again]
    assert projections and all("id" not in columns for columns in projections), (
        "汇总不读取未使用的存储行ID"
    )


def test_sql_strict_daily_summaries_match_original_rows(sales_pg, tmp_path):
    """只读日摘要保留空期、负值、缺项、精度及日期边界，不运输整窗原行。"""
    from decimal import Decimal

    from app.modules.product_board.comparison import summarize_period, summarize_window

    end, sources = candidate(duplicate_profile=False)
    raw = [
        dict(
            z_id=1 + i * 2 + j,
            shop_id="663599807",
            spu_id=str(123 + j),
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount="100.01",
            product_uv=100,
            transaction_users=10,
            transaction_products=2,
            favorite_product_users=3,
        )
        for i in range(120)
        for j in range(2)
    ]
    raw[0]["transaction_amount"] = None
    raw[2]["transaction_amount"] = "-1"
    raw[4]["product_uv"] = None
    raw[6]["transaction_amount"] = (
        "1000000000000000000000000000000000000000000000000000000000000.03"
    )
    sources["product_daily"] = normalize("product_daily", raw, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, sources))
    with snapshot(sales_pg) as repo:
        first = end - timedelta(days=119)
        rows = repo.rows(first, end)
        summaries = repo.period_summaries(first, end)
        assert len(summaries) == 120 and sum(s.row_count for s in summaries.values()) == 240
        for offset, days in ((0, 1), (0, 58), (58, 58), (3, 1), (1, 1), (120, 7)):
            finish = end - timedelta(days=offset)
            start = finish - timedelta(days=days - 1)
            expected = summarize_period([r for r in rows if start <= r["business_date"] <= finish])
            assert summarize_window(summaries, start, finish) == expected
        assert summaries[end].totals["product_revenue"] is None
        assert summaries[end - timedelta(days=2)].totals["visitors"] is None
        assert summaries[end - timedelta(days=3)].totals["product_revenue"] == Decimal(
            "1000000000000000000000000000000000000000000000000000000000100.04"
        )


def test_overview_transport_deduplicates_identity_without_changing_rows(sales_pg, tmp_path):
    """原行顺序、中文/转义名称、大整数、Decimal及NULL均完整往返。"""
    from decimal import Decimal

    end, sources = candidate(duplicate_profile=False)
    raw = [
        dict(
            z_id=1 + i * 3 + j,
            shop_id="663599807",
            shop_name='店名\n"A"',
            spu_id=str(123 + j),
            spu_name="糖豆\\帽" if i % 2 else "商品😀",
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount="100.000001",
            product_pv=100,
            product_uv=9007199254740997,
            transaction_users=10,
            transaction_orders=3,
            transaction_products=2,
            favorite_product_users=None if i == 1 else 4,
        )
        for i in range(62)
        for j in range(3)
    ]
    raw[0]["transaction_amount"] = (
        "1000000000000000000000000000000000000000000000000000000000000.03"
    )
    sources["product_daily"] = normalize("product_daily", raw, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, sources))
    reads = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM public.read_product_daily_facts" in statement:
            reads.append(cursor.rowcount)

    with snapshot(sales_pg) as repo:
        start = end - timedelta(days=61)
        expected = [{k: v for k, v in row.items() if k != "id"} for row in repo.rows(start, end)]
        event.listen(sales_pg, "after_cursor_execute", capture)
        try:
            actual = repo.overview_rows(start, end)
        finally:
            event.remove(sales_pg, "after_cursor_execute", capture)
        assert actual == expected
        assert actual[-3]["product_revenue"] == Decimal(raw[0]["transaction_amount"])
        assert actual[0]["visitors"] == 9007199254740997
        assert repo.overview_rows(start, end, listing_ids=[]) == []
    assert reads and sum(reads) < len(actual), "总览只运输不重复的身份及分组原值"
