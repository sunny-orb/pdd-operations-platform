"""评分发布与GET在真实PG验证；合成事实只写独立测试库。"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from test_m1_ingestion import pg, repo, run, sample  # noqa: F401

from app.core.config import Settings
from app.db import tables as t
from app.main import create_app
from app.modules.products.profiles import ProfileRepository
from app.modules.products.score_repository import ScoreRepository
from app.modules.products.score_standards import import_model
from app.modules.products.scoring import METRICS

SHOP, ID = "663599807", "10000000000000000001"
KEY = f"pdd:{SHOP}:{ID}"


def parameters():
    return {
        "products": {
            KEY: dict(
                category="帽子",
                model_category="帽子",
                season="预热",
                lifecycle="放量期",
                growth_base_floor="10",
                metrics={
                    k: dict(
                        unit=m[2],
                        thresholds=dict(
                            zip(
                                ("basic", "good", "excellent", "outstanding"),
                                ("1", "2", "3", "4"),
                                strict=True,
                            )
                        ),
                        reference_group="合成参照",
                        reference_sample_count=24,
                        fallback_level="group",
                    )
                    for k, m in METRICS.items()
                },
            )
        }
    }


PROVENANCE = dict(
    source_url="https://example.invalid/synthetic",
    resource_url="https://example.invalid/synthetic.js",
    source_as_of="2026-10-20",
    precision="display_rounded",
    observed_at="2026-10-23T00:00:00Z",
)


@pytest.fixture
def scored_db(repo):  # noqa: F811
    repository, engine = repo
    raw = sample()
    p, ad = raw["product_daily"][0], raw["promotion_records"][0]
    raw["product_profile_source"] = raw["product_profile_source"][:1]
    raw["product_daily"] = []
    raw["promotion_records"] = []
    for i in range(14):
        day = str(date(2026, 10, 9) + timedelta(days=i))
        raw["product_daily"].append(
            dict(
                p,
                z_id=day,
                dt=day,
                transaction_amount="10.123456789",
                product_uv=10,
                transaction_users=1,
                transaction_orders=2,
                transaction_products=3,
                favorite_product_users=1,
            )
        )
        if i >= 7:
            for j in (1, 2):
                raw["promotion_records"].append(
                    dict(
                        ad,
                        id=day + str(j),
                        dt=day,
                        start_dt=day,
                        total_cost="1.23456789",
                        transaction_amount="10",
                        impression_count="1000",
                        click_count="20",
                        transaction_count="0.5",
                        promotion_name="方式" + str(j),
                    )
                )
    run(repository, raw)
    ProfileRepository(engine).run()
    model = import_model(engine, parameters(), PROVENANCE)
    app = create_app(
        Settings(database_url=engine.url.render_as_string(hide_password=False), app_env="test")
    )
    with TestClient(app) as client:
        yield client, ScoreRepository(engine), repository, engine, raw, model


def get(client, path="scores", params=None):
    r = client.get("/api/v1/products/" + path, params=params or {})
    assert r.status_code == 200, r.text
    return r.json()


def test_score_read_is_pending_before_background_run_and_never_triggers_write(scored_db):
    client, _, _, engine, _, _ = scored_db
    body = get(client)
    assert body["computation_state"] == "pending" and body["data"]["items"] == []
    assert body["availability"] == "not_connected"
    assert body["meta"]["missing_reasons"]
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.product_scores)) == 0


def test_separate_aggregates_decimal_values_ten_signals_and_exact_weight_accounting(scored_db):
    client, scores, _, engine, _, _ = scored_db
    result = scores.run()
    assert result["products"] == 1 and result["changed"]
    body = get(client)
    assert body["data"]["summary"] == dict(
        total_count=1,
        filtered_count=1,
        scored_count=1,
        complete_count=0,
        partial_count=1,
        unscored_count=0,
    )
    row = body["data"]["items"][0]
    assert row["product_id"] == ID and row["score_status"] == "partial"
    detail = get(client, "scores/" + row["score_id"])["data"]
    assert len(detail["signals"]) == 10
    assert [s["metric_key"] for s in detail["signals"]] == list(METRICS)
    signals = {s["metric_key"]: s for s in detail["signals"]}
    assert signals["product_revenue_7d"]["current_value"] == "70.864197523"
    assert D(signals["score_conversion_rate_7d"]["current_value"]) == D("0.2")
    assert (
        signals["ad_roi_7d"]["current_value"]
        == "8.1000000737100006707610061039251555457189154660421"
    )
    assert detail["inputs"]["promotion"]["rows"] == 14
    assert detail["pending_weight"] == 24 and detail["verified_weight"] == 76
    assert detail["complete_score"] is None and detail["decision"]["code"] == "pending_validation"
    assert (
        detail["context"]["listed_date"] is None
        and detail["context"]["lifecycle_status"] == "unknown"
    )
    assert detail["context"]["first_product_observed_date"] == "2026-10-09"
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.sum(t.product_days.c.product_revenue))) == D(
            "141.728395046"
        )


def test_idempotent_run_and_model_import_do_not_make_duplicate_scores(scored_db):
    client, scores, _, engine, _, model = scored_db
    first = scores.run()
    assert scores.run()["changed"] is False
    assert (
        import_model(engine, parameters(), dict(PROVENANCE, observed_at="2026-10-23T01:00:00Z"))[
            "model_id"
        ]
        == model["model_id"]
    )
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.product_scores)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(t.score_metrics)) == 10
    assert get(client)["meta"]["score_version"] == first["score_version"]


def test_changed_fact_or_profile_invalidates_scores_and_rejects_old_detail(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    scores.run()
    old = get(client)
    row = old["data"]["items"][0]
    data = deepcopy(raw)
    data["product_daily"][-1]["transaction_amount"] = "99"
    run(repository, data)
    ProfileRepository(engine).run()
    assert get(client)["computation_state"] == "pending"
    assert client.get("/api/v1/products/scores/" + row["score_id"]).status_code == 409
    assert (
        client.get(
            "/api/v1/products/scores", params={"data_version": old["meta"]["data_version"]}
        ).status_code
        == 409
    )
    scores.run()
    assert get(client)["data"]["items"][0]["score_id"] != row["score_id"]


def test_missing_ad_days_and_null_values_block_only_dependent_items(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    data = deepcopy(raw)
    data["promotion_records"][0]["transaction_amount"] = None
    # 首次模型未跑；来源修订不是删除，保持所有键。
    run(repository, data)
    ProfileRepository(engine).run()
    scores.run()
    detail = get(client, "scores/" + get(client)["data"]["items"][0]["score_id"])["data"]
    s = {x["metric_key"]: x for x in detail["signals"]}
    assert s["ad_roi_7d"]["points"] is None
    assert s["ad_click_through_rate_7d"]["points"] is not None
    assert s["product_revenue_7d"]["points"] is not None


def test_failure_rolls_back_scores_and_keeps_previous_published_pointer(scored_db):
    client, scores, _, engine, _, _ = scored_db
    scores.run()
    old = get(client)["meta"]["score_version"]
    changed = parameters()
    changed["products"][KEY]["growth_base_floor"] = "11"
    import_model(engine, changed, PROVENANCE)
    with pytest.raises(RuntimeError, match="synthetic"):
        scores.run(before_publish=lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
    with engine.connect() as c:
        assert (
            c.scalar(
                sa.select(t.publication_heads.c.publication_id).where(
                    t.publication_heads.c.scope == "M1_scores"
                )
            )
            is not None
        )
        assert c.scalar(sa.select(sa.func.count()).select_from(t.product_scores)) == 1
    assert get(client)["computation_state"] == "failed"
    assert get(client)["meta"]["previous_score_version"] == old


def test_concurrent_input_change_during_compute_is_not_published(scored_db):
    client, scores, repository, engine, raw, _ = scored_db

    def change():
        data = deepcopy(raw)
        data["product_daily"][-1]["product_uv"] = 99
        run(repository, data)
        ProfileRepository(engine).run()

    result = scores.run(before_publish=change)
    assert result["status"] == "stale"
    assert get(client)["computation_state"] == "pending"
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.product_scores)) == 0


def test_score_scope_filters_decision_pagination_and_invalid_requests(scored_db):
    client, scores, *_ = scored_db
    scores.run()
    assert get(client, params={"decision": "small_scale"})["data"]["summary"]["filtered_count"] == 0
    filters = get(client, "filters", {"scope": "scores"})
    assert filters["data"]["product_total"] == 1
    assert filters["data"]["scope"] == "scores"
    assert (
        get(client, "filters", {"scope": "scores", "decision": "small_scale"})["data"][
            "product_total"
        ]
        == 0
    )
    for params in (
        {"page_size": 101},
        {"decision": "bogus"},
        {"product_id": ID},
        {"as_of_date": "2030-01-01"},
    ):
        assert client.get("/api/v1/products/scores", params=params).status_code in (422, 409)


def test_model_change_rejects_old_score_version_and_category_change_disables_standard(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    scores.run()
    old = get(client)
    new = parameters()
    new["products"][KEY]["growth_base_floor"] = "12"
    import_model(engine, new, PROVENANCE)
    assert (
        client.get(
            "/api/v1/products/scores", params={"score_version": old["meta"]["score_version"]}
        ).status_code
        == 409
    )
    data = deepcopy(raw)
    data["product_profile_source"][0]["pl"] = "手套"
    run(repository, data)
    ProfileRepository(engine).run()
    scores.run()
    item = get(client)["data"]["items"][0]
    assert item["score_status"] == "unscored" and item["verified_score"] is None
    assert item["threshold_status"] == "threshold_profile_category_changed"
    assert D(item["current_values"]["product_revenue_7d"]) == D("70.864197523")


def test_product_without_daily_or_ads_is_retained_as_own_shop_unscored_card(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    data = deepcopy(raw)
    data["product_profile_source"][0]["spihw"] = ID
    run(repository, data)
    ProfileRepository(engine).run()
    scores.run()
    body = get(client, params={"page_size": 1})
    assert body["data"]["summary"]["total_count"] == 2
    assert body["data"]["summary"]["partial_count"] == 1
    second = get(client, params={"page_size": 1, "page": 2})["data"]["items"][0]
    assert second["shop_id"] == "799193231" and second["score_status"] == "unscored"
    assert second["product_id"] == ID and second["current_values"]["visitors_7d"] is None
    assert get(client, params={"shop_id": "799193231"})["data"]["summary"]["filtered_count"] == 1


def test_transaction_mid_write_error_rolls_back_all_candidate_rows(scored_db, monkeypatch):
    client, scores, _, engine, _, _ = scored_db
    scores.run()
    old = get(client)["meta"]["score_version"]
    new = parameters()
    new["products"][KEY]["growth_base_floor"] = "13"
    import_model(engine, new, PROVENANCE)
    original = scores._write

    def broken(*args):
        original(*args)
        raise RuntimeError("synthetic_write_failure")

    monkeypatch.setattr(scores, "_write", broken)
    with pytest.raises(RuntimeError):
        scores.run()
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.product_scores)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(t.score_metrics)) == 10
    assert get(client)["meta"]["previous_score_version"] == old


def test_negative_day_cannot_cancel_into_positive_score_input(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    data = deepcopy(raw)
    data["product_daily"][-1]["transaction_amount"] = "-1"
    run(repository, data)
    ProfileRepository(engine).run()
    scores.run()
    row = get(client)["data"]["items"][0]
    detail = get(client, "scores/" + row["score_id"])["data"]
    s = {x["metric_key"]: x for x in detail["signals"]}
    assert s["product_revenue_7d"]["points"] is None
    assert "product_revenue_negative_input" in s["product_revenue_7d"]["missing_reasons"]
    assert s["revenue_per_visitor_7d"]["current_value"] is None
    assert s["units_7d"]["points"] is not None


def test_ad_observed_gap_cannot_be_inferred_zero_for_roi(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    # 首次接收不完整的一天；不是删除已有行，重建只在独立测试库。
    with engine.begin() as c:
        c.execute(
            t.promotion_records.delete().where(
                t.promotion_records.c.business_date == date(2026, 10, 16)
            )
        )
    scores.run()
    row = get(client)["data"]["items"][0]
    detail = get(client, "scores/" + row["score_id"])["data"]
    s = {x["metric_key"]: x for x in detail["signals"]}
    assert s["ad_roi_7d"]["points"] is None
    assert "promotion_missing_dates" in s["ad_roi_7d"]["missing_reasons"]
    assert s["visitors_7d"]["points"] is not None


def test_actual_new_profile_level_conflict_excluded_from_candidates(scored_db):
    client, scores, repository, engine, raw, _ = scored_db
    data = deepcopy(raw)
    data["product_profile_source"].append(
        dict(data["product_profile_source"][0], __id="level-conflict", cpdj="A级")
    )
    run(repository, data)
    ProfileRepository(engine).run()
    assert scores.run()["products"] == 0
    assert get(client)["data"]["summary"]["total_count"] == 0


def test_published_models_and_score_evidence_reject_in_place_updates(scored_db):
    _, scores, _, engine, _, _ = scored_db
    scores.run()
    for table, column in (
        (t.model_versions, "status"),
        (t.product_scores, "score_status"),
        (t.score_metrics, "status"),
    ):
        with engine.begin() as c, pytest.raises(DBAPIError), c.begin_nested():
            c.execute(table.update().values({column: table.c[column]}))


def test_score_queries_are_read_only_postgresql_transactions(scored_db):
    from app.modules.products.query import ScoreQuery

    _, scores, *_ = scored_db
    with scores.snapshot(ScoreQuery()) as view:
        assert view.c.scalar(sa.text("SHOW transaction_isolation")) == "repeatable read"
        assert view.c.scalar(sa.text("SHOW transaction_read_only")) == "on"
        with pytest.raises(DBAPIError), view.c.begin_nested():
            view.c.execute(t.model_versions.delete())


def test_nonconsecutive_date_rerun_reuses_result_identity_and_evidence(scored_db):
    client, scores, _, engine, _, _ = scored_db
    first = scores.run(date(2026, 10, 22))
    original = get(client)["data"]["items"][0]
    scores.run(date(2026, 10, 21))
    assert get(client)["data"]["as_of_date"] == "2026-10-21"
    third = scores.run(date(2026, 10, 22))
    assert third["score_version"] == first["score_version"]
    assert get(client)["data"]["items"][0]["score_id"] == original["score_id"]
    assert (
        client.get(
            "/api/v1/products/scores/" + original["score_id"],
            params={"score_version": first["score_version"]},
        ).status_code
        == 200
    )
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.product_scores)) == 2
        assert c.scalar(sa.select(sa.func.count()).select_from(t.score_metrics)) == 20
