"""M3整页查询使用隔离真实PG；断言业务结果和版本一致性。"""

from fastapi.testclient import TestClient
from test_sales_extended_sources import sample, window
from test_sales_source import raw_row

from app.core.config import Settings
from app.main import create_app
from app.modules.sales.datasets import SPECS
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def setup(sales_pg, tmp_path):
    ShopDailyStore(sales_pg, archive_root=tmp_path / "daily", approved=window()).publish_candidate(
        normalize_shop_daily(
            [
                raw_row(
                    metrics={
                        "成交金额": "100",
                        "店铺访客数": "10",
                        "成交买家数": "2",
                        "成交订单数": "3",
                    }
                )
            ],
            window(),
        )
    )
    app = create_app(Settings(database_url=""))
    app.state.engine = sales_pg
    return TestClient(app)


def publish(pg, path, code, rows):
    return ShopDailyStore(
        pg, archive_root=path / code, approved=window(), spec=SPECS[code]
    ).publish_candidate(normalize_source(code, rows, window()))


def params(client):
    return dict(
        date_from="2026-10-26",
        date_to="2026-10-27",
        data_version=client.get("/api/v1/sales/filters").json()["meta"]["data_version"],
    )


def test_vector_refund_and_channels(sales_pg, tmp_path):
    c = setup(sales_pg, tmp_path)
    old = params(c)
    publish(
        sales_pg,
        tmp_path,
        "shop_refunds",
        [sample("shop_refunds", **{"统计退款金额": "20", "统计金额": "200"})],
    )
    assert c.get("/api/v1/sales/performance", params=old).status_code == 409
    publish(
        sales_pg,
        tmp_path,
        "shop_channel_product_promotion",
        [
            sample(
                "shop_channel_product_promotion",
                metrics={"总花费(元)": "10", "交易额(元)": "70", "曝光量": "100", "点击量": "5"},
            )
        ],
    )
    publish(
        sales_pg,
        tmp_path,
        "shop_channel_star_shop",
        [
            sample(
                "shop_channel_star_shop",
                cost="2",
                transaction_amount="20",
                impression_count="10",
                click_count="1",
            )
        ],
    )
    r = c.get("/api/v1/sales/performance", params=params(c))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["refund_rate"] == "0.200000000000"
    assert d["refund_match"]["matched_sales"] == "100.00" and d["refund_match"]["matched_days"] == 1
    assert d["promotion_spend"] == "12.00" and d["promotion_ctr"] == "0.054545454545"
    assert d["promotion_roi"] is None and len(d["promotion_channels"]) == 2
    assert len(r.json()["meta"]["input_versions"]) == 4


def test_rating_latest_does_not_fall_back_and_breaks_old_scores(sales_pg, tmp_path):
    c = setup(sales_pg, tmp_path)
    publish(
        sales_pg,
        tmp_path,
        "shop_ratings",
        [
            sample("shop_ratings", id="a", dt="2026-10-25 00:00:00", shop_rating_score="0.98"),
            sample(
                "shop_ratings",
                id="b",
                dt="2026-10-26 00:00:00",
                shop_id="799193231",
                shop_name="kocotree运动户外旗舰店",
                shop_rating_score="4.8",
            ),
        ],
    )
    r = c.get("/api/v1/sales/ratings", params=params(c))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["latest_date"] == "2026-10-26"
    assert d["latest"][0]["score"] is None
    assert d["latest"][1]["score"] is None and d["latest"][1]["raw_score"] == "4.8"
    assert all(p["score"] is None for p in d["points"])


def test_regions_null_zero_weighted_rates_and_product_keys(sales_pg, tmp_path):
    c = setup(sales_pg, tmp_path)
    publish(
        sales_pg,
        tmp_path,
        "shop_regions",
        [
            sample(
                "shop_regions",
                id="r1",
                transaction_amount="10",
                transaction_orders="2",
                transaction_users="1",
            ),
            sample(
                "shop_regions",
                id="r2",
                transaction_amount="20",
                transaction_orders="8",
                transaction_users="4",
            ),
            sample(
                "shop_regions",
                id="r3",
                province="未知",
                transaction_amount=None,
                transaction_users="9",
            ),
        ],
    )
    r = c.get("/api/v1/sales/regions", params=params(c))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["items"][0]["aov"] == "6.000000000000"
    assert r.json()["data"]["totals"]["transaction_amount"] == "30.00"
    code = "product_regions"
    publish(
        sales_pg,
        tmp_path,
        code,
        [
            sample(
                code,
                id="p1",
                style_code="A",
                product_category="雨衣",
                product_name="同名",
                actual_shipped_amount="10",
                actual_returned_amount="2",
            ),
            sample(
                code,
                id="p2",
                style_code="B",
                product_category="雨衣",
                product_name="同名",
                actual_shipped_amount="0",
                actual_returned_amount=None,
            ),
            sample(
                code,
                id="p3",
                style_code=None,
                product_code=None,
                product_category="雨衣",
                actual_shipped_amount="3",
            ),
            sample(code, id="p4", product_category=" 包-材 ", actual_shipped_amount="999"),
        ],
    )
    p = params(c)
    f = c.get(
        "/api/v1/sales/product-regions/filters",
        params={"data_version": p["data_version"], "search": "B", "limit": 1},
    ).json()["data"]
    assert f["total"] == 1 and len(f["products"]) == 1 and f["categories"] == ["雨衣"]
    d = c.get("/api/v1/sales/product-regions", params=p).json()["data"]
    assert d["totals"]["actual_shipped_amount"] == "13.00"
    assert d["null_counts"]["actual_returned_amount"] == 2
    selected = c.get(
        "/api/v1/sales/product-regions", params={**p, "product_key": f["products"][0]["key"]}
    ).json()["data"]
    assert (
        selected["totals"]["actual_shipped_amount"] == "0.00"
        and selected["totals"]["refund_rate"] is None
    )
    assert (
        c.get("/api/v1/sales/product-regions", params={**p, "product_key": "invalid"}).status_code
        == 422
    )


def test_target_actuals_independent_of_operating_range(sales_pg, tmp_path):
    c = setup(sales_pg, tmp_path)
    v = params(c)["data_version"]
    r = c.get("/api/v1/sales/targets", params={"month": "2026-10", "data_version": v})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["month_actual"] == "100.00" and d["annual_actual"] == "100.00"
    assert d["month_target"] is None and d["state"] == "source_unavailable"
    assert d["month_missing_days"] == 161
    assert d["annual_missing_days"] > d["month_missing_days"]
    assert (
        c.get("/api/v1/sales/targets", params={"month": "bad", "data_version": v}).status_code
        == 422
    )


def test_historical_target_actual_stops_at_selected_month(sales_pg, tmp_path):
    c = setup(sales_pg, tmp_path)
    v = params(c)["data_version"]
    d = c.get("/api/v1/sales/targets", params={"month": "2026-09", "data_version": v}).json()[
        "data"
    ]
    assert d["month_actual"] is None and d["annual_actual"] is None


def test_selected_products_can_be_found_beyond_first_page(sales_pg, tmp_path):
    c = setup(sales_pg, tmp_path)
    code = "product_regions"
    publish(
        sales_pg,
        tmp_path,
        code,
        [
            sample(
                code,
                id=str(i),
                style_code=f"{i:04d}",
                product_name=f"产品{i}",
                product_category="雨具",
            )
            for i in range(220)
        ],
    )
    v = params(c)["data_version"]
    first = c.get("/api/v1/sales/product-regions/filters", params={"data_version": v}).json()[
        "data"
    ]
    assert first["total"] == 220 and len(first["products"]) == 100
    remote = c.get(
        "/api/v1/sales/product-regions/filters", params={"data_version": v, "search": "0219"}
    ).json()["data"]
    assert remote["total"] == 1 and remote["products"][0]["code"] == "0219"
