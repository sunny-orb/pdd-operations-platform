"""日期排序真实PG反例：不舍入排序、同值新日期优先、说明与每日汇总一致。"""

from datetime import date
from decimal import Decimal

from tests.test_promotion_api import client_for, publish_products
from tests.test_promotion_query_performance import promotion_row

pytest_plugins = ["tests.sales_pg_support"]


def test_top_dates_preserve_totals_and_explain_only_the_ranked_day(sales_pg, tmp_path):
    def row(day, product, spend, revenue):
        return dict(
            promotion_row(date(2026, 10, day), product, spend=spend),
            transaction_amount=revenue,
        )

    publish_products(
        sales_pg,
        tmp_path,
        [
            row(19, "900", "10", "40"),
            row(20, "900", "10", "10"),
            row(21, "900", "10", "10"),
            row(22, "900", "10", "10.001"),
            row(23, "900", "10", "20"),
            row(23, "901", "0", "5"),
            row(23, "902", None, "100.25"),
            row(23, "903", "2", None),
            row(24, "900", "0", "10"),
            row(25, "900", None, "100"),
            row(26, "900", "10", None),
            row(27, "900", "10", "30"),
        ],
    )
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product").json()
        q = dict(
            mode="product",
            date_from="2026-10-19",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        for detail in ("full", "display"):
            response = client.get("/api/v1/promotion/overview", params={**q, "detail": detail})
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            top = data["top5"]
            assert [r["date"] for r in top] == [
                "2026-10-21",
                "2026-10-20",
                "2026-10-22",
                "2026-10-27",
                "2026-10-19",
            ]
            daily = {r["date"]: r["metrics"] for r in data["series"]}
            for r in top:
                for key in ("roi", "spend", "revenue"):
                    assert r[key] == daily[r["date"]][key]["value"]
                assert r["roi_status"] == daily[r["date"]]["roi"]["status"]
            partial = client.get(
                "/api/v1/promotion/overview",
                params=q | {"date_from": "2026-10-23", "date_to": "2026-10-23"},
            ).json()["data"]
            assert len(partial["top5"]) == 1
            assert Decimal(partial["top5"][0]["roi"]) == Decimal("10.4375")
            assert partial["top5"][0]["roi_status"] == "partial"
            assert Decimal(partial["metrics"]["roi"]["value"]) == Decimal("10.4375")
            assert Decimal(partial["metrics"]["spend"]["value"]) == 12
            assert Decimal(partial["metrics"]["revenue"]["value"]) == Decimal("125.25")
            assert top[0]["roi_status"] == "observed"
            assert top[0]["roi_exclusions"]["missing_spend_rows"] == 0
        narrowed = client.get("/api/v1/promotion/overview", params={**q, "keyword": "900"}).json()[
            "data"
        ]["top5"]
        assert narrowed[3]["roi_status"] == "observed"
        assert Decimal(narrowed[3]["roi"]) == 2
        for extra in (
            {"date_from": "2026-10-24", "date_to": "2026-10-26"},
            {"keyword": "无此商品"},
        ):
            result = client.get("/api/v1/promotion/overview", params=q | extra).json()["data"]
            assert result["top5"] == []
