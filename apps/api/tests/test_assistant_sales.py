"""销售/天气/状态助手契约：仅合成输入，读取真实隔离PostgreSQL。"""

import json
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import event

from app.modules.assistant import sales_tools
from app.modules.product_board.repository import BoardError
from app.modules.sales.contracts import SalesError
from app.modules.sales.normalization import fingerprint, normalize_shop_daily
from app.modules.sales.source_policy import SHOPS
from app.modules.sales.storage import ShopDailyStore
from tests.test_sales_extended_api import publish, setup
from tests.test_sales_extended_sources import sample, window
from tests.test_sales_source import raw_row
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


def test_shop_revenue_fact_precedes_pagination_and_keeps_six_shop_denominator(sales_pg, tmp_path):
    rows = [raw_row(id=sid, shop_id=sid, shop_name=identity[1]) for sid, identity in SHOPS.items()]
    for row, amount in zip(rows, ["80.55", "19.45", "0", "0", "0", "0"], strict=True):
        row["metrics"]["成交金额"] = amount
    ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window()).publish_candidate(
        normalize_shop_daily(rows, window())
    )
    params = args(sales_pg)
    full = sales_tools.read(sales_pg, "sales_shops", {**params, "limit": 1, "offset": 6})
    assert full["tables"][0]["rows"] == []
    total = next(f for f in full["facts"] if f["key"] == "all_shops_revenue")
    assert (total["value"], total["unit"], total["status"]) == ("100.00", "currency", "observed")
    assert "六店" in total["label"]
    single = sales_tools.read(sales_pg, "sales_shops", {**params, "shop_id": "spihw"})
    facts = {f["key"]: f for f in single["facts"]}
    assert facts["all_shops_revenue"] == total
    assert facts["shop_revenue"]["value"] == "19.45"
    assert "户外" in facts["shop_revenue"]["label"]
    assert single["tables"][0]["rows"][0]["sales_share"] == "0.194500000000"


def test_shop_revenue_reports_published_null_amount_without_losing_observed_row(sales_pg, tmp_path):
    # 为读取端构造可空金额的已封版行。现行接收器会拒绝这种核心字段缺失。
    normal = normalize_shop_daily([raw_row()], window())
    raw = normal.raw_rows[0]
    raw_rows = (dict(raw, metrics={**raw["metrics"], "成交金额": None}),)
    nullable = replace(
        normal,
        rows=(dict(normal.rows[0], shop_revenue=None),),
        raw_rows=raw_rows,
        fingerprint=fingerprint(raw_rows),
    )
    ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window()).publish_candidate(nullable)
    result = sales_tools.read(sales_pg, "sales_shops", args(sales_pg, shop_id="spigf"))
    facts = {f["key"]: f for f in result["facts"]}
    for key in ("shop_revenue", "all_shops_revenue"):
        assert facts[key]["value"] is None
        assert facts[key]["status"] == "field_null"
    assert result["tables"][0]["rows"][0]["rank"] is None


def test_target_report_separates_published_channel_goal_from_requested_shop(prepared):
    p = args(prepared, month="2026-10")
    channel = sales_tools.read(prepared, "sales_targets", p)
    single = sales_tools.read(prepared, "sales_targets", {**p, "shop_id": "spigf"})
    for result in (channel, single):
        assert result["scope"]["target_grain"] == "channel"
        assert result["scope"]["shop_target_status"] == "not_published"
        facts = {f["key"]: f for f in result["facts"]}
        assert "六店渠道" in facts["month_target"]["label"]
        assert "六店渠道" in facts["month_ratio"]["label"]
        assert facts["month_target"]["value"] == "25000.25"
        assert facts["month_ratio"]["value"] == "0.003999960000"
    assert single["scope"]["requested_shop_id"] == "spigf"
    facts = {f["key"]: f for f in single["facts"]}
    assert "官方" in facts["shop_month_target"]["label"]
    assert "官方" in facts["shop_month_ratio"]["label"]
    assert facts["shop_month_target"]["value"] is None
    assert facts["shop_month_target"]["status"] == "not_published"
    assert facts["shop_month_ratio"]["value"] is None
    assert facts["shop_month_ratio"]["reason"]
    with pytest.raises(BoardError, match="INVALID_SCOPE"):
        sales_tools.read(prepared, "sales_targets", {**p, "shop_id": "invalid"})


def test_default_target_uses_sales_calendar_when_latest_sale_falls_in_prior_month(
    prepared, monkeypatch
):
    from app.modules.sales import detail_queries, source_policy

    monkeypatch.setattr(source_policy, "calendar_through", lambda: date(2026, 11, 2))
    monkeypatch.setattr(detail_queries, "calendar_through", lambda: date(2026, 11, 2))
    metadata = sales_tools.describe(prepared)
    assert metadata["date_to"] == "2026-10-26"
    target = sales_tools.read(prepared, "sales_targets", {"data_version": metadata["data_version"]})
    assert target["period"] == {"date_from": "2026-11-01", "date_to": "2026-11-02"}
    assert metadata["options"]["default_month"] == target["scope"]["month"] == "2026-11"
    facts = {f["key"]: f for f in target["facts"]}
    assert facts["month_target"]["value"] == "25000.25"
    assert facts["month_actual"]["value"] is None
    assert facts["month_ratio"]["value"] is None


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
                raw_row(id="0003", dt="2026-10-24 00:00:00"),
            ],
            window(),
        )
    )
    p = args(sales_pg, shop_id="spigf")
    for day, expected, state in [
        ("2026-10-25", None, "no_records"),
        ("2026-10-26", "0.00", "partial"),
    ]:
        report = sales_tools.read(sales_pg, "sales_shops", {**p, "date_from": day, "date_to": day})
        facts = {f["key"]: f for f in report["facts"]}
        assert facts["all_shops_revenue"]["value"] == expected
        assert facts["all_shops_revenue"]["status"] == state
        assert facts["all_shops_revenue"]["reason"]
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
