"""销售/天气/状态助手契约：仅合成输入，读取真实隔离PostgreSQL。"""

import json

import pytest
from sqlalchemy import event

from app.modules.assistant import sales_tools
from app.modules.product_board.repository import BoardError
from app.modules.sales.contracts import SalesError
from tests.test_sales_extended_api import publish, setup
from tests.test_sales_extended_sources import sample
from tests.test_sales_targets import SalesTargetStore, candidate
from tests.test_weather import sources

pytest_plugins = ["tests.sales_pg_support"]


@pytest.fixture
def prepared(sales_pg, tmp_path):
    setup(sales_pg, tmp_path)
    for code in (
        "shop_ratings",
        "shop_regions",
        "product_regions",
        "shop_refunds",
        "shop_channel_product_promotion",
        "shop_channel_star_shop",
    ):
        source_rows = [sample(code)]
        if code == "shop_regions":
            source_rows.append(sample(code, id="raw-002", province="安徽"))
        publish(sales_pg, tmp_path, code, source_rows)
    SalesTargetStore(sales_pg, archive_root=tmp_path / "targets").publish_candidate(candidate())
    return sales_pg


def args(engine, **changes):
    return dict(data_version=sales_tools.describe(engine)["data_version"], **changes)


def assert_report(value):
    json.dumps(value, ensure_ascii=False)
    assert value["kind"] == "dashboard"
    assert value["source"] and not value["source"].isascii()
    for table in value["tables"]:
        assert len(table["rows"]) <= 25
        assert all(
            not isinstance(cell, (list, dict)) for row in table["rows"] for cell in row.values()
        )


def test_describe_observed_dates_and_identity(prepared):
    info = sales_tools.describe(prepared)
    assert info["date_from"] == info["date_to"] == "2026-10-26"
    assert info["shops"][0]["id"] == "spigf"
    assert info["shops"][0]["shop_source_id"] == "663599807"
    json.dumps(info)


def test_all_sales_capabilities_read_domain_values(prepared):
    p = args(prepared)
    for capability in sales_tools.CAPABILITIES:
        if not capability.startswith("sales_"):
            continue
        value = sales_tools.read(prepared, capability, {**p, "month": "2026-10", "limit": 1})
        assert_report(value)
        assert value["domain"] == "sales" and value["data_version"] == p["data_version"]
    perf = sales_tools.read(prepared, "sales_performance", p)
    facts = {f["key"]: f for f in perf["facts"]}
    assert facts["shop_revenue"]["value"] == "100.00"
    assert facts["buyer_conversion"]["value"] == "0.200000000000"
    assert facts["buyer_conversion"]["unit"] == "percent"
    assert facts["promotion_roi"]["unit"] == "ratio"
    channels = perf["tables"][0]["rows"]
    assert channels[0]["code"] == "shop_channel_product_promotion"
    assert channels[0]["state"] == "observed"
    assert channels[0]["channel"] == "商品推广"
    targets = sales_tools.read(prepared, "sales_targets", {**p, "month": "2026-10"})
    assert targets["period"]["date_from"] == "2026-10-01"
    assert {f["key"]: f["value"] for f in targets["facts"]}["month_target"] == "25000.25"
    regions = sales_tools.read(prepared, "sales_regions", {**p, "limit": 1})
    assert regions["tables"][0]["rows"][0]["transaction_amount"] == "100.01"
    assert regions["tables"][0]["total"] == 2
    assert {f["key"]: f["value"] for f in regions["facts"]}["transaction_amount"] == "200.02"
    options = sales_tools.read(prepared, "sales_product_options", {**p, "keyword": "雨具"})
    product = options["tables"][0]["rows"][0]["key"]
    pr = sales_tools.read(prepared, "sales_product_regions", {**p, "product_key": product})
    assert {f["key"]: f["value"] for f in pr["facts"]}["actual_shipped_amount"] == "100.01"


def test_pagination_does_not_change_totals_and_shop_scope(prepared):
    p = args(prepared)
    shops = sales_tools.read(prepared, "sales_shops", {**p, "offset": 10, "limit": 1})
    assert shops["tables"][0]["total"] == 6
    assert shops["tables"][0]["rows"] == []
    only = sales_tools.read(prepared, "sales_shops", {**p, "shop_id": "spigf"})
    assert only["tables"][0]["total"] == 1
    trend = sales_tools.read(prepared, "sales_trend", {**p, "view": "promotion_roi"})
    assert len(trend["series"]) == 1
    assert trend["series"][0]["key"] == "promotion_roi"
    assert len(trend["series"][0]["points"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 26},
        {"limit": 0},
        {"offset": -1},
        {"recent_days": 0},
        {"recent_days": 367},
        {"shop_id": "wrong"},
        {"date_from": "2026-10-26"},
        {"date_from": "2026-10-26", "date_to": "2026-10-27"},
    ],
)
def test_scope_rejected(prepared, changes):
    with pytest.raises((BoardError, SalesError)):
        sales_tools.read(prepared, "sales_performance", {**args(prepared), **changes})


def test_version_required_and_stale_rejected(prepared):
    with pytest.raises(BoardError):
        sales_tools.read(prepared, "sales_performance", {})
    for capability in sales_tools.CAPABILITIES:
        if capability.startswith("sales_"):
            with pytest.raises(SalesError, match="DATA_VERSION_CHANGED"):
                sales_tools.read(prepared, capability, {"data_version": "old", "month": "2026-10"})
    with pytest.raises(BoardError):
        sales_tools.read(prepared, "sales_trend", args(prepared, view="invented"))


def test_weather_status_are_local_read_only(prepared, tmp_path):
    from app.jobs.sync_weather import sync_weather

    sync_weather(prepared, sources, tmp_path / "weather")
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(prepared, "before_cursor_execute", capture)
    try:
        weather_meta = sales_tools.describe(prepared, domain="weather")
        weather = sales_tools.read(
            prepared, "weather", {"data_version": weather_meta["data_version"]}
        )
        assert weather["period"] == {"date_from": "2026-10-28", "date_to": "2026-10-28"}
        assert "江苏" in json.dumps(weather, ensure_ascii=False)
        assert_report(weather)
        status = sales_tools.read(prepared, "data_status", {"limit": 2, "offset": 1})
        assert_report(status)
        assert status["tables"][0]["total"] > 2
        assert status["tables"][0]["offset"] == 1
        for view in ("datasets", "processing", "timeliness", "history", "reconciliation"):
            assert_report(sales_tools.read(prepared, "data_status", {"view": view, "limit": 2}))
        assert sales_tools.describe(prepared, domain="data_status")["domain"] == "data_status"
        with pytest.raises(BoardError, match="DATA_VERSION_CHANGED"):
            sales_tools.read(prepared, "weather", {"data_version": "old"})
    finally:
        event.remove(prepared, "before_cursor_execute", capture)
    assert not set(statements) & {"UPDATE", "DELETE", "INSERT", "CREATE", "ALTER"}


def test_zero_missing_and_empty_sources(sales_pg, tmp_path):
    from app.modules.sales.normalization import normalize_shop_daily
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_extended_sources import window
    from tests.test_sales_source import raw_row

    ShopDailyStore(sales_pg, archive_root=tmp_path / "daily", approved=window()).publish_candidate(
        normalize_shop_daily(
            [
                raw_row(
                    metrics={
                        "成交金额": "0",
                        "店铺访客数": "0",
                        "成交买家数": "0",
                        "成交订单数": "0",
                    }
                ),
                raw_row(
                    id="0002",
                    dt="2026-10-27 00:00:00",
                    metrics={
                        "成交金额": "10",
                        "店铺访客数": "5",
                        "成交买家数": "0",
                        "成交订单数": "0",
                    },
                ),
            ],
            window(),
        )
    )
    p = args(sales_pg, shop_id="spigf")
    zero = sales_tools.read(
        sales_pg, "sales_performance", {**p, "date_from": "2026-10-26", "date_to": "2026-10-26"}
    )
    facts = {f["key"]: f for f in zero["facts"]}
    assert facts["shop_revenue"]["value"] == "0.00"
    assert facts["buyer_conversion"]["value"] is None
    null = sales_tools.read(
        sales_pg,
        "sales_performance",
        {**p, "shop_id": "spihw", "date_from": "2026-10-27", "date_to": "2026-10-27"},
    )
    assert {f["key"]: f for f in null["facts"]}["shop_revenue"]["status"] == "no_records"
    for capability in (
        "sales_regions",
        "sales_product_regions",
        "sales_product_options",
        "sales_ratings",
    ):
        value = sales_tools.read(sales_pg, capability, p)
        assert_report(value)
        if capability != "sales_ratings":
            assert value["tables"][0]["total"] == 0
        if capability in ("sales_regions", "sales_product_regions"):
            assert value["facts"][0]["status"] == "not_connected"
    weather_meta = sales_tools.describe(sales_pg, "weather")
    empty = sales_tools.read(sales_pg, "weather", {"data_version": weather_meta["data_version"]})
    assert empty["period"] is None
    assert empty["facts"][0]["value"] == "source_unavailable"
