"""来源监测真实PG反例；外部读取在连接器边界注入，发布和状态使用真实库。"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from tests.test_weather_pg import client_for

pytest_plugins = ["tests.sales_pg_support"]


def test_catalog_merges_physical_sources_and_keeps_dependencies():
    from app.modules.data_status.catalog import SOURCES

    assert len(SOURCES) == len({s.locator for s in SOURCES}) == 16
    sales = next(s for s in SOURCES if s.id == "jst_sales")
    assert sales.datasets == ("jst_shipped_sales", "jst_cancelled_sales")
    assert any(s.id == "jst_master" for s in SOURCES)
    assert len([s for s in SOURCES if s.system == "feishu"]) == 2


def test_web_time_and_successful_observation_survive_check_failure(sales_pg):
    from app.jobs.source_monitor import run_monitor
    from app.modules.data_status.web_observations import record_web_updates

    stamp = datetime.now(UTC) - timedelta(hours=1)
    run_monitor(
        sales_pg,
        {"shop_daily": lambda: {"date": "2026-11-02"}},
        lambda groups, c: dict.fromkeys(groups, True),
        now=stamp,
    )
    record_web_updates(
        sales_pg,
        [
            {
                "tableId": "cAc3IVT4i3",
                "table": "ods_rpa_pdd_traffic_kanban_overview_f",
                "updated": "2026-11-03 10:02:10",
            }
        ],
        stamp,
    )

    def failed():
        raise ValueError("private")

    run_monitor(
        sales_pg, {"shop_daily": failed}, lambda groups, c: {}, now=stamp + timedelta(minutes=16)
    )
    with client_for(sales_pg) as client:
        item = client.get("/api/v1/data-status").json()["data"]["sources"][0]
    assert item["table_name"] == "ods_rpa_pdd_traffic_kanban_overview_f"
    assert item["source_url"].endswith("tableId=cAc3IVT4i3")
    assert item["source_title"] == "ODS_流量_拼多多店铺流量概览表(拼多多RPA)"
    assert item["upstream"]["web_updated_at"] == "2026-11-03 10:02:10"
    assert datetime.fromisoformat(item["upstream"]["web_checked_at"]) == stamp
    assert datetime.fromisoformat(item["upstream"]["observed_at"]) == stamp
    assert datetime.fromisoformat(item["upstream"]["checked_at"]) == stamp + timedelta(minutes=16)
    assert item["upstream"]["state"] == "failed"
    assert item["upstream"]["observation"]["date"] == "2026-11-02"


def test_web_evidence_rejects_wrong_table_and_older_batch_atomically(sales_pg):
    import pytest

    from app.jobs.source_monitor import run_monitor
    from app.modules.data_status.web_observations import record_web_updates

    stamp = datetime.now(UTC)
    run_monitor(
        sales_pg,
        {"shop_daily": lambda: {"date": "2026-11-02"}},
        lambda groups, c: dict.fromkeys(groups, True),
        now=stamp,
    )
    valid = {
        "tableId": "cAc3IVT4i3",
        "table": "ods_rpa_pdd_traffic_kanban_overview_f",
        "updated": "2026-11-03 10:02:10",
    }
    with pytest.raises(ValueError):
        record_web_updates(sales_pg, [valid, {**valid, "tableId": "wrong-table"}], stamp)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT web_updated_at FROM source_checkpoints")) is None
        original = c.execute(
            sa.text("SELECT observed_fingerprint,accepted_fingerprint FROM source_checkpoints")
        ).one()
    record_web_updates(sales_pg, [valid], stamp)
    with pytest.raises(ValueError):
        record_web_updates(
            sales_pg, [{**valid, "updated": "2026-11-02 10:02:10"}], stamp - timedelta(minutes=1)
        )
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT observed_fingerprint,accepted_fingerprint FROM source_checkpoints")
            ).one()
            == original
        )
        assert (
            c.scalar(sa.text("SELECT web_updated_at FROM source_checkpoints")) == valid["updated"]
        )


def test_monitor_first_change_no_change_and_due_budget(sales_pg):
    from app.jobs.source_monitor import run_monitor

    stamp = datetime.now(UTC)
    value, executed = {"date": "2026-11-02", "count": 10}, []

    def sync(groups, connection):
        executed.extend(groups)
        return {group: True for group in groups}

    probe = {"shop_daily": lambda: value.copy()}
    run_monitor(sales_pg, probe, sync, now=stamp)
    assert executed == ["shop_daily"]
    run_monitor(sales_pg, probe, sync, now=stamp + timedelta(seconds=1))
    run_monitor(sales_pg, probe, sync, now=stamp + timedelta(minutes=16))
    assert executed == ["shop_daily"]
    value["count"] = 11  # 同日新增也要接收，不只比较最大日期。
    run_monitor(sales_pg, probe, sync, now=stamp + timedelta(minutes=32))
    assert executed == ["shop_daily", "shop_daily"]


def test_failed_sync_keeps_accepted_marker_and_retries(sales_pg):
    from app.jobs.source_monitor import run_monitor

    stamp = datetime.now(UTC)
    value = {"date": "2026-11-02", "count": 10}
    probes = {"shop_daily": lambda: value.copy()}
    run_monitor(sales_pg, probes, lambda groups, c: dict.fromkeys(groups, True), now=stamp)
    value["count"] = 12
    run_monitor(
        sales_pg,
        probes,
        lambda groups, c: dict.fromkeys(groups, False),
        now=stamp + timedelta(minutes=16),
    )
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["observed_fingerprint"] != row["accepted_fingerprint"]
        assert row["error_code"] == "SYNC_FAILED"
    early = run_monitor(
        sales_pg,
        probes,
        lambda groups, c: {},
        now=stamp + timedelta(minutes=20, seconds=59),
    )
    assert early["checked"] == 0

    def temporarily_unavailable():
        raise ValueError("synthetic probe failure")

    run_monitor(
        sales_pg,
        {"shop_daily": temporarily_unavailable},
        lambda groups, c: {},
        now=stamp + timedelta(minutes=21),
    )
    run_monitor(
        sales_pg,
        probes,
        lambda groups, c: dict.fromkeys(groups, True),
        now=stamp + timedelta(minutes=26),
    )
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["observed_fingerprint"] == row["accepted_fingerprint"]
        assert row["error_code"] is None


def test_change_during_sync_not_acknowledged_and_other_probe_failure_isolated(sales_pg):
    from app.jobs.source_monitor import run_monitor

    value = {"date": "2026-11-02", "count": 10}

    def failed():
        raise ValueError("private connection string")

    def sync(groups, connection):
        value["count"] += 1
        return dict.fromkeys(groups, True)

    run_monitor(sales_pg, {"shop_daily": lambda: value.copy(), "shop_ratings": failed}, sync)
    with sales_pg.connect() as c:
        rows = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().all()
        by_id = {r["source_id"]: r for r in rows}
        assert by_id["shop_daily"]["accepted_fingerprint"] is None
        assert by_id["shop_daily"]["error_code"] == "SOURCE_CHANGED_DURING_SYNC"
        assert by_id["shop_ratings"]["check_status"] == "failed"
        assert "private" not in str(rows)


def test_monitor_obeys_project_lock_and_readonly_api(sales_pg):
    from app.jobs.source_monitor import run_monitor

    with sales_pg.connect() as c:
        c.execute(sa.text("SELECT pg_advisory_lock(hashtext('project-source-sync'))"))
        c.commit()
        result = run_monitor(sales_pg, {}, lambda groups, conn: {})
        assert result["status"] == "already_running"
        c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
        c.commit()
    with client_for(sales_pg) as client:
        response = client.get("/api/v1/data-status")
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["sources"]) == 16
        assert all(row["upstream"]["state"] == "unchecked" for row in data["sources"])
        assert all(row["platform_state"] == "unavailable" for row in data["sources"])
        assert client.post("/api/v1/data-status").status_code == 405
    with sales_pg.connect() as c:
        assert c.scalar(
            sa.text("SELECT has_table_privilege('pdd_app','source_checkpoints','SELECT')")
        )
        assert not c.scalar(
            sa.text("SELECT has_table_privilege('pdd_app','source_checkpoints','UPDATE')")
        )


def test_state_api_separates_stale_check_from_sync_and_reports_weather(sales_pg, tmp_path):
    from app.jobs.source_monitor import run_monitor
    from app.jobs.sync_weather import sync_weather
    from tests.test_weather import sources

    sync_weather(sales_pg, sources, tmp_path)
    run_monitor(
        sales_pg,
        {"weather_bulletin": lambda: {"date": "2026-10-28"}},
        lambda groups, c: dict.fromkeys(groups, True),
        now=datetime.now(UTC) - timedelta(hours=2),
    )
    with client_for(sales_pg) as client:
        data = client.get("/api/v1/data-status").json()["data"]
        row = next(r for r in data["sources"] if r["id"] == "weather_bulletin")
        assert row["upstream"]["stale"] is True
        assert row["datasets"][0]["received_at"] is not None
        assert row["platform_state"] == "available"
        assert len(data["processing"]) == 3


def test_unchanged_input_still_allows_pending_processing_retry(sales_pg):
    from app.jobs.source_monitor import run_monitor

    stamp = datetime.now(UTC)
    called = []

    def sync(groups, connection):
        called.append(groups)
        return dict.fromkeys(groups, True)

    probes = {"shop_daily": lambda: {"date": "2026-11-02"}}
    run_monitor(sales_pg, probes, sync, now=stamp)
    run_monitor(sales_pg, probes, sync, now=stamp + timedelta(minutes=16))
    assert called == [("shop_daily",), ()]


def test_processing_tracks_input_date_and_retries_changed_dependencies(sales_pg, tmp_path):
    from app.jobs.source_monitor_runtime import pending_processing
    from app.modules.product_board.storage import ProductBoardStore
    from app.modules.product_scoring.publisher import publish_scores
    from tests.test_product_scoring_publication import inputs

    store = ProductBoardStore(sales_pg, tmp_path / "source")
    store.run(lambda hb: inputs())
    with sales_pg.begin() as c:
        assert pending_processing(c) == ["product_scores"]
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    with sales_pg.begin() as c:
        assert pending_processing(c) == []
    with client_for(sales_pg) as client:
        item = client.get("/api/v1/data-status").json()["data"]["processing"][0]
        assert item["date"] == "2026-10-26"
        assert item["state"] == "available"
    end, rows = inputs()
    rows["product_daily"][0]["product_revenue"] += 1
    store.run(lambda hb: (end, rows))
    with sales_pg.begin() as c:
        assert pending_processing(c) == ["product_scores"]
    with client_for(sales_pg) as client:
        assert (
            client.get("/api/v1/data-status").json()["data"]["processing"][0]["state"] == "pending"
        )


def test_expired_worker_is_interrupted_and_unchanged_source_retries(sales_pg):
    from app.jobs.source_monitor import run_monitor

    stamp = datetime.now(UTC) - timedelta(hours=2)
    probes = {"shop_daily": lambda: {"date": "2026-11-02"}}
    run_monitor(sales_pg, probes, lambda groups, c: dict.fromkeys(groups, True), now=stamp)
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO task_runs
          (id,job_code,request_ref,lock_key,status,queued_at,started_at,worker_ref,lease_expires_at)
          VALUES ('expired','sync_shop_daily','test','expired','running',:at,:at,'gone',:lease)
        """),
            {"at": stamp, "lease": stamp + timedelta(minutes=5)},
        )
    with client_for(sales_pg) as client:
        row = client.get("/api/v1/data-status").json()["data"]["sources"][0]
        assert row["platform_state"] == "failed"
        assert row["datasets"][0]["attempt_status"] == "abandoned"
    called = []

    def sync(groups, c):
        called.extend(groups)
        return dict.fromkeys(groups, True)

    run_monitor(sales_pg, probes, sync)
    assert called == ["shop_daily"]


def test_monitor_failure_is_reported_in_batch_result(sales_pg):
    from app.jobs.source_monitor import run_monitor

    def fail():
        raise ValueError("private detail")

    result = run_monitor(sales_pg, {"shop_daily": fail}, lambda groups, c: {})
    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert result["checked"] == 1
    assert "private" not in str(result)


def test_new_optional_target_requires_report_refresh(sales_pg, tmp_path):
    from datetime import date

    from app.jobs.source_monitor_runtime import pending_processing
    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source
    from app.modules.aftersales.targets import publish_targets
    from tests.test_aftersales_ingestion import sample
    from tests.test_aftersales_targets import config

    receive = sample(date(2026, 10, 27))
    for code in FACTS:
        publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)
    publish_reports(sales_pg, tmp_path)
    publish_targets(sales_pg, config(), tmp_path)
    with sales_pg.begin() as c:
        assert pending_processing(c) == ["aftersales_report"]
    with client_for(sales_pg) as client:
        report = client.get("/api/v1/data-status").json()["data"]["processing"][1]
        assert report["state"] == "pending"
    publish_reports(sales_pg, tmp_path)
    with sales_pg.begin() as c:
        assert pending_processing(c) == []
    revised = config()
    revised["total_refund_rate"]["upper"] = "0.44"
    publish_targets(sales_pg, revised, tmp_path)
    with sales_pg.begin() as c:
        assert pending_processing(c) == ["aftersales_report"]


def test_source_date_requires_current_version_snapshot(sales_pg, tmp_path):
    from app.jobs.source_monitor import run_monitor
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_scoring_publication import inputs

    store = ProductBoardStore(sales_pg, tmp_path)
    store.run(lambda hb: inputs())
    with client_for(sales_pg) as client:
        rows = client.get("/api/v1/data-status").json()["data"]["sources"]
        assert next(r for r in rows if r["id"] == "product_daily")["datasets"][0]["date"] is None
    run_monitor(
        sales_pg,
        {"product_daily": lambda: {"date": "2026-10-26"}},
        lambda groups, c: dict.fromkeys(groups, True),
    )
    with client_for(sales_pg) as client:
        rows = client.get("/api/v1/data-status").json()["data"]["sources"]
        assert (
            next(r for r in rows if r["id"] == "product_daily")["datasets"][0]["date"]
            == "2026-10-26"
        )
    end, rows = inputs()
    rows["product_daily"][0]["product_revenue"] += 1
    store.run(lambda hb: (end, rows))
    with client_for(sales_pg) as client:
        rows = client.get("/api/v1/data-status").json()["data"]["sources"]
        assert next(r for r in rows if r["id"] == "product_daily")["datasets"][0]["date"] is None
