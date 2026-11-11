"""接口/聚合真实PG测试，合成反例不进入业务库。"""

from datetime import date

import sqlalchemy as sa
from fastapi.testclient import TestClient
from test_sales_source import raw_row

from app.core.config import Settings
from app.main import create_app
from app.modules.sales.contracts import ReadWindow
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_policy import SHOPS
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def test_query_contract_weighted_ratios_zero_and_version_conflict(sales_pg, tmp_path):
    window = ReadWindow(date(2026, 10, 25), date(2026, 10, 27), tuple(SHOPS))
    rows = [
        raw_row(
            id="001",
            dt="2026-10-25 00:00:00",
            metrics={"成交金额": "10.20", "店铺访客数": "10", "成交买家数": "2", "成交订单数": "8"},
        ),
        raw_row(
            id="002",
            metrics={
                "成交金额": "20.30",
                "店铺访客数": "100",
                "成交买家数": "3",
                "成交订单数": "9",
            },
        ),
        raw_row(
            id="003",
            dt="2026-10-25 00:00:00",
            shop_id="799193231",
            shop_name=SHOPS["799193231"][1],
            metrics={"成交金额": "0.00", "店铺访客数": "0", "成交买家数": "0", "成交订单数": "0"},
        ),
    ]
    published = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window).publish_candidate(
        normalize_shop_daily(rows, window)
    )
    app = create_app(Settings(database_url=""))
    app.state.engine = sales_pg
    client = TestClient(app)
    filters = client.get("/api/v1/sales/filters")
    assert filters.status_code == 200
    assert len(filters.json()["data"]["shops"]) == 6
    official = next(s for s in filters.json()["data"]["shops"] if s["shop_id"] == "spigf")
    assert official["source_shop_id"] == "663599807"
    version = filters.json()["meta"]["data_version"]
    assert filters.json()["meta"]["input_versions"]["shop_daily"] == published.version_id
    params = dict(date_from="2026-10-25", date_to="2026-10-27", data_version=version)
    response = client.get("/api/v1/sales/performance", params=params)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["shop_revenue"] == "30.50"
    assert data["buyer_conversion"] == "0.045454545455"
    assert data["aov"] == "6.100000000000"
    assert data["promotion_spend"] is None
    assert response.json()["meta"]["complete_through"] is None
    single = client.get("/api/v1/sales/performance", params={**params, "shop_id": "spihw"}).json()[
        "data"
    ]
    assert single["shop_revenue"] == "0.00" and single["buyer_conversion"] is None
    shops = client.get("/api/v1/sales/shops", params=params).json()["data"]["items"]
    assert len(shops) == 6 and shops[0]["shop_id"] == "spigf"
    assert shops[0]["sales_share"] == "1.000000000000"
    assert (
        next(s for s in shops if s["shop_id"] == "spihw")["coverage"]["latest_observed_date"]
        == "2026-10-25"
    )
    assert shops[-1]["rank"] is None and shops[-1]["shop_revenue"] is None
    latest_day = filters.json()["data"]["latest_day"]
    assert latest_day == {
        "date": "2026-10-26",
        "shop_revenue": "20.30",
        "observed_shop_count": 1,
    }
    assert (
        client.get(
            "/api/v1/sales/performance", params={**params, "data_version": "old"}
        ).status_code
        == 409
    )
    assert (
        client.get("/api/v1/sales/performance", params={**params, "shop_id": "unknown"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/sales/performance", params={**params, "date_from": "2026-10-28"}
        ).status_code
        == 422
    )
    assert (
        client.get("/api/v1/sales/shops", params={**params, "shop_id": "spigf"}).status_code == 422
    )
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM shop_daily_facts")).scalar_one() == 3


def test_no_connection_returns_explicit_service_unavailable():
    client = TestClient(create_app(Settings(database_url="")))
    result = client.get("/api/v1/sales/filters")
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert result.json()["error"]["request_id"]


def test_stale_publication_keeps_current_calendar_and_exposes_missing_days(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.sales import source_policy

    monkeypatch.setattr(source_policy, "calendar_through", lambda: date(2026, 11, 1))
    window = ReadWindow(date(2026, 2, 1), date(2026, 10, 27), tuple(SHOPS))
    ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window).publish_candidate(
        normalize_shop_daily([raw_row()], window)
    )
    app = create_app(Settings(database_url=""))
    app.state.engine = sales_pg
    client = TestClient(app)
    filters = client.get("/api/v1/sales/filters").json()
    assert filters["data"]["calendar_through"] == "2026-11-01"
    assert filters["data"]["default_month"] == "2026-11"
    assert filters["data"]["observed_through"] == "2026-10-26"
    assert filters["data"]["latest_day"]["date"] == "2026-10-26"
    params = dict(
        date_from="2026-11-01", date_to="2026-11-01", data_version=filters["meta"]["data_version"]
    )
    response = client.get("/api/v1/sales/performance", params=params)
    assert response.status_code == 200
    assert response.json()["data"]["shop_revenue"] is None
    assert len(response.json()["data"]["coverage"]["missing_dates"]) == 6
    assert (
        client.get(
            "/api/v1/sales/performance", params={**params, "date_to": "2026-11-02"}
        ).status_code
        == 422
    )
