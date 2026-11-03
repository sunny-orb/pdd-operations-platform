"""真实PG验证诊断摘要与分页清单同口径，首屏不下载全部商品明细。"""

from decimal import Decimal

import pytest

from tests.test_promotion_api import client_for, publish_products

pytest_plugins = ["tests.sales_pg_support"]


@pytest.mark.parametrize("search", ["¥1.01", "+20.00%", "1,235", "1234.50", "多等级"])
def test_list_search_matches_visible_values_and_rounding(search):
    from app.modules.promotion.diagnosis import object_matches

    item = dict(
        level="A级/B级混合",
        current={
            "cpc": dict(label="CPC", value=Decimal("1.005"), type="currency"),
            "clicks": dict(label="点击量", value=Decimal("1234.5"), type="number"),
            "roi": dict(label="实际投产比", value=Decimal("1234.5"), type="ratio"),
        },
        previous={},
        findings=[],
        notes=[],
        clues=[
            dict(
                label="CTR",
                current=Decimal("0.12"),
                previous=Decimal("0.1"),
                change=Decimal("0.2"),
                evidence="原始变化",
                action="检查",
            )
        ],
    )
    assert object_matches(item, search)


def publish_many(engine, root):
    publish_products(
        engine,
        root,
        [
            dict(
                id=str(i),
                shop_id="663599807",
                spu_id=str(1000 + i),
                dt="2026-10-27",
                start_dt="2026-10-27",
                time_granularity="day",
                transaction_count="1",
                total_cost=str(i + 1),
                transaction_amount=str((i + 1) * 2),
                net_transaction_amount="0",
                impression_count="100",
                click_count="10",
                spu_name=f"验收商品{i}",
            )
            for i in range(27)
        ],
    )


def scope(client):
    meta = client.get("/api/v1/promotion/meta?mode=product").json()
    return dict(
        mode="product",
        date_from="2026-10-27",
        date_to="2026-10-27",
        data_version=meta["meta"]["data_version"],
    )


def test_summary_keeps_full_counts_metrics_without_downloading_lists(sales_pg, tmp_path):
    publish_many(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        q = scope(client)
        full = client.get("/api/v1/promotion/diagnosis", params=q).json()["data"]
        response = client.get("/api/v1/promotion/diagnosis", params={**q, "detail": "summary"})
        assert response.status_code == 200, response.text
        summary = response.json()["data"]
        assert summary["products"] == []
        assert summary["focused_products"] == []
        assert summary["shops"] == []
        assert summary["categories"] == []
        assert summary["counts"]["products"] == 27
        assert Decimal(summary["overall"]["current"]["spend"]["value"]) == 378
        for key in ("counts", "overall", "coverage", "current_window", "previous_window"):
            assert summary[key] == full[key]


def test_objects_paginate_search_full_group_and_enforce_version(sales_pg, tmp_path):
    publish_many(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        q = {**scope(client), "group": "products", "page_size": 25}
        path = "/api/v1/promotion/diagnosis/objects"
        first = client.get(path, params=q)
        assert first.status_code == 200, first.text
        data = first.json()["data"]
        assert (data["total"], data["group_total"], len(data["items"])) == (27, 27, 25)
        assert data["items"][0]["product_id"] == "1026"
        second = client.get(path, params={**q, "page": 2}).json()["data"]
        assert [r["product_id"] for r in second["items"]] == ["1001", "1000"]
        found = client.get(path, params={**q, "search": "验收商品0"}).json()["data"]
        assert found["total"] == 1 and found["group_total"] == 27
        assert found["items"][0]["product_id"] == "1000"
        missing = client.get(path, params={**q, "search": "无此对象"}).json()["data"]
        assert missing["items"] == [] and missing["total"] == 0
        for patch, status in [
            ({"group": "invalid"}, 422),
            ({"page_size": 999}, 422),
            ({"data_version": "stale"}, 409),
        ]:
            assert client.get(path, params={**q, **patch}).status_code == status


def test_alerts_projection_keeps_all_source_findings_order_and_evidence(sales_pg, tmp_path):
    """中心轻量取数只裁响应分组；七项以上不截断，也不重定级或重算。"""
    publish_products(
        sales_pg,
        tmp_path,
        [
            dict(
                id=f"alert-{i}", shop_id="663599807", spu_id=str(2000 + i),
                dt="2026-10-27", start_dt="2026-10-27", time_granularity="day",
                transaction_count="0", total_cost=str(400 + i), transaction_amount="0",
                net_transaction_amount="0", impression_count="100", click_count="10",
                spu_name=f"来源异常{i}",
            )
            for i in range(8)
        ],
    )
    with client_for(sales_pg) as client:
        q = scope(client)
        full = client.get("/api/v1/promotion/diagnosis", params=q).json()["data"]
        response = client.get("/api/v1/promotion/diagnosis", params={**q, "detail": "alerts"})
        assert response.status_code == 200, response.text
        alerts = response.json()["data"]
        assert len(alerts["flagged_products"]) == 8
        for key in ("flagged_products", "counts", "overall", "coverage", "current_window",
                    "previous_window", "rule_version", "rule_states", "status"):
            assert alerts[key] == full[key]
        for key in ("products", "focused_products", "shops", "categories"):
            assert alerts[key] == []
        bad_version = {**q, "detail": "alerts", "data_version": "stale"}
        assert client.get("/api/v1/promotion/diagnosis", params=bad_version).status_code == 409
