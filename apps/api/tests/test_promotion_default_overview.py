"""商品推广默认展示预计算的真实 PostgreSQL 验收。"""

from datetime import date
from threading import Event

from fastapi.testclient import TestClient

from app import main
from app.api import promotion
from app.core.config import Settings
from tests.test_promotion_api import publish_products
from tests.test_promotion_query_performance import promotion_row

pytest_plugins = ["tests.sales_pg_support"]


def default_params(client):
    response = client.get("/api/v1/promotion/meta", params={"mode": "product"})
    assert response.status_code == 200
    meta = response.json()
    return {
        "mode": "product",
        "date_from": meta["data"]["recommended_from"],
        "date_to": meta["data"]["recommended_to"],
        "data_version": meta["meta"]["data_version"],
        "detail": "display",
    }


def test_product_default_overview_prewarms_and_preserves_other_scopes(
    sales_pg, tmp_path, monkeypatch
):
    first_day = promotion_row(date(2026, 10, 20), spend="2")
    latest_day = promotion_row(date(2026, 10, 27), spend="3")
    publish_products(sales_pg, tmp_path, [first_day, latest_day])
    computed = Event()
    calls = []
    original = promotion.overview

    def count_overview(repo, q):
        data = original(repo, q)
        calls.append((repo.token, q))
        computed.set()
        return data

    monkeypatch.setattr(promotion, "overview", count_overview)
    monkeypatch.setattr(main, "build_engine", lambda _: sales_pg)
    monkeypatch.setattr(main, "OVERVIEW_PREWARM_INTERVAL", 0.02, raising=False)
    app = main.create_app(Settings(_env_file=None, app_env="production", database_url=""))
    path = "/api/v1/promotion/overview"
    with TestClient(app) as client:
        assert computed.wait(3), "启动后应在用户首次访问前计算商品推广默认总览"
        params = default_params(client)
        assert params["date_from"] == "2026-10-20" and params["date_to"] == "2026-10-27"
        first = client.get(path, params=params)
        repeated = client.get(path, params=params)
        assert first.status_code == repeated.status_code == 200
        assert first.json()["data"] == repeated.json()["data"]
        assert first.json()["data"]["metrics"]["spend"]["value"] == "5"
        assert first.headers["X-Request-ID"] != repeated.headers["X-Request-ID"]
        assert repeated.json()["meta"]["request_id"] == repeated.headers["X-Request-ID"]
        assert len(calls) == 1

        filtered = client.get(path, params=params | {"keyword": "不匹配"})
        historical = client.get(path, params=params | {"date_to": params["date_from"]})
        full = client.get(path, params=params | {"detail": "full"})
        assert filtered.status_code == historical.status_code == full.status_code == 200
        assert filtered.json()["data"]["current_record_count"] == "0"
        assert historical.json()["data"]["metrics"]["spend"]["value"] == "2"
        assert "coverage" in full.json()["data"]["series"][0]
        assert "coverage" not in first.json()["data"]["series"][0]
        assert len(calls) == 4

        computed.clear()
        publish_products(sales_pg, tmp_path, [first_day, dict(latest_day, total_cost="8")])
        assert computed.wait(3), "同日期修订并发布后应自动更新预计算"
        assert client.get(path, params=params).status_code == 409
        updated = default_params(client)
        result = client.get(path, params=updated)
        assert result.status_code == 200
        assert result.json()["data"]["metrics"]["spend"]["value"] == "10"
        assert len(calls) == 5
