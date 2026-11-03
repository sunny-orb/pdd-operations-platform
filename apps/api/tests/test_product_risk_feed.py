"""运营中心风险接口：真实PG核验完整名单、缺项、身份、期间和页面一致性。"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


@pytest.mark.parametrize(
    ("orders", "metric", "method", "current"),
    [(0, "orders", "daily_average", "0"), (4, "conversion", "ratio_of_totals", "0.02")],
)
def test_order_and_conversion_alert_contract(sales_pg, tmp_path, orders, metric, method, current):
    end, data = candidate(duplicate_profile=False)
    rows = [
        dict(
            z_id=i + 1,
            shop_id="663599807",
            spu_id="123",
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount="100",
            product_uv=0 if i == 2 else 200,
            transaction_orders=orders if i == 0 else 0 if i == 2 else 10,
        )
        for i in range(8)
    ]
    data["product_daily"] = normalize("product_daily", rows, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        meta = client.get("/api/v1/products/meta").json()
        result = client.get(
            "/api/v1/products/risks",
            params=dict(
                date_from=str(end - timedelta(days=7)),
                date_to=str(end),
                data_version=meta["meta"]["data_version"],
            ),
        )
        assert result.status_code == 200
        alert = result.json()["data"]["risks"][0]["alert"]
        assert alert["metric"] == metric and alert["baseline_method"] == method
        assert alert["current"] == current
        if metric == "conversion":
            assert alert["baseline"] == "0.05"
            assert alert["change"] == "-0.6"
            assert alert["trend"][4] is None  # 真实零访客日不伪造0%转化。
        else:
            assert alert["change"] == "-1"
            assert alert["trend"][4] == "0"


def test_risk_feed_real_pg(sales_pg, tmp_path):
    end, data = candidate(duplicate_profile=False)
    products = [
        ("663599807", pid, "spigf")
        for pid in ("123", "234", "345", "456", "567", "678", "789", "890", "901")
    ] + [("799193231", "123", "spihw")]
    rows, profiles = [], []
    for index, (shop, pid, profile_field) in enumerate(products):
        profiles.append(
            dict(
                __id=index + 1,
                **{profile_field: pid},
                spmc=pid,
                cpdj="清仓" if pid == "890" else "A级",
                pl="帽子",
            )
        )
        for i in range(8):
            if pid == "678" and i == 3:
                continue
            rows.append(
                dict(
                    z_id=len(rows) + 1,
                    shop_id=shop,
                    spu_id=pid,
                    dt=str(end - timedelta(days=i)),
                    time_granularity="day",
                    transaction_amount=None if pid == "456" and i == 1 else "100",
                    product_uv=None
                    if pid == "789" and i == 0
                    else (50 if i == 0 and pid != "567" else 200),
                    transaction_orders=4 if i == 0 and pid != "567" else 10,
                )
            )
    data["product_daily"] = normalize("product_daily", rows, end)
    data["profile_records"] = normalize("profile_records", profiles, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        token = client.get("/api/v1/products/meta").json()["meta"]["data_version"]
        params = dict(date_from=str(end - timedelta(days=7)), date_to=str(end), data_version=token)
        result = client.get("/api/v1/products/risks", params=params)
        assert result.status_code == 200, result.text
        feed = result.json()["data"]
        assert result.json()["meta"]["data_version"] == token
        assert feed["total"] == 6  # 不截前三、不按页面5条分页截断接口。
        assert feed["counts"] == dict(
            selected=10, excluded=1, evaluated=7, unevaluated=2, flagged=6, unflagged=1
        )
        assert feed["evaluation_date"] == str(end)
        assert feed["baseline_period"] == dict(
            start=str(end - timedelta(days=7)), end=str(end - timedelta(days=1))
        )
        assert feed["trend_period"] == dict(start=str(end - timedelta(days=6)), end=str(end))
        assert feed["rule_version"] == "product-risk-online-v1"
        assert feed["impact_kind"] == "estimated"
        assert feed["status"] == "partial"
        overview = client.get("/api/v1/products/overview", params=params).json()["data"]
        expected = [{key: row[key] for key in feed["risks"][0]} for row in overview["risks"]]
        assert feed["risks"] == expected
        twins = [row for row in feed["risks"] if row["product_id"] == "123"]
        assert len({row["listing_id"] for row in twins}) == 2
        assert len({row["shop_id"] for row in twins}) == 2
        item = twins[0]
        assert item["period_revenue"] == "800"
        assert item["alert"]["current"] == "50"
        assert item["alert"]["baseline"] == "200"
        assert item["alert"]["change"] == "-0.75"
        assert item["alert"]["expected_loss"] == "75.00"
        assert item["alert"]["metric"] == "visitors"
        assert item["alert"]["baseline_method"] == "daily_average"
        assert item["alert"]["deadline"] is None
        assert item["alert"]["status"] == "未启用"
        assert len(item["alert"]["trend"]) == 7
        assert (
            next(r for r in feed["risks"] if r["product_id"] == "456")["alert"]["expected_loss"]
            is None
        )
        selected = client.get(
            "/api/v1/products/risks", params={**params, "listing_ids": item["listing_id"]}
        ).json()["data"]
        assert selected["risks"] == [item]
        empty = client.get(
            "/api/v1/products/risks", params={**params, "category": "不存在"}
        ).json()["data"]
        assert empty["status"] == "empty" and empty["total"] == 0
        missing = client.get("/api/v1/products/risks", params={**params, "keyword": "789"}).json()[
            "data"
        ]
        assert missing["status"] == "insufficient_data" and missing["counts"]["unevaluated"] == 1
        excluded = client.get("/api/v1/products/risks", params={**params, "level": "清仓"}).json()[
            "data"
        ]
        assert excluded["status"] == "not_applicable" and excluded["counts"]["excluded"] == 1
        assert (
            client.get(
                "/api/v1/products/risks", params={**params, "data_version": "stale"}
            ).status_code
            == 409
        )
        assert (
            client.get(
                "/api/v1/products/risks", params={**params, "date_to": "2020-01-01"}
            ).status_code
            == 422
        )


def test_risk_feed_no_database_is_not_an_empty_success():
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        response = client.get(
            "/api/v1/products/risks",
            params=dict(date_from="2026-10-02", date_to="2026-11-01", data_version="not-connected"),
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
