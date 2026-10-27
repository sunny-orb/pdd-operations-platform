"""经营API真实PG验证：精确原值、不放大、缺数、版本与只读事务。"""

from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_m1_ingestion import pg, repo, run, sample  # noqa: F401

from app.core.config import Settings
from app.main import create_app

SHOP = "663599807"
ID = "10000000000000000001"
QUERY = f"date_from=2026-10-21&date_to=2026-10-22&shop_id={SHOP}"


def facts():
    raw = sample()
    template = raw["product_daily"][0]
    raw["product_daily"] = []
    for day, revenue, uv, buyers in [
        ("2026-10-21", "10.123456789", 10, 2),
        ("2026-10-22", "30.876543211", 30, 3),
        ("2026-10-19", "5", 5, 1),
        ("2026-10-20", "15", 15, 2),
        ("2025-10-21", "8", 8, 2),
        ("2025-10-22", "12", 12, 2),
    ]:
        raw["product_daily"].append(
            dict(
                template,
                z_id=day,
                dt=day,
                product_uv=uv,
                transaction_users=buyers,
                transaction_orders=buyers + 1,
                transaction_products=buyers + 2,
                transaction_amount=revenue,
            )
        )
    raw["product_daily"].append(
        dict(
            template,
            z_id="other",
            dt="2026-10-22",
            shop_id="191467219",
            transaction_amount="1000",
            product_uv=100,
        )
    )
    raw["product_profile_source"][0]["spifs"] = ID
    return raw


@pytest.fixture
def client_db(repo):  # noqa: F811
    repository, engine = repo
    raw = facts()
    run(repository, raw)
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    url = engine.url.render_as_string(hide_password=False)
    app = create_app(Settings(database_url=url, app_env="test"))
    with TestClient(app) as client:
        yield client, repository, engine, raw


def get(client, endpoint="overview", query=QUERY):
    result = client.get(f"/api/v1/products/{endpoint}?{query}")
    assert result.status_code == 200, result.text
    return result.json()


def test_overview_preserves_decimal_and_weighted_rates_without_join_inflation(client_db):
    client, _, _, _ = client_db
    body = get(client)
    metrics = {m["key"]: m for m in body["data"]["metrics"]}
    assert metrics["product_revenue"]["value"] == "41.000000000"
    assert metrics["product_revenue"]["previous"] == "20"
    assert metrics["product_revenue"]["year_ago"] == "20"
    assert metrics["product_revenue"]["mom_change"] == "1.050000000"
    assert metrics["visitors"]["value"] == "40"
    assert metrics["revenue_per_buyer"]["value"] == "8.200000000"
    assert metrics["buyer_conversion_rate"]["value"] == "0.125"
    assert metrics["favorites"]["value"] is None
    assert "definition_pending" in metrics["favorites"]["missing_reasons"]
    assert body["meta"]["completeness"] == "unknown"
    assert body["meta"]["profile_basis"] == "current"
    assert body["data"]["health"]["status"] == "insufficient_evidence"


def test_days_use_shop_identity_source_name_fallback_and_stable_server_pagination(client_db):
    client, _, _, _ = client_db
    first = get(client, "days", QUERY + "&page_size=1")["data"]
    second = get(client, "days", QUERY + "&page_size=1&page=2")["data"]
    assert first["total"] == 2 and second["total"] == 2
    assert first["items"][0]["business_date"] == "2026-10-22"
    assert second["items"][0]["business_date"] == "2026-10-21"
    row = first["items"][0]
    assert row["product_id"] == ID and row["shop_id"] == SHOP
    assert row["product_name"] == "合成商品" and row["level"] is None
    assert row["field_statuses"]["level"] == "conflict"
    assert row["buyer_conversion_rate"] == "0.1"
    assert row["product_revenue"] == "30.876543211"


def test_missing_days_null_values_and_zero_are_not_conflated(client_db):
    client, repository, engine, raw = client_db
    data = deepcopy(raw)
    data["product_daily"][0]["transaction_amount"] = None
    data["product_daily"][1]["transaction_amount"] = "0"
    run(repository, data)
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    overview = get(client)["data"]
    m = next(m for m in overview["metrics"] if m["key"] == "product_revenue")
    assert m["value"] == "0" and m["status"] == "partial"
    assert m["mom_change"] is None and m["missing_value_count"] == 1
    empty = get(client, "overview", f"date_from=2026-10-23&shop_id={SHOP}")
    assert empty["availability"] == "empty"
    assert "no_records_received" in empty["meta"]["missing_reasons"]
    assert all(m["value"] is None for m in empty["data"]["metrics"])


def test_compare_does_not_silently_shorten_31_day_previous_period(client_db):
    client, _, _, _ = client_db
    body = get(client, query=f"date_from=2026-09-22&date_to=2026-10-22&shop_id={SHOP}")
    coverage = body["data"]["coverage"]["previous"]
    assert coverage["not_imported_dates"] == ["2026-08-22", "2026-08-23"]
    assert body["data"]["windows"]["previous"] == {
        "start_date": "2026-08-22",
        "end_date": "2026-09-21",
    }
    assert all(m["mom_change"] is None for m in body["data"]["metrics"])


def test_trend_retains_all_requested_days_gaps_and_calendar_year_mapping(client_db):
    client, _, _, _ = client_db
    body = get(client, "trend", f"date_from=2026-09-01&date_to=2026-10-22&shop_id={SHOP}")
    points = body["data"]["points"]
    assert len(points) == 52
    assert points[0]["value"] is None
    assert points[-1]["value"] == "30.876543211"
    assert points[-1]["year_ago"] == "12"
    leap = get(client, "trend", f"date_from=2024-02-29&shop_id={SHOP}")
    assert leap["data"]["points"][0]["year_ago_date"] is None


def test_filters_are_paginated_and_keyword_is_literal_not_sql_or_wildcards(client_db):
    client, _, _, _ = client_db
    data = get(client, "filters", QUERY + "&page_size=1")["data"]
    assert len(data["products"]) == 1 and data["product_total"] == 1
    assert get(client, "days", QUERY + "&keyword=%25")["data"]["total"] == 0
    assert get(client, "days", QUERY + "&keyword=%27%20OR%201=1--")["data"]["total"] == 0
    assert get(client, "overview", QUERY + "&operator=运营一")["availability"] == "empty"


@pytest.mark.parametrize(
    "query",
    [
        "date_from=bad",
        "date_from=2026-10-22&date_to=2026-10-20",
        "page_size=101",
        f"product_id={ID}",
        "page=0",
        "metric=secret_column",
        "scope=scores",
        "date_from=2020-01-01&date_to=2026-10-22",
        "product_key=bad",
    ],
)
def test_invalid_scope_identity_or_unbounded_request_rejected(client_db, query):
    client, _, _, _ = client_db
    response = client.get("/api/v1/products/overview?" + query)
    assert response.status_code == 422


def test_stale_version_and_unresolved_raw_publication_are_not_mixed(client_db):
    client, repository, engine, raw = client_db
    old = get(client)["meta"]["data_version"]
    raw["product_daily"][0]["transaction_amount"] = "20.123456789"
    run(repository, raw)
    stale = client.get("/api/v1/products/overview?" + QUERY + "&data_version=" + old)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "DATA_VERSION_CHANGED"
    assert client.get("/api/v1/products/overview?" + QUERY).status_code == 503
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    assert get(client)["data"]["metrics"][0]["value"] == "51.000000000"


def test_query_transaction_is_readonly_and_repeatable_across_publish(client_db):
    _, repository, engine, raw = client_db
    from app.modules.products.query import ProductQuery
    from app.modules.products.repository import ProductRepository
    from app.modules.products.service import ProductService

    query = ProductQuery(date_from=date(2026, 10, 21), date_to=date(2026, 10, 22), shop_id=SHOP)
    with ProductRepository(engine).snapshot() as view:
        assert (
            view.connection.execute(text("SHOW transaction_isolation")).scalar_one()
            == "repeatable read"
        )
        assert view.connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        raw["product_daily"][0]["transaction_amount"] = "99"
        run(repository, raw)
        assert ProductService(view, query).overview()["metrics"][0]["value"] == "41.000000000"
        with pytest.raises(DBAPIError):
            view.connection.execute(text("UPDATE product_days SET visitors=999"))


def test_source_failure_metadata_keeps_successful_values_visible(client_db):
    client, _, engine, _ = client_db
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sync_runs(dataset_id,source_id,status,phase,error_code,error_summary) "
                "VALUES ('product_daily','warehouse_bwd_product','failed','failed',"
                "'EXISTING_KEYS_MISSING_IN_SOURCE','本轮未发布，保留旧数据')"
            )
        )
    body = get(client)
    assert body["data"]["metrics"][0]["value"] == "41.000000000"
    assert body["meta"]["sync_status"] == "failed"
    assert body["meta"]["last_error"]["code"] == "EXISTING_KEYS_MISSING_IN_SOURCE"
    assert body["meta"]["last_success_at"] is not None


def test_non_scoring_endpoints_return_real_data_and_rule_evidence(client_db):
    client, _, _, _ = client_db
    for endpoint in ("brief", "alerts", "diagnosis"):
        body = get(client, endpoint)
        assert body["meta"]["is_preview"] is False
    assert get(client, "brief")["data"]["actions"] == []
    assert get(client, "diagnosis")["data"]["generation_method"] == "rules"


def test_partial_numerator_does_not_produce_a_mismatched_ratio(client_db):
    client, repository, engine, raw = client_db
    raw["product_daily"][0]["transaction_users"] = None
    run(repository, raw)
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    metrics = {m["key"]: m for m in get(client)["data"]["metrics"]}
    assert metrics["buyer_conversion_rate"]["value"] is None
    assert metrics["revenue_per_buyer"]["value"] is None
    assert metrics["buyers"]["status"] == "partial"


def test_selected_products_missing_one_link_day_are_not_complete(client_db):
    client, repository, engine, raw = client_db
    extra = dict(raw["product_daily"][0], spu_id="1002", z_id="sparse-two")
    raw["product_daily"].append(extra)
    run(repository, raw)
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    query = QUERY + f"&product_key=pdd:{SHOP}:{ID}&product_key=pdd:{SHOP}:1002"
    body = get(client, query=query)
    assert body["data"]["coverage"]["current"]["all_observed"] is False
    assert body["data"]["metrics"][0]["mom_change"] is None


def test_unassigned_category_is_separate_from_actual_category_top3(client_db):
    client, repository, engine, raw = client_db
    raw["product_profile_source"][1]["pl"] = "围巾"
    run(repository, raw)
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    body = get(client, "brief")["data"]
    assert body["categories"] == []
    assert body["unassigned_category"]["product_revenue"] == "61.000000000"


def test_risk_p0_sorts_before_p1_high_score_and_preserves_evidence(client_db):
    client, repository, engine, raw = client_db
    risk_source(raw)
    run(repository, raw)
    from app.modules.products.profiles import ProfileRepository

    ProfileRepository(engine).run()
    query = f"shop_id={SHOP}&date_from=2026-10-15&date_to=2026-10-22"
    data = get(client, "alerts", query)["data"]
    assert [r["priority"] for r in data["items"]] == ["P0", "P1"]
    assert data["items"][0]["estimated_impact_amount"] == "50.0"
    assert len(data["items"][0]["trend"]) == 7
    assert data["items"][0]["handling_status"] == "not_enabled"
    changed = get(client, "alerts", query.replace("10-15", "10-16"))["data"]
    assert data["items"][0]["id"] != changed["items"][0]["id"]


def risk_source(raw):
    raw["product_profile_source"][1]["cpdj"] = "新品"
    raw["product_profile_source"].append(
        dict(raw["product_profile_source"][0], __id="r3", spigf="1002", spifs=None)
    )
    for offset in range(8):
        day = date(2026, 10, 15) + timedelta(days=offset)
        found = next(
            (
                r
                for r in raw["product_daily"]
                if r["shop_id"] == SHOP and r["spu_id"] == ID and r["dt"].startswith(str(day))
            ),
            None,
        )
        if found is None:
            found = dict(raw["product_daily"][0], dt=str(day), z_id="new-" + str(day))
            raw["product_daily"].append(found)
        found.update(
            product_uv=100 if offset == 7 else 200,
            transaction_orders=10 if offset == 7 else 20,
            transaction_amount="50" if offset == 7 else "100",
        )
        raw["product_daily"].append(
            dict(
                found,
                spu_id="1002",
                z_id="other-" + str(day),
                product_uv=200,
                transaction_orders=0 if offset == 7 else 20,
                transaction_amount="0" if offset == 7 else "10",
            )
        )


def publish_changed(repository, engine, raw):
    from app.modules.products.profiles import ProfileRepository

    run(repository, raw)
    ProfileRepository(engine).run()


def complete_calendar(raw, start, length, shops=(SHOP,)):
    """生成明确的测试日历，独立于生产聚合/覆盖逻辑。"""
    template = raw["product_daily"][0]
    for shop in shops:
        for i in range(length):
            day = str(start + timedelta(days=i))
            if not any(
                r["shop_id"] == shop and r["spu_id"] == ID and r["dt"].startswith(day)
                for r in raw["product_daily"]
            ):
                raw["product_daily"].append(
                    dict(
                        template,
                        shop_id=shop,
                        dt=day,
                        z_id=shop + day,
                        transaction_amount="10",
                        product_uv=100,
                        transaction_users=10,
                    )
                )


def test_review_filtered_comparison_does_not_drop_a_missing_shop(client_db):
    client, repository, engine, raw = client_db
    raw["product_daily"].append(
        dict(
            raw["product_daily"][0],
            shop_id="191467219",
            z_id="prior-other-shop",
            dt="2026-10-20",
            transaction_amount="100",
        )
    )
    publish_changed(repository, engine, raw)
    data = get(client, query="date_from=2026-10-21&category=帽子")["data"]
    assert data["metrics"][0]["mom_change"] is None
    assert data["coverage"]["current"]["expected_shops"] == ["191467219", SHOP]
    assert data["coverage"]["current"]["missing_record_dates"] == ["2026-10-21"]


def test_review_category_rank_detects_missing_shop_day(client_db):
    client, repository, engine, raw = client_db
    complete_calendar(raw, date(2026, 10, 9), 14, (SHOP, "191467219"))
    raw["product_daily"] = [
        r
        for r in raw["product_daily"]
        if not (r["shop_id"] == "191467219" and r["dt"].startswith("2026-10-21"))
    ]
    publish_changed(repository, engine, raw)
    data = get(client, "brief", "date_from=2026-10-16&date_to=2026-10-22&category=帽子")["data"]
    assert data["categories"][0]["change"] is None
    assert data["categories"][0]["status"] == "partial"


def test_review_brief_conversion_uses_matching_nonnull_inputs(client_db):
    client, repository, engine, raw = client_db
    complete_calendar(raw, date(2026, 10, 16), 7)
    raw["product_daily"][0]["transaction_users"] = None
    publish_changed(repository, engine, raw)
    product = get(client, "brief")["data"]["products"][0]
    assert product["buyer_conversion_rate"] is None
    assert product["conversion_status"] == "partial"


@pytest.mark.parametrize("missing", ["one_unknown_amount", "all_amounts_unknown", "one_absent_day"])
def test_review_unknown_candidate_revenue_cannot_create_verified_p0(client_db, missing):
    client, repository, engine, raw = client_db
    risk_source(raw)
    if missing == "all_amounts_unknown":
        for r in raw["product_daily"]:
            if r["shop_id"] == SHOP and "2026-10-15" <= r["dt"][:10] <= "2026-10-22":
                r["transaction_amount"] = None
    elif missing == "one_unknown_amount":
        next(
            r
            for r in raw["product_daily"]
            if r["spu_id"] == "1002" and r["dt"].startswith("2026-10-21")
        )["transaction_amount"] = None
    else:
        raw["product_daily"] = [
            r
            for r in raw["product_daily"]
            if not (r["spu_id"] == "1002" and r["dt"].startswith("2026-10-21"))
        ]
    publish_changed(repository, engine, raw)
    data = get(client, "alerts", f"shop_id={SHOP}&date_from=2026-10-15&date_to=2026-10-22")["data"]
    traffic = next(r for r in data["items"] if r["product_id"] == ID)
    assert traffic["priority"] == "P1"
    assert traffic["priority_status"] == "upgrade_not_verified"
    assert data["ranking_status"] == "insufficient_evidence"


def test_filter_dimensions_do_not_collapse_on_keyword_or_downstream_choice(repo):  # noqa: F811
    repository, engine = repo
    raw = facts()
    from app.modules.products.profiles import ProfileRepository

    raw["product_profile_source"][0]["spifs"] = None
    raw["product_profile_source"][1].update(
        spigf="2002", spifs=None, fzyy="运营二", pl="马甲", cpdj="A级"
    )
    raw["product_daily"].append(dict(raw["product_daily"][0], z_id="second", spu_id="2002"))
    run(repository, raw)
    ProfileRepository(engine).run()
    app = create_app(
        Settings(database_url=engine.url.render_as_string(hide_password=False), app_env="test")
    )
    with TestClient(app) as client:
        data = get(
            client, "filters", QUERY + "&operator=运营一&category=不存在&keyword=没有此商品"
        )["data"]
    assert data["products"] == []
    assert data["operators"] == ["运营一", "运营二"]
    assert data["categories"] == ["帽子"]
