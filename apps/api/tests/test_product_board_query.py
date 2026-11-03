from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.product_board.query import BoardQuery, overview, score_cards
from app.modules.product_board.repository import BoardError, snapshot
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


def test_score_catalog_keeps_profile_only_listings_with_filters_and_detail(sales_pg, tmp_path):
    from app.modules.product_board.query import options
    from app.modules.product_board.source import normalize

    end, rows = candidate(duplicate_profile=False)
    rows["profile_records"] = normalize(
        "profile_records",
        [
            dict(__id=1, spigf="123", spmc="有经营款", cpdj="新品", pl="衣"),
            dict(__id=2, spigf="456", spmc="无经营款", cpdj="新品", pl="帽", yy="甲"),
            dict(__id=3, spigf="456", spmc="无经营款", cpdj="新品", pl="帽", yy="甲"),
            dict(__id=4, spihw="456", spmc="另一店新品", cpdj="新品", pl="帽"),
            dict(__id=5, spigf="789", spmc="等级冲突", cpdj="新品"),
            dict(__id=6, spigf="789", spmc="等级冲突", cpdj="A级"),
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        q = BoardQuery(repo.start, repo.end, size=12)
        page = score_cards(repo, q)
        assert page["total"] == page["total_count"] == 3
        assert page["scored_count"] == 0
        assert page["groups"] == dict(complete=0, pending=0, unscored=3)
        assert len({r["listing_id"] for r in page["items"]}) == 3
        missing = next(r for r in page["items"] if r["product_name"] == "无经营款")
        assert missing["revenue"] is None and missing["score"] is None
        assert missing["decision"] == "unscored"
        filtered = score_cards(
            repo, BoardQuery(repo.start, repo.end, shop="663599807", category="帽")
        )
        assert filtered["total"] == 1 and filtered["total_count"] == 3
        assert filtered["items"][0]["listing_id"] == missing["listing_id"]
        assert score_cards(repo, q, missing["listing_id"])["items"][0] == missing
        directory = options(repo, q, (missing["listing_id"],), new_only=True)
        assert directory["total"] == 3
        assert directory["selection"]["products"] == [missing["listing_id"]]
        assert options(repo, q)["total"] == 1
        assert overview(repo, q)["metrics"]["revenue"]["value"] == Decimal("23.17")
        empty = score_cards(repo, BoardQuery(repo.start, repo.end, category="不存在"))
        assert empty["total"] == 0 and empty["total_count"] == 3
        assert score_cards(repo, BoardQuery(repo.start, repo.end, page=2, size=12))["items"] == []


def test_real_pg_category_comparison_allows_a_new_listing(sales_pg, tmp_path):
    from app.modules.product_board.source import normalize

    end, rows = candidate(duplicate_profile=False)
    products = [
        dict(
            z_id=i + 1,
            shop_id="663599807",
            spu_id="123",
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount="100",
        )
        for i in range(14)
    ]
    products.append(dict(products[0], z_id=15, spu_id="456"))
    rows["product_daily"] = normalize("product_daily", products, end)
    rows["profile_records"] = normalize(
        "profile_records",
        [dict(__id=i, spigf=pid, pl="冲锋衣") for i, pid in ((1, "123"), (2, "456"))],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        category = overview(repo, BoardQuery(end - timedelta(days=30), end))["brief"]["categories"][
            0
        ]
        assert category["revenue"] == Decimal("800")
        assert category["previous_revenue"] == Decimal("700")
        assert category["growth"].quantize(Decimal(".000001")) == Decimal(".142857")
        assert category["comparison_reason"] is None


def test_real_pg_profile_consensus_and_no_multiplication(sales_pg, tmp_path):
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: candidate())
    with snapshot(sales_pg) as repo:
        data = overview(repo, BoardQuery(date(2026, 10, 26), date(2026, 10, 26)))
        assert data["metrics"]["revenue"]["value"] == Decimal("23.17")
        assert data["metrics"]["conversion"]["value"] == Decimal(".2")
        assert data["metrics"]["revenue"]["previous"] is None
        cards = score_cards(repo, BoardQuery(repo.start, repo.end))
        assert cards["total"] == 1
        assert cards["items"][0]["decision"] == "unscored"
        assert len(cards["items"][0]["signals"]) == 10
        token = repo.token
    with pytest.raises(BoardError) as e:
        with snapshot(sales_pg, "stale"):
            pass
    assert e.value.status == 409
    with snapshot(sales_pg, token) as repo:
        assert repo.profiles()[next(iter(repo.profiles()))]["category"] == "冲锋衣"


def test_real_pg_variant_names_merge_without_multiplying_facts(sales_pg, tmp_path):
    end, rows = candidate()
    for profile, suffix in zip(rows["profile_records"], ("拉链款", "按扣款"), strict=True):
        profile["source_product_name_raw"] = f"商品甲（{suffix}）"
        profile["source_listing_ids_official_raw"] = "123-下架"
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        profile = next(iter(repo.profiles().values()))
        assert profile["product_name"] == "商品甲"
        assert profile["field_status"]["product_name"] == "matched"
        assert profile["resolution_method"]["product_name"] == "common_variant_base"
        assert len(profile["candidates"]["product_name"]) == 2
        data = overview(repo, BoardQuery(end, end))
        assert data["metrics"]["revenue"]["value"] == Decimal("23.17")
        assert data["brief"]["products"][0]["product_name"] == "商品甲"


def test_cross_shop_identity_conflicts_and_period_name_fallback(sales_pg, tmp_path):
    from app.modules.product_board.query import options
    from app.modules.product_board.source import normalize

    end, rows = candidate()
    raw = [
        dict(
            z_id=1,
            shop_id="663599807",
            spu_id="123",
            dt="2026-10-25",
            time_granularity="day",
            spu_name="期间原名",
            transaction_amount="10",
        ),
        dict(
            z_id=2,
            shop_id="663599807",
            spu_id="123",
            dt="2026-10-26",
            time_granularity="day",
            spu_name="后日原名",
            transaction_amount="20",
        ),
        dict(
            z_id=3,
            shop_id="799193231",
            spu_id="123",
            dt="2026-10-25",
            time_granularity="day",
            spu_name="另一店同ID",
            transaction_amount="30",
        ),
    ]
    rows["product_daily"] = normalize("product_daily", raw, end)
    rows["profile_records"] = normalize(
        "profile_records",
        [
            dict(__id=1, spigf="123", spmc="资料A", pl="衣"),
            dict(__id=2, spigf="123", spmc="资料B", pl="衣"),
            dict(__id=3, spihw="123", spmc="另一店资料", pl="鞋"),
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        q = BoardQuery(date(2026, 10, 25), date(2026, 10, 25))
        result = overview(repo, q)
        assert result["metrics"]["revenue"]["value"] == Decimal("40")
        assert len(result["brief"]["products"]) == 2
        official = next(r for r in result["brief"]["products"] if r["shop_id"] == "663599807")
        assert official["product_name"] == "期间原名"
        assert official["field_status"]["product_name"] == "multiple"
        scoped = options(repo, BoardQuery(q.start, q.end, shop="663599807", category="衣"))
        assert len(scoped["shops"]) == 2


def test_real_pg_missing_listing_days_remain_partial(sales_pg, tmp_path):
    from app.modules.product_board.source import normalize

    end, rows = candidate()
    rows["product_daily"] = normalize(
        "product_daily",
        [
            dict(
                z_id=1,
                shop_id="663599807",
                spu_id="123",
                dt="2026-10-26",
                time_granularity="day",
                transaction_amount="100",
            ),
            dict(
                z_id=2,
                shop_id="663599807",
                spu_id="123",
                dt="2026-10-19",
                time_granularity="day",
                transaction_amount="100",
            ),
            dict(
                z_id=3,
                shop_id="663599807",
                spu_id="123",
                dt="2025-10-26",
                time_granularity="day",
                transaction_amount="100",
            ),
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        lid = repo.rows(end, end)[0]["listing_id"]
        result = overview(repo, BoardQuery(date(2026, 10, 20), end, products=(lid,)))
        assert result["metrics"]["revenue"]["value"] == Decimal("100")
        assert result["metrics"]["revenue"]["status"] == "partial"
        assert result["metrics"]["revenue"]["mom"] == 0
        assert result["metrics"]["revenue"]["yoy"] == 0
        assert result["health"]["tone"] == "neutral"
