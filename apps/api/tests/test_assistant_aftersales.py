"""助手售后适配的真实隔离PG验收：固定报告、完整分母与有界结果。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.modules.aftersales.classification import rule_parameters
from app.modules.aftersales.rpa_publisher import sync_rpa
from app.modules.aftersales.targets import publish_targets
from app.modules.product_board.repository import BoardError
from tests.test_aftersales_api import publish
from tests.test_aftersales_rpa import delivery
from tests.test_aftersales_targets import config

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
    target_gap = next(item for item in result["facts"] if item["key"] == "risk_target_gap")
    assert target_gap["value"] is None and "没有可比较的整体目标" in target_gap["reason"]
    assert "总退款率" in target_gap["reason"]
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
    assert all("实际值不可用" in row.get("comparison", "") for row in targets["rows"])
    assert "RPA" in result["source"]


def test_bound_target_directions_use_report_rates_and_percentage_points(sales_pg, tmp_path):
    publish_targets(sales_pg, config(), tmp_path)
    previous = delivery(date(2026, 11, 16), 100)
    previous[1]["json_data"]["实际入库金额"] = "24.725"
    current = delivery(date(2026, 11, 23), 200)
    current[1]["json_data"]["实际入库金额"] = "27.0125"
    sync_rpa(sales_pg, lambda: previous + current, tmp_path)

    result = adapter().read(sales_pg, "aftersales_overview", {})
    assert result["period"] == {"date_from": "2026-10-25", "date_to": "2026-11-23"}
    by_key = {item["key"]: item for item in result["facts"]}
    assert Decimal(by_key["total_rate"]["value"]) == Decimal("0.4161")
    gap = by_key["risk_target_gap"]
    assert Decimal(gap["value"]) == Decimal("-1.39") and gap["unit"] == "number"
    assert "上界" in gap["label"] and "百分点" in gap["label"]
    change = by_key["risk_change_pp"]
    assert Decimal(change["value"]) == Decimal("1.83") and change["unit"] == "number"
    assert "总退款率" in change["label"] and "较上期" in change["label"]
    assert "总退款率高于上期 1.83 个百分点" in change["reason"]

    targets = next(t for t in result["tables"] if t["title"] == "整体目标区间")
    total, after = targets["rows"]
    assert total["state"] == "within_range" and after["state"] == "below_range"
    for phrase in ("区间内", "40%–43%", "高于下界 1.61 个百分点", "低于上界 1.39 个百分点"):
        assert phrase in total["comparison"]
    assert total["comparison"] == gap["reason"]
    assert "售后退款率" in after["comparison"] and "低于下界 4.99 个百分点" in after["comparison"]

    for filters in ({"category": "童帽"}, {"keyword": "K01"}, {"focus": "quality"}):
        filtered = adapter().read(sales_pg, "aftersales_overview", filters)
        assert facts(filtered)["total_rate"] is not None
        targets = next(t for t in filtered["tables"] if t["title"] == "整体目标区间")
        assert len(targets["rows"]) == 2
        for row in targets["rows"]:
            assert row["state"] == "scope_not_applicable" and "不适用" in row["comparison"]
            assert row["current"] is None and row["upper_gap_pp"] is None


def test_closed_target_bounds_and_directions_survive_display_rounding(sales_pg, tmp_path):
    publish_targets(sales_pg, config(), tmp_path)
    # 每期实发100元、取消25元。按指定的入库金额核对各期整体总退款率。
    examples = [
        (date(2026, 10, 19), "25", "within_range", ("区间内", "等于下界")),
        (date(2026, 10, 26), "28.75", "within_range", ("区间内", "等于上界")),
        (date(2026, 10, 31), "30", "above_range", ("高于上界 1.00 个百分点",)),
        (date(2026, 11, 2), "23.75", "below_range", ("低于下界 1.00 个百分点",)),
        (date(2026, 11, 9), "28.75125", "above_range", ("高于上界", "不足 0.01 个百分点")),
    ]
    rows = []
    for i, (end, inbound, _, _) in enumerate(examples):
        sample = delivery(end, i * 100)
        sample[1]["json_data"]["实际入库金额"] = inbound
        if end == date(2026, 10, 31):
            for row in sample:
                row.update(time_granularity="monthly", start_date=date(2026, 10, 1))
        rows.extend(sample)
    sync_rpa(sales_pg, lambda: rows, tmp_path)
    catalog = adapter().describe(sales_pg)
    report_ids = {r["date_to"]: r["id"] for r in catalog["reports"]}
    for end, _, state, phrases in examples:
        report = adapter().read(
            sales_pg, "aftersales_overview", {"report_id": report_ids[str(end)]}
        )
        target = next(t for t in report["tables"] if t["title"] == "整体目标区间")["rows"][0]
        assert target["state"] == state
        for phrase in phrases:
            assert phrase in target.get("comparison", "")


def test_shared_report_input_cache_preserves_answers_and_reduces_sql(sales_pg):
    from sqlalchemy import event

    from app.modules.aftersales.read_cache import ReportRowsCache

    seed(sales_pg)
    cache = ReportRowsCache()
    statements = []
    def count(connection, cursor, statement, parameters, context, executemany):
        statements.append(1)
    event.listen(sales_pg, "after_cursor_execute", count)
    try:
        first = adapter().read(sales_pg, "aftersales_overview", {}, rows_cache=cache)
        first_count = len(statements)
        statements.clear()
        second = adapter().read(sales_pg, "aftersales_overview", {}, rows_cache=cache)
        assert first == second
        assert len(statements) < first_count
        assert adapter().describe(sales_pg, rows_cache=cache) == adapter().describe(sales_pg)
    finally:
        event.remove(sales_pg, "after_cursor_execute", count)
