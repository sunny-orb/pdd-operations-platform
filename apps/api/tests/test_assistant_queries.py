"""单款问答在真实隔离 PostgreSQL 使用与看板相同的发布事实。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.product_board.repository import BoardError
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore

pytest_plugins = ["tests.sales_pg_support"]

END = date(2026, 10, 26)
SHOP = "663599807"
OTHER_SHOP = "799193231"


@pytest.fixture
def published(sales_pg, tmp_path):
    daily = []
    ads = []
    for sid in (SHOP, OTHER_SHOP):
        for offset in range(14):
            if sid == SHOP and offset == 5:
                continue
            day = END - timedelta(days=offset)
            daily.append(
                dict(
                    z_id=len(daily) + 1,
                    shop_id=sid,
                    spu_id="123",
                    spu_name="同名商品",
                    dt=str(day),
                    time_granularity="day",
                    product_uv=100,
                    transaction_users=20,
                    transaction_orders=20,
                    transaction_amount="999" if sid == OTHER_SHOP else "10",
                )
            )
            # 商品记录覆盖至26日；推广25日后无行，不得悄悄把推广七天往前推。
            if offset:
                ads.append(
                    dict(
                        id=str(len(ads) + 1),
                        shop_id=sid,
                        spu_id="123",
                        spu_name="同名商品",
                        dt=str(day),
                        start_dt=str(day),
                        time_granularity="day",
                        total_cost="999" if sid == OTHER_SHOP else "3",
                        transaction_amount="12",
                        impression_count="10",
                        click_count="2",
                        transaction_count="1",
                    )
                )
    rows = {
        "product_daily": normalize("product_daily", daily, END),
        "product_promotion": normalize("product_promotion", ads, END),
        "profile_records": normalize(
            "profile_records", [dict(__id=1, spigf="123", spmc="同名商品", cpdj="清仓")], END
        ),
    }
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, rows))
    return sales_pg


def test_search_preserves_each_store_identity_and_exact_matches(published):
    from app.modules.assistant.queries import search_products

    products = search_products(published, "同名商品")
    assert len(products) == len({p["listing_id"] for p in products}) == 2
    assert {p["shop_id"] for p in products} == {SHOP, OTHER_SHOP}
    first = next(p for p in products if p["shop_id"] == SHOP)
    assert first["name"] == "同名商品" and first["product_id"] == "123"
    assert search_products(published, "123", shop_id=SHOP) == [first]
    assert search_products(published, first["listing_id"]) == [first]
    assert search_products(published, "不存在") == []
    assert search_products(published, "  ") == []


def test_performance_compares_adjacent_days_and_preserves_missing(published):
    from app.modules.assistant.queries import product_performance, search_products

    (product,) = search_products(published, "123", shop_id=SHOP)
    report = product_performance(published, product["listing_id"])
    assert report["product"] == product
    assert report["period"] == dict(
        date_from="2026-10-20",
        date_to="2026-10-26",
        previous_from="2026-10-13",
        previous_to="2026-10-19",
    )
    metrics = {m["key"]: m for m in report["metrics"]}
    assert Decimal(metrics["revenue"]["current"]) == 60
    assert Decimal(metrics["revenue"]["previous"]) == 70
    assert Decimal(metrics["conversion"]["current"]) == Decimal(".2")
    assert report["coverage"]["current"]["missing_dates"] == ["2026-10-21"]
    assert report["coverage"]["complete_through"] is None
    assert report["data_version"] and len(report["series"]) == 7
    assert report["series"][1]["values"]["revenue"] is None
    assert report["evidence"]["rules"]["status"] == "not_applicable"


def test_promotion_keeps_product_dates_and_compares_only_same_store(published):
    from app.modules.assistant.queries import product_promotion, search_products

    (product,) = search_products(published, "123", shop_id=SHOP)
    report = product_promotion(published, product["listing_id"])
    assert report["product"] == product and report["kind"] == "product_promotion"
    assert report["period"]["date_to"] == "2026-10-26"
    assert report["coverage"]["current"]["missing_dates"] == ["2026-10-21", "2026-10-26"]
    metrics = {m["key"]: m for m in report["metrics"]}
    assert Decimal(metrics["spend"]["current"]) == 15
    assert Decimal(metrics["spend"]["previous"]) == 21
    assert Decimal(metrics["roi"]["current"]) == 4
    assert metrics["roi"]["change"] is None
    assert report["series"][-1]["values"]["spend"] is None
    assert report["evidence"]["rules"]["date_to"] == "2026-10-25"
    selected = product_promotion(published, product["listing_id"], date_from=END, date_to=END)
    assert selected["period"]["previous_from"] == "2026-10-25"
    assert all(m["current"] is None for m in selected["metrics"])


def test_rejects_invalid_range_future_and_non_listing_identifiers(published):
    from app.modules.assistant.queries import (
        product_performance,
        product_promotion,
        search_products,
    )

    (product,) = search_products(published, "123", shop_id=SHOP)
    for query in (product_performance, product_promotion):
        for start, end in (
            (END, None),
            (None, END),
            (END, END - timedelta(days=1)),
            (END + timedelta(days=1), END + timedelta(days=2)),
            (END - timedelta(days=31), END),
        ):
            with pytest.raises(BoardError, match="INVALID_SCOPE"):
                query(published, product["listing_id"], date_from=start, date_to=end)
        with pytest.raises(BoardError, match="UNKNOWN_LISTING_ID"):
            query(published, "123")


def test_triggered_product_rule_and_absent_promotion_keep_their_own_state(sales_pg, tmp_path):
    from app.modules.assistant.queries import (
        product_performance,
        product_promotion,
        search_products,
    )
    from tests.test_product_board_storage import candidate

    _, rows = candidate()
    rows["product_daily"] = normalize(
        "product_daily",
        [
            dict(
                z_id=i + 1,
                shop_id=SHOP,
                spu_id="123",
                dt=str(END - timedelta(days=i)),
                time_granularity="day",
                product_uv=200,
                transaction_orders=0 if i == 0 else 5,
                transaction_amount="0" if i == 0 else "120",
                transaction_users=0 if i == 0 else 5,
            )
            for i in range(8)
        ],
        END,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, rows))
    (product,) = search_products(sales_pg, "123", shop_id=SHOP)
    report = product_performance(sales_pg, product["listing_id"], date_from=END, date_to=END)
    revenue = next(m for m in report["metrics"] if m["key"] == "revenue")
    assert Decimal(revenue["current"]) == 0 and Decimal(revenue["previous"]) == 120
    rule = report["evidence"]["rules"]
    assert rule["status"] == "evaluated" and rule["date_to"] == str(END)
    assert rule["baseline_from"] == "2026-10-19" and rule["baseline_to"] == "2026-10-25"
    (finding,) = rule["findings"]
    assert finding["label"] == "有流量无成交" and finding["action"]
    assert "同店" in rule["ranking_basis"] and "20%" in rule["ranking_basis"]
    ads = product_promotion(sales_pg, product["listing_id"])
    assert ads["coverage"]["current"]["observed_days"] == 0
    assert ads["coverage"]["previous"]["observed_days"] == 0
    assert all(m["current"] is None and m["previous"] is None for m in ads["metrics"])
    assert ads["evidence"]["rules"]["status"] == "insufficient_evidence"


def test_recent_days_uses_published_product_date_in_both_domains(published):
    from app.modules.assistant.queries import (
        product_performance,
        product_promotion,
        search_products,
    )

    (product,) = search_products(published, "123", shop_id=SHOP)
    for query in (product_performance, product_promotion):
        report = query(published, product["listing_id"], recent_days=14)
        assert report["period"] == dict(
            date_from="2026-10-13",
            date_to="2026-10-26",
            previous_from="2026-09-29",
            previous_to="2026-10-12",
        )
        assert len(report["series"]) == 14
        for invalid in (0, 32, True):
            with pytest.raises(BoardError, match="INVALID_SCOPE"):
                query(published, product["listing_id"], recent_days=invalid)


def test_partial_product_subtotal_survives_stricter_comparison(sales_pg, tmp_path):
    from app.modules.assistant.queries import product_performance, search_products
    from tests.test_product_board_storage import candidate

    _, rows = candidate()
    rows["product_daily"] = normalize(
        "product_daily",
        [
            dict(
                z_id=i + 1,
                shop_id=SHOP,
                spu_id="123",
                dt=str(END - timedelta(days=i)),
                time_granularity="day",
                transaction_amount=None if i == 0 else "20",
                product_uv=5,
                transaction_users=1 if i else None,
            )
            for i in range(4)
        ],
        END,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, rows))
    (product,) = search_products(sales_pg, "123", shop_id=SHOP)
    report = product_performance(sales_pg, product["listing_id"], recent_days=2)
    metrics = {m["key"]: m for m in report["metrics"]}
    assert Decimal(metrics["revenue"]["current"]) == 20
    assert Decimal(metrics["revenue"]["previous"]) == 40
    assert Decimal(metrics["conversion"]["current"]) == Decimal(".2")
    assert metrics["revenue"]["status"] == "partial" and metrics["revenue"]["reason"]
    assert metrics["revenue"]["change"] is None and metrics["conversion"]["change"] is None


def test_pinned_id_skips_whole_directory_and_still_checks_store(published, monkeypatch):
    from app.modules.assistant.queries import search_products
    from app.modules.product_board.repository import Repository

    (product,) = search_products(published, "123", shop_id=SHOP)

    def reject_catalog(*args):
        pytest.fail("已固定店铺商品，无需读取全目录")

    monkeypatch.setattr(Repository, "identity_rows", reject_catalog)
    assert search_products(published, product["listing_id"]) == [product]
    assert search_products(published, product["listing_id"], shop_id=OTHER_SHOP) == []
    assert search_products(published, "6a2c4433-fa93-4923-9a9e-94dfd7bc4cb6") == []


def test_promotion_page_date_and_promotion_only_listing(sales_pg, tmp_path):
    from app.modules.assistant.queries import (
        product_performance,
        product_promotion,
        search_products,
    )
    from tests.test_promotion_api import publish_products

    publish_products(sales_pg, tmp_path)
    hits = search_products(sales_pg, "123")
    assert {p["shop_id"] for p in hits} == {SHOP, OTHER_SHOP}
    ad_only = next(p for p in hits if p["shop_id"] == OTHER_SHOP)
    assert search_products(sales_pg, ad_only["listing_id"], shop_id=OTHER_SHOP) == [ad_only]
    assert search_products(sales_pg, ad_only["listing_id"], shop_id=SHOP) == []
    day = END + timedelta(days=1)
    for hit in hits:
        report = product_promotion(sales_pg, hit["listing_id"], date_from=day, date_to=day)
        revenue = next(m for m in report["metrics"] if m["key"] == "revenue")
        assert Decimal(revenue["current"]) == (40 if hit["shop_id"] == SHOP else 100)
        assert report["coverage"]["current"]["observed_days"] == 1
    empty = product_performance(sales_pg, ad_only["listing_id"], date_from=day, date_to=day)
    assert empty["coverage"]["current"]["missing_dates"] == [str(day)]
    assert all(m["current"] is None for m in empty["metrics"])
    assert empty["evidence"]["rules"]["status"] == "empty"
    assert product_promotion(sales_pg, ad_only["listing_id"])["period"]["date_to"] == str(END)


def test_promotion_comparisons_distinguish_zero_base_and_unavailable_inputs(sales_pg, tmp_path):
    from app.modules.assistant.queries import product_promotion, search_products
    from tests.test_promotion_api import publish_products

    rows = [
        dict(
            id=str(offset),
            shop_id=SHOP,
            spu_id="123",
            dt=str(END - timedelta(days=offset)),
            start_dt=str(END - timedelta(days=offset)),
            time_granularity="day",
            total_cost="10" if not offset else "0",
            transaction_amount="20" if not offset else "0",
            transaction_count="2",
            click_count=None if offset else "10",
            impression_count="100",
        )
        for offset in (0, 1)
    ]
    publish_products(sales_pg, tmp_path, rows)
    (product,) = search_products(sales_pg, "123")
    report = product_promotion(sales_pg, product["listing_id"], date_from=END, date_to=END)
    metrics = {m["key"]: m for m in report["metrics"]}
    for key in ("spend", "revenue"):
        assert metrics[key]["status"] == "zero_baseline"
        assert "前期为0" in metrics[key]["reason"] and metrics[key]["change"] is None
        assert Decimal(metrics[key]["previous"]) == 0
    assert metrics["roi"]["status"] == "zero_denominator"
    assert "前期" in metrics["roi"]["reason"] and "分母为零" in metrics["roi"]["reason"]
    assert metrics["roi"]["current"] is not None and metrics["roi"]["previous"] is None
    assert metrics["cvr"]["status"] == "missing"
    assert "前期" in metrics["cvr"]["reason"] and "字段" in metrics["cvr"]["reason"]


def test_each_report_locks_both_page_versions(published):
    from app.modules.assistant.queries import (
        product_performance,
        product_promotion,
        search_products,
    )
    from app.modules.product_board.repository import snapshot as product_snapshot
    from app.modules.promotion.repository import snapshot as promotion_snapshot

    [product] = search_products(published, "123", shop_id=SHOP)
    with product_snapshot(published) as board, promotion_snapshot(published, "product") as ads:
        versions = {"product_version": board.token, "promotion_version": ads.token}
    for query in (product_performance, product_promotion):
        report = query(published, product["listing_id"])
        assert report["versions"] == versions
