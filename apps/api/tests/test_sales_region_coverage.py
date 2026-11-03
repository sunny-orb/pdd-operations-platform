"""后台地区缺项明细使用真实隔离PG；缺行、空字段、真实0分别验证。"""

from test_sales_extended_api import params, publish, setup
from test_sales_extended_sources import sample

pytest_plugins = ["sales_pg_support"]


def test_region_field_gaps_follow_shop_and_date_without_changing_totals(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    publish(
        sales_pg,
        tmp_path,
        "shop_regions",
        [
            sample("shop_regions", id="known", transaction_amount="10", transaction_users="2"),
            sample(
                "shop_regions",
                id="empty-amount",
                province="江苏",
                transaction_amount=None,
                transaction_orders=None,
                transaction_users="9",
            ),
            sample(
                "shop_regions",
                id="empty-buyers",
                dt="2026-10-27",
                transaction_amount="20",
                transaction_users=None,
            ),
            sample(
                "shop_regions",
                id="zero",
                shop_id="799193231",
                shop_name="kocotree运动户外旗舰店",
                transaction_amount="0",
                transaction_users="0",
                transaction_orders="0",
            ),
        ],
    )
    p = params(client)
    data = client.get("/api/v1/sales/regions", params={**p, "shop_id": "spigf"}).json()["data"]
    assert data["field_gaps"] == [
        {
            "shop_id": "spigf",
            "date": "2026-10-26",
            "fields": {"transaction_amount": 1, "transaction_orders": 1},
        },
        {"shop_id": "spigf", "date": "2026-10-27", "fields": {"transaction_buyers": 1}},
    ]
    assert data["missing_shop_days"] == []
    assert data["totals"] == {
        "transaction_amount": "30.00",
        "transaction_orders": "4",
        "transaction_buyers": "11",
    }
    zhejiang = next(row for row in data["items"] if row["region"] == "浙江")
    assert zhejiang["aov"] == "5.000000000000"  # 仅10元/2人配对，未将20元/空买家混入
    assert zhejiang["share"] == "1.000000000000"

    day = client.get(
        "/api/v1/sales/regions", params={**p, "shop_id": "spigf", "date_from": "2026-10-27"}
    ).json()["data"]
    assert day["field_gaps"] == [data["field_gaps"][1]]
    zero = client.get(
        "/api/v1/sales/regions", params={**p, "shop_id": "spihw", "date_to": "2026-10-26"}
    ).json()["data"]
    assert zero["totals"] == {
        "transaction_amount": "0.00",
        "transaction_orders": "0",
        "transaction_buyers": "0",
    }
    assert zero["field_gaps"] == zero["missing_shop_days"] == []
    assert zero["region_count"] == 1
    assert zero["items"][0]["aov"] is None and zero["items"][0]["share"] is None
    empty = client.get("/api/v1/sales/regions", params={**p, "shop_id": "spixb"}).json()["data"]
    assert all(value is None for value in empty["totals"].values())
    assert empty["region_count"] == 0 and empty["field_gaps"] == []
    assert empty["missing_shop_days"] == ["spixb:2026-10-26", "spixb:2026-10-27"]
