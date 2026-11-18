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


def test_prewarmed_filter_batches_inputs_and_reuses_fixed_history_summaries(sales_pg):
    from app.api.aftersales import prewarm_aftersales_overview
    from app.modules.aftersales.read_cache import AftersalesCache

    for number in range(1, 5):
        publish(
            sales_pg,
            suffix=str(number),
            end=date(2026, 10, 24 + number),
            rules=rule_parameters(),
            previous_report_id=f"report{number - 1}" if number > 1 else None,
        )
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with client_for(sales_pg) as client:
        expected = client.get("/api/v1/aftersales/overview?category=帽子").json()["data"]
        cache = AftersalesCache()
        client.app.state.aftersales_overview_cache = cache
        prewarm_aftersales_overview(sales_pg, cache, None)
        sa.event.listen(sales_pg, "before_cursor_execute", capture)
        try:
            response = client.get("/api/v1/aftersales/overview?category=帽子")
        finally:
            sa.event.remove(sales_pg, "before_cursor_execute", capture)
        assert response.status_code == 200
        assert response.json()["data"] == expected
        assert not any(
            "FROM public.read_return_records " in sql
            or "FROM public.read_product_sales_records " in sql
            for sql in statements
        )
        input_reads = [
            sql for sql in statements if "version_inputs i" in sql and "input_role=ANY" in sql
        ]
        assert len(input_reads) == 1


def test_cached_overview_reuses_summary_but_observes_new_sync_failure(sales_pg):
    from datetime import UTC, datetime

    from migrations_v2.baseline_20261028.schema import build_metadata

    publish(sales_pg, rules=rule_parameters())
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with client_for(sales_pg) as client:
        first = client.get("/api/v1/aftersales/overview").json()
        with sales_pg.begin() as c:
            c.execute(
                build_metadata()
                .tables["public.task_runs"]
                .insert()
                .values(
                    id="failed-after-cache",
                    job_code="sync_jst_returns",
                    request_ref="synthetic",
                    lock_key="synthetic",
                    status="failed",
                    queued_at=datetime.now(UTC),
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                    error_code="SYNTHETIC_FAILURE",
                )
            )
        sa.event.listen(sales_pg, "before_cursor_execute", capture)
        try:
            response = client.get("/api/v1/aftersales/overview")
        finally:
            sa.event.remove(sales_pg, "before_cursor_execute", capture)
        assert response.status_code == 200
        value = response.json()
        assert value["data"] == first["data"]
        source = next(s for s in value["meta"]["sources"] if s["code"] == "jst_returns")
        assert source["status"] == "failed_retained"
        assert source["error_code"] == "SYNTHETIC_FAILURE"
        assert not any(
            "FROM public.read_return_records " in sql
            or "FROM public.read_product_sales_records " in sql
            for sql in statements
        )


def test_summary_is_independent_exact_and_shares_row_entry_eviction():
    from decimal import Decimal

    from app.modules.aftersales.read_cache import ReportRowsCache

    cache = ReportRowsCache(max_bytes=1024, max_entries=2)
    rows = [{"source_product_code": "001", "sales_amount": Decimal("0")}]
    expected = {
        "rows": 1,
        "first": date(2026, 10, 27),
        "last": None,
        "sales_amount": Decimal("123456789012345678901234.0000000000000000007"),
        "source_sales_qty": Decimal("0"),
        "actual_inbound_amount": None,
    }
    cache.put("v1", rows, dict(expected))
    summary = cache.get_summary("v1")
    assert summary == expected
    summary["sales_amount"] = Decimal("7")
    assert cache.get("v1") == (rows, expected)
    assert cache.get_summary("v1") == expected
    for n in range(2, 12):
        cache.put(f"v{n}", rows, expected)
        assert cache._bytes == sum(map(len, cache._entries.values()))
        assert cache._bytes <= cache.max_bytes
    assert cache.get_summary("v1") is None
    assert cache.get("v1") is None
    assert cache.get_summary("v11") == expected


def test_concurrent_cold_report_and_summary_reads_preserve_each_fixed_version(sales_pg):
    from concurrent.futures import ThreadPoolExecutor

    from app.modules.aftersales.read_cache import AftersalesCache

    publish(sales_pg, rules=rule_parameters())
    publish(
        sales_pg,
        suffix="2",
        rules=rule_parameters(),
        end=date(2026, 10, 28),
        previous_report_id="report1",
        missing=True,
    )
    scopes = [
        {"report_id": "report1", "category": "帽子"},
        {"report_id": "report2", "category": "帽子"},
        {"report_id": "report1", "keyword": "不存在"},
        {"report_id": "report2", "focus": "quality"},
    ]
    with client_for(sales_pg) as client:

        def read(scope):
            response = client.get("/api/v1/aftersales/overview", params=scope)
            assert response.status_code == 200
            return response.json()

        expected = [read(scope) for scope in scopes]
        cache = AftersalesCache()
        client.app.state.aftersales_overview_cache = cache
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(read, scopes)) == expected
            assert list(pool.map(read, scopes)) == expected
        assert cache.rows._bytes <= cache.rows.max_bytes
        assert sum(map(len, cache._entries.values())) + cache.rows._bytes <= 8 * 1024 * 1024


def test_prewarmed_meta_avoids_fact_scans_and_keeps_sync_status_live(sales_pg):
    from datetime import UTC, datetime

    from app.api.aftersales import prewarm_aftersales_overview
    from migrations_v2.baseline_20261028.schema import build_metadata

    publish(sales_pg, rules=rule_parameters())
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with client_for(sales_pg) as client:
        cold = client.get("/api/v1/aftersales/meta").json()
        prewarm_aftersales_overview(sales_pg, client.app.state.aftersales_overview_cache, None)
        sa.event.listen(sales_pg, "before_cursor_execute", capture)
        try:
            warm = client.get("/api/v1/aftersales/meta")
            assert warm.status_code == 200 and warm.json() == cold
            with sales_pg.begin() as c:
                c.execute(
                    build_metadata()
                    .tables["public.task_runs"]
                    .insert()
                    .values(
                        id="failed-after-meta-prewarm",
                        job_code="sync_jst_returns",
                        request_ref="synthetic",
                        lock_key="synthetic",
                        status="failed",
                        queued_at=datetime.now(UTC),
                        started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC),
                        error_code="SYNTHETIC_FAILURE",
                    )
                )
            response = client.get("/api/v1/aftersales/meta")
        finally:
            sa.event.remove(sales_pg, "before_cursor_execute", capture)
        assert response.status_code == 200
        value = response.json()
        source = next(s for s in value["data"]["sources"] if s["code"] == "jst_returns")
        assert source["status"] == "failed_retained"
        assert source["error_code"] == "SYNTHETIC_FAILURE"
        assert value["data"]["data_version"] == cold["data"]["data_version"]
        assert value["data"]["observed_to"] == cold["data"]["observed_to"]
        fact_reads = [
            sql
            for sql in statements
            if "FROM public.read_return_records " in sql
            or "FROM public.read_product_sales_records " in sql
        ]
        assert len(fact_reads) == 0
