"""店铺经营趋势的真实 PostgreSQL 接口和单日八卡对照。"""

from sqlalchemy import event
from test_sales_extended_api import params, publish, setup
from test_sales_extended_sources import sample

pytest_plugins = ["sales_pg_support"]


def test_performance_trend_matches_daily_cards_and_reads_range_once(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    old = params(client)
    publish(sales_pg, tmp_path, "shop_refunds", [sample("shop_refunds")])
    channel = "shop_channel_product_promotion"
    publish(
        sales_pg,
        tmp_path,
        channel,
        [
            sample(channel),
            sample(
                channel,
                id="next",
                dt="2026-10-27",
                metrics={"总花费(元)": "0", "交易额(元)": None, "曝光量": "0", "点击量": "0"},
            ),
        ],
    )
    star = "shop_channel_star_shop"
    publish(sales_pg, tmp_path, star, [sample(star, cost="2", transaction_amount="20")])

    endpoint = "/api/v1/sales/performance/trend"
    scope = params(client)
    statements = []

    def count_statement(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(sales_pg, "before_cursor_execute", count_statement)
    try:
        result = client.get(endpoint, params=scope)
        range_queries = len(statements)
        statements.clear()
        one = client.get(endpoint, params={**scope, "date_to": scope["date_from"]})
        assert one.status_code == 200
        assert len(statements) == range_queries
    finally:
        event.remove(sales_pg, "before_cursor_execute", count_statement)

    assert result.status_code == 200, result.text
    assert result.json()["meta"]["data_version"] == scope["data_version"]
    for shop in (None, "spigf", "spihw"):
        selected = {**scope, **({"shop_id": shop} if shop else {})}
        response = client.get(endpoint, params=selected)
        points = response.json()["data"]["points"]
        assert [p["date"] for p in points] == ["2026-10-26", "2026-10-27"]
        for point in points:
            day = point["date"]
            cards = client.get(
                "/api/v1/sales/performance",
                params={**selected, "date_from": day, "date_to": day},
            ).json()["data"]
            assert len(point["metrics"]) == 8
            assert all(value["value"] == cards[key] for key, value in point["metrics"].items())
    first, second = result.json()["data"]["points"]
    assert first["metrics"]["refund_rate"] == {
        "value": "0.102000000000",
        "state": "partial",
        "observed": 1,
        "expected": 6,
    }
    assert first["metrics"]["promotion_spend"] == {
        "value": "12.20",
        "state": "partial",
        "observed": 2,
        "expected": 7,
    }
    assert second["metrics"]["shop_revenue"]["state"] == "no_records"
    assert second["metrics"]["promotion_spend"]["value"] == "0.00"
    assert second["metrics"]["promotion_ctr"]["state"] == "zero_denominator"
    assert second["metrics"]["promotion_roi"]["value"] is None
    assert client.get(endpoint, params=old).status_code == 409
    assert client.get(endpoint, params={**scope, "shop_id": "unknown"}).status_code == 422
    assert client.get(endpoint, params={**scope, "date_from": "2026-10-28"}).status_code == 422
