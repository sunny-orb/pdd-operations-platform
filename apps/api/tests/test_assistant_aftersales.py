"""助手售后适配的真实隔离PG验收：固定报告、完整分母与有界结果。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.modules.aftersales.classification import rule_parameters
from app.modules.product_board.repository import BoardError
from tests.test_aftersales_api import publish

pytest_plugins = ["tests.sales_pg_support"]


def adapter():
    from app.modules.assistant import aftersales_tools

    return aftersales_tools


def facts(result):
    return {item["key"]: item["value"] for item in result["facts"]}


def seed(engine):
    publish(engine, suffix="yoy", start=date(2025, 9, 28), end=date(2025, 10, 27))
    publish(engine, suffix="previous", start=date(2026, 9, 21), end=date(2026, 10, 20))
    publish(
        engine,
        rules=rule_parameters(risk_available=True),
        previous_report_id="reportprevious",
        yoy_report_id="reportyoy",
    )


def assert_scalar_tables(result):
    for table in result["tables"]:
        assert len(table["rows"]) <= 25
        assert isinstance(table["total"], int)
        assert all(
            not isinstance(value, (dict, list, tuple))
            for row in table["rows"]
            for value in row.values()
        )
        assert all(
            col["unit"] in {"currency", "number", "percent", "ratio", "text"}
            for col in table["columns"]
        )


def test_describe_exposes_real_report_period_and_bounded_catalog(sales_pg):
    seed(sales_pg)
    result = adapter().describe(sales_pg)
    assert result["domain"] == "aftersales"
    assert (result["date_from"], result["date_to"]) == ("2026-09-28", "2026-10-27")
    assert result["default_report_id"] == "report1"
    assert {r["id"] for r in result["reports"]} == {"report1", "reportprevious", "reportyoy"}
    assert result["categories"] == ["帽子"]
    assert result["reports_total"] == 3
    assert result["categories_total"] == 1


def test_overview_preserves_full_scope_decimal_and_read_only(sales_pg):
    seed(sales_pg)
    before = None
    with sales_pg.connect() as c:
        before = c.scalar(sa.text("SELECT count(*) FROM task_runs"))
    result = adapter().read(sales_pg, "aftersales_overview", {"limit": 1, "offset": 1})
    values = facts(result)
    assert result["period"] == {"date_from": "2026-09-28", "date_to": "2026-10-27"}
    assert result["scope"]["report_id"] == "report1"
    assert Decimal(values["returned_qty"]) == 4
    assert Decimal(values["gmv"]) == 145
    assert Decimal(values["inbound_amount"]) == 15
    assert values["total_rate"] == "0.34482758620689655172413793103448275862068965517241"
    assert result["domain"] == "aftersales" and result["source"]
    assert_scalar_tables(result)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs")) == before


@pytest.mark.parametrize(
    "view,total", [("category", 1), ("style", 3), ("mom", 12), ("yoy", 12),
                   ("quality", 1), ("history", 3)]
)
def test_all_six_views_keep_totals_and_scalar_cells(sales_pg, view, total):
    seed(sales_pg)
    result = adapter().read(
        sales_pg, "aftersales_analysis", {"view": view, "limit": 2, "offset": 1}
    )
    table = result["tables"][0]
    assert table["total"] == total
    assert table["offset"] == 1
    assert len(table["rows"]) == min(2, max(total - 1, 0))
    assert result["scope"]["view"] == view
    assert_scalar_tables(result)


def test_filter_preserves_missing_amount_and_rule_gate(sales_pg):
    publish(sales_pg, missing=True)
    result = adapter().read(sales_pg, "aftersales_overview", {"category": "帽子"})
    assert facts(result)["inbound_amount"] is None
    assert facts(result)["total_rate"] is None
    assert facts(result)["gmv"] == "145"
    focused = adapter().read(sales_pg, "aftersales_analysis", {"focus": "quality"})
    assert focused["tables"][0]["rows"] == []
    assert any("质量" in note and "筛选" in note for note in focused["notes"])
    unmatched = adapter().read(sales_pg, "aftersales_overview", {"keyword": "不存在的款"})
    assert facts(unmatched)["returned_qty"] is None


def test_selected_history_and_stale_version_are_not_replaced(sales_pg):
    seed(sales_pg)
    old = adapter().describe(sales_pg)["data_version"]
    result = adapter().read(sales_pg, "aftersales_overview", {"report_id": "reportyoy"})
    assert result["period"] == {"date_from": "2025-09-28", "date_to": "2025-10-27"}
    publish(sales_pg, suffix="next", end=date(2026, 10, 28))
    with pytest.raises(BoardError) as error:
        adapter().read(sales_pg, "aftersales_overview", {"data_version": old})
    assert error.value.code == "DATA_VERSION_CHANGED" and error.value.status == 409
    with pytest.raises(BoardError) as error:
        adapter().read(sales_pg, "aftersales_overview", {"report_id": "invented"})
    assert error.value.code == "REPORT_NOT_FOUND"


def test_no_report_does_not_report_zero(sales_pg):
    description = adapter().describe(sales_pg)
    assert description["reports"] == [] and description["date_to"] is None
    result = adapter().read(sales_pg, "aftersales_overview", {})
    assert result["period"] is None
    assert all(item["value"] is None for item in result["facts"] if item["unit"] != "text")
    assert any("报告" in note for note in result["notes"])


@pytest.mark.parametrize("view", ["category", "style", "mom", "yoy", "quality", "history"])
def test_all_views_preserve_no_report_as_unavailable(sales_pg, view):
    result = adapter().read(sales_pg, "aftersales_analysis", {"view": view})
    assert result["period"] is None
    assert result["tables"][0]["total"] == 0
    assert result["tables"][0]["rows"] == []
    assert any("unavailable" in note for note in result["notes"])


@pytest.mark.parametrize(
    "params",
    [{"limit": 26}, {"offset": -1}, {"limit": True}, {"focus": "invented"},
     {"view": "invented"}, {"date_from": "2026-01-01", "date_to": "2026-01-07"},
     {"listing_id": "platform-listing"}, {"shop_id": "shop"}, {"category": ["帽子"]}],
)
def test_invalid_scope_and_unmapped_product_do_not_query_broader_scope(params):
    with pytest.raises(BoardError) as error:
        adapter().read(None, "aftersales_overview", params)
    assert error.value.status == 422


def test_options_are_paged_without_losing_catalog_total(sales_pg):
    seed(sales_pg)
    result = adapter().read(sales_pg, "aftersales_options", {"limit": 1, "offset": 1})
    reports = result["tables"][0]
    assert reports["total"] == 3 and reports["offset"] == 1
    assert reports["rows"][0]["id"] == "reportprevious"
    assert len(reports["rows"]) == 1
    assert_scalar_tables(result)


def test_non_aligned_offset_spans_domain_pages_without_losing_rows(sales_pg):
    returns = [
        dict(
            id=f"return-{i}", source_row_key=f"row-{i:02}", source_product_code=f"sku-{i}",
            source_style_code=f"style-{i:02}", actual_returned_qty=Decimal(i),
            actual_inbound_amount=Decimal(i), source_return_reason="质量问题，商品破损",
        )
        for i in range(1, 31)
    ]
    publish(sales_pg, returns=returns, rules=rule_parameters())
    result = adapter().read(
        sales_pg, "aftersales_analysis", {"view": "style", "limit": 10, "offset": 23}
    )
    rows = result["tables"][0]["rows"]
    assert result["tables"][0]["total"] == 32  # 30退货款＋2个独立销售组
    assert len(rows) == 9
    assert [r["rank"] for r in rows] == [str(i) for i in range(24, 33)]
    assert len({r["id"] for r in rows}) == 9
    assert_scalar_tables(result)
    overview = adapter().read(sales_pg, "aftersales_overview", {"limit": 1})
    assert facts(overview)["returned_qty"] == "465"
    assert facts(overview)["quality_qty"] == "465"  # 多标签质量原行只计一次


def test_report_catalog_limit_can_be_continued_and_selected(sales_pg):
    for i in range(27):
        end = date(2026, 10, 27) - timedelta(days=i)
        publish(sales_pg, suffix=f"catalog-{i}", end=end, start=end - timedelta(days=29))
    first = adapter().describe(sales_pg)
    assert first["reports_total"] == 27 and len(first["reports"]) == 25
    rest = adapter().read(sales_pg, "aftersales_options", {"offset": 25, "limit": 25})
    assert rest["tables"][0]["total"] == 27
    assert [r["id"] for r in rest["tables"][0]["rows"]] == [
        "reportcatalog-25", "reportcatalog-26"
    ]
    historical = adapter().read(
        sales_pg, "aftersales_overview", {"report_id": "reportcatalog-26"}
    )
    assert historical["period"] == {"date_from": "2026-09-02", "date_to": "2026-10-01"}


def test_rpa_zero_denominator_and_bound_targets_remain_explicit(sales_pg, tmp_path):
    from app.modules.aftersales.rpa_publisher import sync_rpa
    from app.modules.aftersales.targets import publish_targets
    from tests.test_aftersales_rpa import delivery
    from tests.test_aftersales_targets import config

    publish_targets(sales_pg, config(), tmp_path)
    rows = delivery()
    for row in rows:
        for key in ("实退数量", "实际入库金额", "实发金额", "销售金额"):
            if key in row["json_data"]:
                row["json_data"][key] = "0"
    sync_rpa(sales_pg, lambda: rows, tmp_path)
    result = adapter().read(sales_pg, "aftersales_overview", {})
    values = facts(result)
    assert values["returned_qty"] == "0" and values["gmv"] == "0"
    assert values["inbound_amount"] == "0"
    assert values["total_rate"] is None and values["aftersale_rate"] is None
    targets = next(t for t in result["tables"] if t["title"] == "整体目标区间")
    assert [r["upper_rate"] for r in targets["rows"]] == ["0.43", "0.36"]
    assert all(r["current"] is None and r["state"] == "metric_unavailable"
               for r in targets["rows"])
    assert "RPA" in result["source"]
    filtered = adapter().read(sales_pg, "aftersales_overview", {"category": "童帽"})
    targets = next(t for t in filtered["tables"] if t["title"] == "整体目标区间")
    assert all(r["state"] == "scope_not_applicable" for r in targets["rows"])
