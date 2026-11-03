"""推广等级分组用真实PG验证：不展开规格、不改原等级或经营金额。"""

from datetime import date
from decimal import Decimal

from app.modules.product_board.repository import snapshot as product_snapshot
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate
from tests.test_promotion_api import client_for

pytest_plugins = ["tests.sales_pg_support"]


def test_level_groups_keep_raw_members_and_partition_facts(sales_pg, tmp_path):
    end = date(2026, 10, 27)
    _, rows = candidate()
    profiles = [
        dict(__id=i, spigf=pid, spmc="测试商品", pl="帽子", cpdj=level)
        for i, (pid, level) in enumerate(
            [
                ("123", "A级"),
                ("124", "A级"),
                ("124", "B级"),
                ("124", "B级"),
                ("125", "B级"),
                ("125", "S级"),
                ("126", None),
            ],
            1,
        )
    ]
    ads = [
        dict(
            id=i,
            shop_id="663599807",
            spu_id=str(122 + i),
            spu_name="测试商品",
            dt=str(end),
            start_dt=str(end),
            time_granularity="day",
            total_cost=str(i),
            transaction_amount=str(i * 10),
            net_transaction_amount=str(i * 8),
            impression_count="100",
            click_count="10",
            transaction_count="1",
        )
        for i in range(1, 5)
    ]
    rows["profile_records"] = normalize("profile_records", profiles, end)
    rows["product_promotion"] = normalize("product_promotion", ads, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with product_snapshot(sales_pg) as repo:
        original_profiles = repo.profiles()
    with client_for(sales_pg) as client:
        version = client.get("/api/v1/promotion/meta?mode=product").json()["meta"]["data_version"]
        q = dict(mode="product", date_from=str(end), date_to=str(end), data_version=version)
        catalog = client.get("/api/v1/promotion/options", params={**q, "catalog": True}).json()[
            "data"
        ]
        assert set(catalog["levels"]) == {"A级", "多等级", "未匹配"}
        by_id = {p["product_id"]: p for p in catalog["products"]}
        assert by_id["124"]["level"] == by_id["125"]["level"] == "多等级"
        assert by_id["124"]["level_details"] == ["A级", "B级"]
        assert by_id["125"]["level_details"] == ["B级", "S级"]
        assert by_id["126"]["level_details"] == []
        grouped_spend = Decimal(0)
        for level, ids, spend in [
            ("A级", {"123"}, "1"),
            ("多等级", {"124", "125"}, "5"),
            ("未匹配", {"126"}, "4"),
        ]:
            filtered = {**q, "level": level}
            choices = client.get("/api/v1/promotion/options", params=filtered).json()["data"]
            assert {p["product_id"] for p in choices["products"]} == ids
            summary = client.get("/api/v1/promotion/overview", params=filtered).json()["data"]
            daily = client.get("/api/v1/promotion/days", params=filtered).json()["data"]
            diagnosis = client.get("/api/v1/promotion/diagnosis", params=filtered).json()["data"]
            assert Decimal(summary["metrics"]["spend"]["value"]) == Decimal(spend)
            assert sum(Decimal(r["spend"]) for r in daily["items"]) == Decimal(spend)
            assert {p["product_id"] for p in diagnosis["products"]} == ids
            grouped_spend += Decimal(spend)
        assert grouped_spend == Decimal(
            client.get("/api/v1/promotion/overview", params=q).json()["data"]["metrics"]["spend"][
                "value"
            ]
        )
        legacy = client.get(
            "/api/v1/promotion/options", params={**q, "level": "A级/B级混合"}
        ).json()["data"]
        assert {p["product_id"] for p in legacy["products"]} == {"124"}
    with product_snapshot(sales_pg) as repo:
        assert repo.profiles() == original_profiles
