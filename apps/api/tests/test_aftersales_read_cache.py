"""固定报告筛选读取复用：真实PG隔离、版本失效及金额精度。"""

from datetime import date

import sqlalchemy as sa

from app.modules.aftersales.classification import rule_parameters
from tests.test_aftersales_api import client_for, publish

pytest_plugins = ["tests.sales_pg_support"]


def test_filter_reuses_fixed_rows_without_fact_scan_and_preserves_result(sales_pg):
    publish(sales_pg, rules=rule_parameters())
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(sales_pg, "before_cursor_execute", capture)
    try:
        with client_for(sales_pg) as client:
            first = client.get("/api/v1/aftersales/analysis?view=style").json()["data"]
            statements.clear()
            selected = client.get("/api/v1/aftersales/analysis?view=style&keyword=完全不存在")
            assert selected.status_code == 200
            assert selected.json()["data"]["total"] == 0
            assert not any("GROUP BY source_shop_id" in sql for sql in statements)
            assert not any(
                "FROM public.read_return_records " in sql
                or "FROM public.read_product_sales_records " in sql
                for sql in statements
            )
            assert client.get("/api/v1/aftersales/analysis?view=style").json()["data"] == first
            version = client.get("/api/v1/aftersales/meta").json()["meta"]["data_version"]
            publish(sales_pg, suffix="2", end=date(2026, 10, 28), missing=True)
            assert (
                client.get(
                    "/api/v1/aftersales/analysis", params={"data_version": version}
                ).status_code
                == 409
            )
            old = client.get("/api/v1/aftersales/analysis?report_id=report1&view=style")
            assert old.json()["data"] == first
            fresh = client.get("/api/v1/aftersales/overview").json()["data"]
            assert (
                next(m["value"] for m in fresh["metrics"] if m["key"] == "inbound_amount") is None
            )
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", capture)


def test_codec_roundtrip_preserves_null_precision_dates_and_isolation():
    from decimal import Decimal

    from app.modules.aftersales.read_cache import ReportRowsCache

    cache = ReportRowsCache(max_bytes=4096, max_entries=2)
    original = [
        {
            "actual_inbound_amount": Decimal("123456789012345678901234.0000000000000000007"),
            "actual_returned_qty": Decimal("0"),
            "sales_amount": None,
            "_absolute_actual_inbound_amount": Decimal(
                "123456789012345678901234.0000000000000000007"
            ),
            "source_product_code": "001",
            "period_start": date(2026, 1, 1),
            "period_end": None,
            "_record_count": 3,
            "_rpa": True,
        }
    ]
    cache.put("v1", original)
    hit, _ = cache.get("v1")
    assert hit == original
    hit[0]["actual_inbound_amount"] = Decimal(0)
    assert cache.get("v1") == (original, None)
    cache.put("v2", [])
    assert cache.get("v2") == ([], None)
    cache.put("v3", original)
    assert cache.get("v1") is None
    assert cache._bytes <= cache.max_bytes


def test_total_byte_budget_evicts_and_oversized_input_is_not_cached():
    from app.modules.aftersales.read_cache import ReportRowsCache

    cache = ReportRowsCache(max_bytes=140, max_entries=24)
    for n in range(20):
        cache.put(n, [{"source_product_code": f"product-{n:08d}"}])
        assert cache._bytes <= 140
    assert cache.get(0) is None
    assert cache.get(19) == ([{"source_product_code": "product-00000019"}], None)
    cache.MAX_RAW_BYTES = 5
    cache.put("huge", [{"source_product_code": "cannot-fit"}])
    assert cache.get("huge") is None


def test_cached_results_equal_uncached_across_filters_history_and_comparisons(sales_pg):
    from app.modules.aftersales.analysis import analysis
    from app.modules.aftersales.query import AfterQuery, overview
    from app.modules.aftersales.read_cache import ReportRowsCache
    from app.modules.aftersales.repository import snapshot

    publish(sales_pg, rules=rule_parameters())
    publish(
        sales_pg,
        suffix="2",
        rules=rule_parameters(),
        end=date(2026, 10, 28),
        previous_report_id="report1",
        yoy_report_id="report1",
    )
    cache = ReportRowsCache()
    for report in (None, "report1"):
        for scope in (
            {},
            {"keyword": "尺码"},
            {"category": "帽子"},
            {"focus": "quality"},
            {"keyword": "不存在"},
        ):
            for view in ("category", "style", "mom", "yoy", "quality", "history"):
                q = AfterQuery(report_id=report, view=view, **scope)
                with snapshot(sales_pg, report) as repo:
                    expected = (overview(repo, q), analysis(repo, q), repo.metadata())
                with snapshot(sales_pg, report, rows_cache=cache) as repo:
                    actual = (overview(repo, q), analysis(repo, q), repo.metadata())
                assert actual == expected, (report, scope, view)


def test_summary_budget_is_total_not_per_filter_and_large_reports_still_fit():
    from app.modules.aftersales.read_cache import AftersalesCache

    cache = AftersalesCache()
    for n in range(8):
        cache.put(("result", n), b"x" * (800 * 1024))
    assert sum(map(len, cache._entries.values())) <= 4 * 1024 * 1024
    assert cache.get(("result", 0)) is None
    assert len(cache.get(("result", 7))) == 800 * 1024
