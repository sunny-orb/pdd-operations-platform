"""经营摘要真实PG核验：按天计数、空花费单列，真实零仍参与投产。"""

from datetime import date
from decimal import Decimal

import pytest

from tests.test_promotion_api import client_for, publish_products
from tests.test_promotion_query_performance import promotion_row

pytest_plugins = ["tests.sales_pg_support"]


def test_callout_counts_daily_rows_and_includes_revenue_without_spend(sales_pg, tmp_path):
    day = date(2026, 10, 27)
    publish_products(
        sales_pg,
        tmp_path,
        [
            dict(promotion_row(day, "900", spend="10"), transaction_amount="40"),
            dict(promotion_row(day, "901", spend="0"), transaction_amount="5"),
            dict(promotion_row(day, "902", spend=None), transaction_amount="100.25"),
            dict(promotion_row(date(2026, 10, 25), "903", spend="0"), transaction_amount="0"),
            dict(promotion_row(day, "904", spend="2"), transaction_amount=None),
        ],
    )
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product").json()
        q = dict(
            mode="product",
            date_from="2026-10-25",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        for detail in ("full", "display"):
            result = client.get("/api/v1/promotion/overview", params={**q, "detail": detail})
            assert result.status_code == 200, result.text
            data = result.json()["data"]
            daily = client.get("/api/v1/promotion/days", params=q).json()["data"]
            assert data["coverage"]["observed_days"] == daily["total"] == 2
            assert data["current_record_count"] == "5"  # 原始记录元信息不改变
            assert data["roi_exclusions"] == {
                "missing_spend_rows": 1,
                "missing_revenue_rows": 1,
                "revenue_without_spend": "100.25",
            }
            assert abs(Decimal(data["metrics"]["roi"]["value"]) - Decimal("145.25") / 12) < Decimal(
                "1e-24"
            )
            assert data["metrics"]["roi"]["status"] == "partial"
            assert Decimal(data["metrics"]["revenue"]["value"]) == Decimal("145.25")
            assert Decimal(data["metrics"]["spend"]["value"]) == 12
        empty = client.get("/api/v1/promotion/overview", params={**q, "keyword": "无交集"}).json()[
            "data"
        ]
        assert empty["roi_exclusions"] == {
            "missing_spend_rows": 0,
            "missing_revenue_rows": 0,
            "revenue_without_spend": "0",
        }
        assert empty["coverage"]["observed_days"] == 0
        assert empty["metrics"]["roi"]["value"] is None


def test_period_change_keeps_previous_only_products_and_normal_missing_days(sales_pg, tmp_path):
    publish_products(
        sales_pg,
        tmp_path,
        [
            promotion_row(date(2026, 10, 24), "900", spend="5"),
            promotion_row(date(2026, 10, 25), "901", spend="5"),
            promotion_row(date(2026, 10, 26), "900", spend="20"),
            promotion_row(date(2026, 10, 27), "902", spend=None),
            promotion_row(date(2026, 10, 27), "903", spend="0"),
        ],
    )
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product").json()
        q = dict(
            mode="product",
            date_from="2026-10-26",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        for detail in ("full", "display"):
            response = client.get("/api/v1/promotion/overview", params={**q, "detail": detail})
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            m = data["metrics"]
            assert Decimal(m["spend"]["previous"]) == 10  # 前期独有的901也计入
            assert Decimal(m["spend"]["change"]) == 1
            assert m["spend"]["comparison_status"] == "reference"
            for key in ("impressions", "clicks"):
                assert Decimal(m[key]["change"]) == Decimal("0.5")
                assert m[key]["comparison_status"] == "available"
            assert data["coverage"]["missing_count"] > 0
            assert data["windows"][1] == {
                "role": "previous",
                "start": "2026-10-24",
                "end": "2026-10-25",
            }
        for keyword, before, change in [("900", "5", "3"), ("901", "5", None)]:
            m = client.get("/api/v1/promotion/overview", params={**q, "keyword": keyword}).json()[
                "data"
            ]["metrics"]["spend"]
            assert Decimal(m["previous"]) == Decimal(before)
            if change is None:
                assert m["change"] is None
                assert m["comparison_status"] == "no_current"
            else:
                assert Decimal(m["change"]) == Decimal(change)


@pytest.mark.parametrize(
    "before,now,change,status",
    [
        ("0", "2", None, "zero_base"),
        (None, "2", None, "no_previous"),
        ("1", None, None, "no_current"),
        ("1", "0", "-1", "available"),
        ("1", "1", "0", "available"),
    ],
)
def test_period_change_distinguishes_zero_from_missing(
    sales_pg, tmp_path, before, now, change, status
):
    publish_products(
        sales_pg,
        tmp_path,
        [
            promotion_row(date(2026, 10, 26), spend=before),
            promotion_row(date(2026, 10, 27), spend=now),
        ],
    )
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/promotion/meta?mode=product").json()
        q = dict(
            mode="product",
            date_from="2026-10-27",
            date_to="2026-10-27",
            data_version=meta["meta"]["data_version"],
        )
        m = client.get("/api/v1/promotion/overview", params=q).json()["data"]["metrics"]["spend"]
        assert m["comparison_status"] == status
        if change is None:
            assert m["change"] is None
        else:
            assert Decimal(m["change"]) == Decimal(change)
