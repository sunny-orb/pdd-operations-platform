"""新版M1协议与真实PG输出核验；不触发来源连接。"""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


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
