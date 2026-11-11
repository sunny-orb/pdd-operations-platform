"""新版M1协议与真实PG输出核验；不触发来源连接。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api import product_board
from app.core.config import Settings
from app.core.overview_cache import DefaultOverviewCache
from app.main import create_app
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


def test_failed_warm_does_not_evict_ready_version():
    cache = DefaultOverviewCache()
    assert cache.get_or_compute("ready", lambda: b"existing") == b"existing"

    def fail():
        raise ValueError("DATA_VERSION_CHANGED")

    with pytest.raises(ValueError, match="DATA_VERSION_CHANGED"):
        cache.get_or_compute("outdated", fail)
    assert cache.get_or_compute("ready", fail) == b"existing"


def default_params(client):
    meta = client.get("/api/v1/products/meta").json()["data"]
    end = date.fromisoformat(meta["date_to"])
    return dict(
        date_from=max(date.fromisoformat(meta["date_from"]), end - timedelta(days=30)),
        date_to=end,
        data_version=meta["data_version"],
    )


def test_production_prepares_default_overview_on_startup_and_after_publish(
    sales_pg, tmp_path, monkeypatch
):
    store = ProductBoardStore(sales_pg, tmp_path)
    store.run(lambda hb: candidate())
    calls = []
    started = Event()
    original = product_board.overview

    def count_overview(repo, query):
        calls.append((repo.token, query.start, query.end))
        if query.start == query.end:
            started.set()
        return original(repo, query)

    monkeypatch.setattr(product_board, "overview", count_overview)
    monkeypatch.setattr(main, "build_engine", lambda settings: sales_pg)
    monkeypatch.setattr(main, "OVERVIEW_PREWARM_INTERVAL", 0.02)
    app = create_app(Settings(_env_file=None, app_env="production", database_url=""))
    with TestClient(app) as client:
        assert started.wait(3), "API启动应预计算运营中心的最新单日汇总"
        url = "/api/v1/products/overview"
        first = default_params(client)
        assert first["date_from"] < first["date_to"]
        daily = {**first, "date_from": first["date_to"]}
        response = client.get(url, params=first)
        assert response.status_code == 200
        assert response.json()["data"]["metrics"]["revenue"]["value"] == "23.17"
        one_day = client.get(url, params=daily)
        assert one_day.status_code == 200
        assert one_day.json()["data"]["metrics"]["revenue"]["value"] == "23.17"
        assert one_day.json()["data"] != response.json()["data"]
        assert sorted((start, end) for _, start, end in calls) == [
            (first["date_from"], first["date_to"]),
            (daily["date_from"], daily["date_to"]),
        ]
        started.clear()

        store.run(lambda hb: candidate(amount="47.25"))
        assert started.wait(3), "版本变化应自动预计算"
        latest = default_params(client)
        assert client.get(url, params=first).status_code == 409
        response = client.get(url, params=latest)
        assert response.status_code == 200
        assert response.json()["data"]["metrics"]["revenue"]["value"] == "47.25"
        latest_daily = {**latest, "date_from": latest["date_to"]}
        assert (
            client.get(url, params=latest_daily).json()["data"]["metrics"]["revenue"]["value"]
            == "47.25"
        )
        assert len(calls) == 4
        assert {token for token, _, _ in calls} == {first["data_version"], latest["data_version"]}

        filtered = client.get(url, params={**latest_daily, "category": "不存在的品类"})
        assert filtered.status_code == 200
        assert filtered.json()["data"]["product_count"] == "0"
        assert len(calls) == 5


def test_default_overview_reuses_only_current_published_result(sales_pg, tmp_path, monkeypatch):
    store = ProductBoardStore(sales_pg, tmp_path)
    store.run(lambda hb: candidate())
    calls = []
    original = product_board.overview

    def count_overview(repo, query):
        calls.append(query)
        return original(repo, query)

    monkeypatch.setattr(product_board, "overview", count_overview)
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg

        url = "/api/v1/products/overview"
        first_params = default_params(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(lambda _: client.get(url, params=first_params), range(2))
        assert first.status_code == second.status_code == 200
        assert first.json()["data"] == second.json()["data"]
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
        assert first.json()["meta"]["request_id"] == first.headers["X-Request-ID"]
        assert second.json()["meta"]["request_id"] == second.headers["X-Request-ID"]
        assert len(calls) == 1

        filtered = client.get(url, params={**first_params, "category": "不存在的品类"})
        assert filtered.status_code == 200
        assert filtered.json()["data"]["product_count"] == "0"
        assert len(calls) == 2
        assert client.get(url, params=first_params).json()["data"] == first.json()["data"]
        assert len(calls) == 2

        store.run(lambda hb: candidate(amount="47.25"))
        assert client.get(url, params=first_params).status_code == 409
        latest = client.get(url, params=default_params(client))
        assert latest.status_code == 200
        assert latest.json()["data"]["metrics"]["revenue"]["value"] == "47.25"
        assert len(calls) == 3


def test_real_pg_endpoints_decimal_ad_merge_and_stale_vector(sales_pg, tmp_path):
    end, rows = candidate()
    rows["product_promotion"] = normalize(
        "product_promotion",
        [
            dict(
                id="a",
                shop_id="663599807",
                spu_id="123",
                dt="2026-10-26",
                start_dt="2026-10-26",
                time_granularity="day",
                promotion_name="方式A",
                total_cost="2",
                transaction_amount="4",
                impression_count="2000.5",
                click_count="100",
                transaction_count="1.5",
            ),
            dict(
                id="b",
                shop_id="663599807",
                spu_id="123",
                dt="2026-10-26",
                start_dt="2026-10-26",
                time_granularity="day",
                promotion_name="方式B",
                total_cost="8",
                transaction_amount="36",
                impression_count="100.5",
                click_count="50",
                transaction_count="3.5",
            ),
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        meta = client.get("/api/v1/products/meta").json()
        assert meta["data"]["complete_through"] is None
        params = dict(
            date_from="2026-10-26", date_to="2026-10-26", data_version=meta["meta"]["data_version"]
        )
        for path in ["overview", "options", "days", "scores"]:
            response = client.get("/api/v1/products/" + path, params=params)
            assert response.status_code == 200, response.json().get("error")
            assert response.headers["X-Request-ID"] == response.json()["meta"]["request_id"]
        scores = client.get("/api/v1/products/scores", params=params).json()["data"]
        first = scores["items"][0]
        assert first["ad_roi"] == "4"
        assert first["ad_impressions"] == "2101.0"
        assert first["score"] is None
        selected = client.get(
            "/api/v1/products/options",
            params={**params, "keyword": "不匹配候选", "selected_listing_ids": first["listing_id"]},
        ).json()["data"]
        assert selected["products"] == []
        assert selected["selected_products"][0]["value"] == first["listing_id"]
        detail = client.get("/api/v1/products/scores/" + first["listing_id"], params=params)
        assert detail.status_code == 200
        assert len(detail.json()["data"]["signals"]) == 10
        stale = client.get("/api/v1/products/overview", params={**params, "data_version": "stale"})
        assert stale.status_code == 409
        assert (
            client.get("/api/v1/products/days", params={**params, "page_size": 999}).status_code
            == 422
        )
