"""真实PG验证等级仅分组筛选，不改原等级、资格或重复累计经营事实。"""

from decimal import Decimal

from app.modules.product_board.repository import snapshot
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate
from tests.test_promotion_api import client_for

pytest_plugins = ["tests.sales_pg_support"]


def test_product_grade_groups_partition_facts_and_keep_legacy_and_new_product_rules(
    sales_pg, tmp_path
):
    end, rows = candidate()
    levels = [
        ("123", "A级"),
        ("124", "A级"),
        ("124", "B级"),
        ("124", "B级"),
        ("125", "B级"),
        ("125", "S级"),
        ("126", None),
        ("127", "新品"),
        ("128", "新品"),
        ("128", "A级"),
        ("129", "B级"),
        ("129", "清仓"),
        ("130", "清仓"),
    ]
    profiles = [
        dict(__id=i, spigf=pid, spmc="测试商品", pl="帽子", cpdj=level)
        for i, (pid, level) in enumerate(levels, 1)
    ]
    profiles += [
        dict(__id=30 + i, spihw="124", spmc="另一店商品", pl="帽子", cpdj=level)
        for i, level in enumerate(("A级", "B级"))
    ]
    facts = [
        dict(
            z_id=i,
            shop_id="663599807",
            spu_id=str(122 + i),
            dt=str(end),
            time_granularity="day",
            transaction_amount=str(i * 10),
            visitor_count="10",
            payment_user_count="1",
        )
        for i in range(1, 9)
    ]
    facts.append(dict(facts[1], z_id=9, shop_id="799193231", transaction_amount="90"))
    rows["profile_records"] = normalize("profile_records", profiles, end)
    rows["product_daily"] = normalize("product_daily", facts, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        original = repo.profiles()
    with client_for(sales_pg) as client:
        version = client.get("/api/v1/products/meta").json()["data"]["data_version"]
        q = dict(date_from=str(end), date_to=str(end), data_version=version)

        def read(path, **extra):
            response = client.get("/api/v1/products/" + path, params={**q, **extra})
            assert response.status_code == 200
            return response.json()["data"]

        directory = read("catalog")
        assert directory["total"] == 9
        groups = read("options")["levels"]
        assert set(groups) == {"A级", "多等级", "未匹配", "新品", "清仓"}
        mixed = [r for r in directory["items"] if r["level_group"] == "多等级"]
        assert len(mixed) == 5
        assert {r["level"] for r in mixed} == {
            "A级/B级混合",
            "B级/S级混合",
            "A级/新品混合",
            "B级/清仓混合",
        }
        assert all(len(r["level_details"]) == 2 for r in mixed)
        seen = set()
        for level, count, amount in [
            ("A级", 1, "10"),
            ("多等级", 5, "270"),
            ("未匹配", 1, "40"),
            ("新品", 1, "50"),
            ("清仓", 1, "80"),
        ]:
            opts, summary, daily = [
                read(path, level=level) for path in ("options", "overview", "days")
            ]
            assert opts["total"] == daily["total"] == count
            assert opts["selection"]["level"] == level
            assert Decimal(summary["metrics"]["revenue"]["value"]) == Decimal(amount)
            ids = {r["listing_id"] for r in daily["items"]}
            assert len(ids) == count and not (seen & ids)
            seen |= ids
        assert len(seen) == 9
        assert Decimal(read("overview")["metrics"]["revenue"]["value"]) == Decimal("450")
        legacy = read("options", level="A级/B级混合")
        assert legacy["total"] == 2 and legacy["selection"]["level"] == "A级/B级混合"
        assert "A级/B级混合" in legacy["levels"]
        assert Decimal(
            read("overview", level="A级/B级混合")["metrics"]["revenue"]["value"]
        ) == Decimal("110")
        assert read("catalog", scope="score")["total"] == read("scores")["total"] == 1
        assert read("scores")["items"][0]["product_id"] == "127"
        assert read("options", level="多等级", shop_id="799193231")["total"] == 1
        assert read("days", category="不存在")["items"] == []
    with snapshot(sales_pg) as repo:
        assert repo.profiles() == original
        assert {p["level"]: p["risk_eligible"] for p in original.values()}["B级/清仓混合"] is False
