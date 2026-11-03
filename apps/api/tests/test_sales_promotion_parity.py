"""两页共用店铺推广计算：真实PG中核对API结果、缺日及独立空字段。"""

from decimal import Decimal
from pathlib import Path

import pytest
from test_sales_extended_api import params, publish, setup
from test_sales_extended_sources import sample

pytest_plugins = ["sales_pg_support"]


@pytest.mark.parametrize(
    "change,want",
    [
        ({}, ("40", "0.15", "2.5")),
        ({"总花费(元)": None}, (None, "0.15", None)),
        ({"交易额(元)": None}, ("40", "0.15", None)),
        ({"点击量": None}, ("40", None, "2.5")),
        ({"曝光量": None}, ("40", None, "2.5")),
    ],
)
def test_sales_cards_and_shop_rows_match_store_promotion(sales_pg, tmp_path, change, want):
    """缺27日不能遮挡26日；空字段只影响对应指标，同日已有值不能伪装全量。"""
    client = setup(sales_pg, tmp_path)
    product = {"总花费(元)": "10", "交易额(元)": "40", "曝光量": "100", "点击量": "10"}
    publish(
        sales_pg,
        tmp_path,
        "shop_channel_product_promotion",
        [
            sample("shop_channel_product_promotion", metrics={**product, **change}),
            sample(
                "shop_channel_product_promotion",
                id="outdoor-27",
                dt="2026-10-27",
                shop_id="799193231",
                shop_name="kocotree运动户外旗舰店",
                metrics={"总花费(元)": "0", "交易额(元)": "0", "曝光量": "0", "点击量": "0"},
            ),
        ],
    )
    publish(
        sales_pg,
        tmp_path,
        "shop_channel_star_shop",
        [
            sample(
                "shop_channel_star_shop",
                cost="30",
                transaction_amount="60",
                impression_count="100",
                click_count="20",
            )
        ],
    )
    q = params(client)
    promo_version = client.get("/api/v1/promotion/meta?mode=store").json()["meta"]["data_version"]
    for shop in ("", "spigf"):
        response = client.get("/api/v1/sales/performance", params={**q, "shop_id": shop or None})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        promotion = client.get(
            "/api/v1/promotion/overview",
            params={
                **q,
                "mode": "store",
                "shop_id": "663599807" if shop else "",
                "data_version": promo_version,
            },
        )
        assert promotion.status_code == 200, promotion.text
        metrics = promotion.json()["data"]["metrics"]
        for name, expected in zip(("spend", "ctr", "roi"), want, strict=True):
            actual = data[f"promotion_{name}"]
            other = metrics[name]["value"]
            if expected is None:
                assert actual is None and other is None
            else:
                assert Decimal(actual) == Decimal(other) == Decimal(expected)
        assert data["shop_revenue"] == "100.00"
    shops = client.get("/api/v1/sales/shops", params=q).json()["data"]["items"]
    official = next(row for row in shops if row["shop_id"] == "spigf")
    assert {
        key: official[key] for key in ("promotion_roi", "promotion_ctr", "promotion_spend")
    } == {key: data[key] for key in ("promotion_roi", "promotion_ctr", "promotion_spend")}
    assert len(shops) == 6


def test_no_rows_and_true_zero_keep_different_amounts(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    publish(
        sales_pg,
        tmp_path,
        "shop_channel_product_promotion",
        [
            sample(
                "shop_channel_product_promotion",
                metrics={
                    "总花费(元)": "0",
                    "交易额(元)": "0",
                    "曝光量": "0",
                    "点击量": "0",
                },
            ),
        ],
    )
    q = params(client)
    zero = client.get("/api/v1/sales/performance", params={**q, "shop_id": "spigf"}).json()["data"]
    empty = client.get("/api/v1/sales/performance", params={**q, "shop_id": "spihw"}).json()["data"]
    assert zero["promotion_spend"] == "0.00" and empty["promotion_spend"] is None
    assert zero["promotion_roi"] is None and empty["promotion_roi"] is None
    assert zero["promotion_ctr"] is None and empty["promotion_ctr"] is None


def test_shared_rule_change_refreshes_both_pages_without_republishing_facts(
    sales_pg, tmp_path, monkeypatch
):
    """只变计算代码时两页旧查询都409，新查询仍读取同一来源版本。"""
    client = setup(sales_pg, tmp_path)
    publish(
        sales_pg,
        tmp_path,
        "shop_channel_product_promotion",
        [sample("shop_channel_product_promotion")],
    )
    scope = dict(date_from="2026-10-26", date_to="2026-10-26")
    endpoints = {
        "/api/v1/sales/filters": (("performance", "shops"), scope),
        "/api/v1/promotion/meta?mode=store": (("overview", "days"), {**scope, "mode": "store"}),
    }
    baseline = {}
    for meta_url, (paths, query) in endpoints.items():
        metadata = client.get(meta_url).json()
        meta = metadata["meta"]
        root = meta_url.rsplit("/", 1)[0]
        data = {}
        for path in paths:
            response = client.get(
                f"{root}/{path}", params={**query, "data_version": meta["data_version"]}
            )
            assert response.status_code == 200, response.text
            data[path] = response.json()["data"]
        baseline[meta_url] = (meta, root, data, metadata["data"].get("sources"))

    read_bytes = Path.read_bytes

    def revised_rule(path):
        raw = read_bytes(path)
        return raw + b"\n# rule revision\n" if str(path).endswith("promotion/metrics.py") else raw

    monkeypatch.setattr(Path, "read_bytes", revised_rule)
    for meta_url, (paths, query) in endpoints.items():
        old, root, data, sources = baseline[meta_url]
        metadata = client.get(meta_url).json()
        new = metadata["meta"]
        assert new["data_version"] != old["data_version"]
        if "input_versions" in old:
            assert new["input_versions"] == old["input_versions"]
        assert metadata["data"].get("sources") == sources
        for path in paths:
            assert (
                client.get(
                    f"{root}/{path}", params={**query, "data_version": old["data_version"]}
                ).status_code
                == 409
            )
            fresh = client.get(
                f"{root}/{path}", params={**query, "data_version": new["data_version"]}
            )
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()["data"] == data[path]
