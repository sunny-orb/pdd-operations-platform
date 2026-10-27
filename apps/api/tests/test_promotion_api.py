"""M2真实PG反例：推广独立日期/身份、资料不放大、渠道门禁与精确空值。"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from app.modules.sales.source_normalization import normalize_source
from tests.test_product_board_storage import candidate
from tests.test_sales_extended_sources import sample, window

pytest_plugins = ["tests.sales_pg_support"]


def test_channel_projection_preserves_net_zero_and_fractional_deals():
    raw = sample("shop_channel_product_promotion")
    raw["metrics"].update({"净交易额(元)": "0", "成交笔数": "3.5"})
    row = normalize_source("shop_channel_product_promotion", [raw], window()).rows[0]
    assert row["net_attributed_revenue"] == 0
    assert row["attributed_deals"] == Decimal("3.5")
    star = normalize_source(
        "shop_channel_star_shop", [sample("shop_channel_star_shop")], window()
    ).rows[0]
    assert star["attributed_deals"] == 0 and star["net_attributed_revenue"] is None


def publish_products(engine, root):
    _, rows = candidate()
    end = date(2026, 10, 27)
    base = dict(
        shop_id="663599807",
        spu_id="123",
        dt="2026-10-27",
        start_dt="2026-10-27",
        time_granularity="day",
        transaction_count="1.5",
        total_cost="2",
        transaction_amount="4",
        net_transaction_amount="0",
        impression_count="100.5",
        click_count="10",
        spu_name="仅推广的新名称",
    )
    rows["product_promotion"] = normalize(
        "product_promotion",
        [
            dict(base, id="a", promotion_name="方式A"),
            dict(base, id="b", promotion_name="方式B", total_cost="8", transaction_amount="36"),
            dict(
                base,
                id="c",
                shop_id="799193231",
                total_cost=None,
                transaction_amount="100",
                spu_id="123",
                net_transaction_amount=None,
            ),
        ],
        end,
    )
    ProductBoardStore(engine, root).run(lambda hb: (end, rows))


@contextmanager
def client_for(engine):
    from app.api.promotion import router
    from app.main import create_app

    app = create_app(Settings(_env_file=None, database_url=""))
    if "/api/v1/promotion/meta" not in app.openapi()["paths"]:
        app.include_router(router)
    with TestClient(app) as client:
        app.state.engine = engine
        yield client


def test_product_uses_ad_max_date_and_full_identity_not_operating_scope(sales_pg, tmp_path):
    publish_products(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product")
        assert meta.status_code == 200, meta.text
        assert meta.json()["data"]["date_to"] == "2026-10-27"
        assert meta.json()["data"]["complete_through"] is None
        q = dict(
            mode="product",
            date_from="2026-10-27",
            date_to="2026-10-27",
            data_version=meta.json()["meta"]["data_version"],
        )
        options = client.get("/api/v1/promotion/options", params=q).json()["data"]
        assert len(options["products"]) == 2
        assert len({p["value"] for p in options["products"]}) == 2
        official = next(p for p in options["products"] if p["shop_id"] == "663599807")
        data = client.get(
            "/api/v1/promotion/overview", params={**q, "shop_id": "663599807"}
        ).json()["data"]
        assert Decimal(data["metrics"]["spend"]["value"]) == 10
        assert Decimal(data["metrics"]["revenue"]["value"]) == 40
        assert Decimal(data["metrics"]["roi"]["value"]) == 4
        assert Decimal(data["net"]["net_roi"]["value"]) == 0
        assert data["current_record_count"] == "2"
        assert len(data["funnel"]) == 3
        assert data["top5"][0]["date"] == "2026-10-27"
        selected = client.get(
            "/api/v1/promotion/options",
            params={**q, "keyword": "不存在", "selected_ids": official["value"]},
        ).json()["data"]
        assert (
            selected["products"] == []
            and selected["selected_products"][0]["value"] == official["value"]
        )
        all_data = client.get("/api/v1/promotion/overview", params=q).json()["data"]
        assert all_data["metrics"]["spend"]["status"] == "partial"
        assert Decimal(all_data["metrics"]["roi"]["value"]) == 4  # 成对有效范围
        daily = client.get("/api/v1/promotion/days", params=q).json()["data"]
        assert daily["total"] == 1 and daily["row_grain"] == "day"
        diagnosis = client.get("/api/v1/promotion/diagnosis", params=q).json()["data"]
        assert len(diagnosis["products"]) == 2 and diagnosis["status"] == "insufficient_evidence"
        assert all(not p["findings"] for p in diagnosis["products"])
        for path, params in [
            ("overview", {**q, "data_version": "stale"}),
            ("days", {**q, "page_size": 999}),
            ("overview", {**q, "date_from": "2026-10-28"}),
            ("options", {**q, "selected_ids": "not-a-listing"}),
        ]:
            response = client.get("/api/v1/promotion/" + path, params=params)
            assert response.status_code == (
                409 if params.get("data_version") == "stale" else 422
            ), response.text


def test_store_versions_independent_channel_gate_and_nullable_net(sales_pg, tmp_path):
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.storage import ShopDailyStore

    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        c = normalize_source(code, [sample(code)], window())
        ShopDailyStore(
            sales_pg, archive_root=tmp_path / code, approved=window(), spec=SPECS[code]
        ).publish_candidate(c)
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=store")
        assert meta.status_code == 200, meta.text
        q = dict(
            mode="store",
            date_from="2026-10-26",
            date_to="2026-10-26",
            shop_id="663599807",
            data_version=meta.json()["meta"]["data_version"],
        )
        data = client.get("/api/v1/promotion/overview", params=q).json()["data"]
        assert Decimal(data["metrics"]["spend"]["value"]) == Decimal("10.20")
        assert data["metrics"]["revenue"]["value"] is None
        assert data["metrics"]["roi"]["status"] == "cross_channel_unconfirmed"
        assert data["net"]["net_revenue"]["value"] is None
        single = client.get(
            "/api/v1/promotion/overview", params={**q, "channel": "product_promotion"}
        ).json()["data"]
        assert Decimal(single["net"]["net_revenue"]["value"]) == Decimal("80.80")
        daily = client.get("/api/v1/promotion/days", params=q).json()["data"]
        assert daily["total"] == 2 and daily["row_grain"] == "shop_channel_day"
        assert (
            client.get("/api/v1/promotion/days", params={**q, "page_size": 50}).status_code == 422
        )
        assert (
            client.get("/api/v1/promotion/options", params={**q, "category": "衣"}).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/promotion/options",
                params={**q, "selected_ids": "00000000-0000-0000-0000-000000000001"},
            ).status_code
            == 422
        )
        publish_products(sales_pg, tmp_path / "m1")
        assert client.get("/api/v1/promotion/overview", params=q).status_code == 200


def test_sop_net_zero_can_fall_full_hundred_percent_but_pending_rule_not_rate():
    from app.modules.promotion.diagnosis import assess

    previous = [
        dict(
            spend=Decimal("500"),
            attributed_revenue=Decimal("1000"),
            net_attributed_revenue=Decimal("500"),
            attributed_deals=Decimal("10"),
            impressions=Decimal("1000"),
            clicks=Decimal("100"),
            date=date(2026, 10, 19),
        )
    ]
    current = [dict(previous[0], net_attributed_revenue=Decimal("0"), date=date(2026, 10, 26))]
    confirmed = assess(current, previous, complete=True)
    assert confirmed["current"]["net_roi"]["value"] == 0
    assert [(f["level"], f["rule"]) for f in confirmed["findings"]] == [("P1", "净投产下降≥25%")]
    assert assess(current, previous)["findings"] == []
    growth = [dict(previous[0], spend=Decimal("650"), date=date(2026, 10, 26))]
    pending = assess(growth, previous, complete=True)
    assert not any("花费增长" in f["rule"] for f in pending["findings"])
    assert any(c["status"] == "rule_pending" for c in pending["clues"])


def test_paired_rates_and_store_missing_net_stays_null():
    from app.modules.promotion.query import aggregate

    rows = [
        dict(
            spend=Decimal("10"),
            attributed_revenue=None,
            net_attributed_revenue=Decimal("0"),
            attributed_deals=None,
            impressions=Decimal("100"),
            clicks=Decimal("0"),
        ),
        dict(
            spend=Decimal("20"),
            attributed_revenue=Decimal("100"),
            net_attributed_revenue=None,
            attributed_deals=Decimal("2.5"),
            impressions=None,
            clicks=Decimal("5"),
        ),
    ]
    output = aggregate(rows)
    assert output["roi"]["value"] == 5 and output["roi"]["denominator"] == 20
    assert output["ctr"]["value"] == 0 and output["ctr"]["numerator"] == 0
    assert output["cvr"]["value"] == Decimal(".5")
    assert output["net_roi"]["value"] == 0
    assert aggregate(rows, strict=True)["net_revenue"]["value"] is None
    assert aggregate(rows, strict=True)["roi"]["value"] is None


def test_expected_star_absence_cannot_publish_product_only_net_as_combined_total():
    from app.modules.promotion.query import aggregate

    product_only = [
        dict(
            spend=Decimal("10"),
            attributed_revenue=Decimal("100"),
            net_attributed_revenue=Decimal("80"),
            attributed_deals=Decimal("2"),
            impressions=Decimal("1000"),
            clicks=Decimal("20"),
        )
    ]
    result = aggregate(product_only, strict=True, cross=True)
    assert result["net_revenue"]["value"] is None
    assert result["net_roi"]["value"] is None


def test_unknown_selected_uuid_rejected_for_all_product_endpoints(sales_pg, tmp_path):
    publish_products(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        token = client.get("/api/v1/promotion/meta?mode=product").json()["meta"]["data_version"]
        q = dict(
            mode="product",
            date_from="2026-10-27",
            date_to="2026-10-27",
            data_version=token,
            listing_ids="00000000-0000-0000-0000-000000000001",
        )
        for endpoint in ("overview", "days", "diagnosis", "options"):
            assert client.get("/api/v1/promotion/" + endpoint, params=q).status_code == 422


def test_product_metadata_reads_each_sources_own_observed_dates(sales_pg, tmp_path):
    publish_products(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        sources = {
            r["dataset"]: r
            for r in client.get("/api/v1/promotion/meta?mode=product").json()["data"]["sources"]
        }
        assert sources["product_daily"]["observed_through"] == "2026-10-26"
        assert sources["product_promotion"]["observed_through"] == "2026-10-27"
        assert sources["profile_records"]["date_status"] == "not_applicable"
