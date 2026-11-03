"""完整轻量筛选目录：真实PG，超过100款、身份隔离、日期/版本及新品边界。"""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


def test_catalog_returns_every_listing_without_operating_values(sales_pg, tmp_path):
    end, rows = candidate(duplicate_profile=False)
    records = [
        dict(
            z_id=i + 1,
            shop_id="663599807",
            spu_id=str(1000 + i),
            dt=str(end),
            time_granularity="day",
            spu_name=f"款{i}",
            transaction_amount="10",
        )
        for i in range(151)
    ]
    records += [
        dict(records[0], z_id=152, shop_id="799193231"),
        dict(records[0], z_id=153, spu_id="999", dt=str(end - timedelta(days=1))),
    ]
    rows["product_daily"] = normalize("product_daily", records, end)
    rows["profile_records"] = normalize(
        "profile_records",
        [
            dict(__id=i + 1, spigf=str(1000 + i), spmc=f"款{i}", pl="衣", cpdj="新品", yy="甲")
            for i in range(151)
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        version = client.get("/api/v1/products/meta").json()["data"]["data_version"]
        params = dict(date_from=str(end), date_to=str(end), data_version=version)
        result = client.get("/api/v1/products/catalog", params=params)
        assert result.status_code == 200
        data = result.json()["data"]
        assert data["total"] == len(data["items"]) == 152
        assert len({item["value"] for item in data["items"]}) == 152
        assert len(data["shops"]) == 2
        assert sum("1000" in item["search_text"] for item in data["items"]) == 2
        assert any("1150" in item["search_text"] for item in data["items"])
        assert all(
            set(item)
            == {
                "value",
                "label",
                "shop",
                "operator",
                "category",
                "level",
                "level_group",
                "level_details",
                "search_text",
            }
            for item in data["items"]
        )
        earlier = client.get(
            "/api/v1/products/catalog", params={**params, "date_from": str(end - timedelta(days=1))}
        ).json()["data"]
        assert earlier["total"] == 153
        assert (
            client.get(
                "/api/v1/products/catalog", params={**params, "data_version": "expired"}
            ).status_code
            == 409
        )
        assert (
            client.get(
                "/api/v1/products/catalog",
                params={**params, "date_from": str(end + timedelta(days=1))},
            ).status_code
            == 422
        )


def test_score_catalog_keeps_profile_only_new_products_and_aliases(sales_pg, tmp_path):
    end, rows = candidate()
    rows["profile_records"] = normalize(
        "profile_records",
        [
            dict(__id=1, spigf="123", spmc="帽子（大码）", pl="帽", cpdj="新品"),
            dict(__id=2, spigf="123", spmc="帽子（小码）", pl="帽", cpdj="新品"),
            dict(__id=3, spigf="456", spmc="无经营新品", pl="衣", cpdj="新品"),
            dict(__id=4, spigf="789", spmc="非新品", pl="衣", cpdj="A级"),
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        version = client.get("/api/v1/products/meta").json()["data"]["data_version"]
        params = dict(date_from=str(end), date_to=str(end), data_version=version)
        result = client.get("/api/v1/products/catalog", params={**params, "scope": "score"})
        assert result.status_code == 200
        data = result.json()["data"]
        assert data["total"] == 2
        assert all(item["level"] == "新品" for item in data["items"])
        assert any("无经营新品" in item["label"] for item in data["items"])
        assert any("小码" in item["search_text"] for item in data["items"])
        main = client.get("/api/v1/products/catalog", params=params).json()["data"]
        assert main["total"] == 1
