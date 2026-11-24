"""固定时点策略与真实PG状态机；上游边界注入，事务和锁使用真实PG。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]


def at(value):
    return datetime.fromisoformat("2026-11-04T" + value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize("fixed", [True, False])
def test_interrupted_check_keeps_success_and_retries_five_minutes_after_recovery(sales_pg, fixed):
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources

    runner = run_scheduled_sources if fixed else run_monitor
    calls = []
    received = []

    def probe():
        calls.append(True)
        return {"revision": "same"}

    def sync(ids, connection):
        received.extend(ids)
        return dict.fromkeys(ids, True)

    runner(sales_pg, {"shop_daily": probe}, sync, now=at("08:30"))
    with sales_pg.begin() as c:
        old = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        c.execute(
            sa.text("UPDATE source_checkpoints SET check_status='checking', checked_at=:at"),
            {"at": at("08:31")},
        )
    runner(sales_pg, {"shop_daily": probe}, sync, now=at("08:50"))
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["check_status"] == "failed"
        assert row["observed_at"] == old["observed_at"]
        assert row["accepted_fingerprint"] == old["accepted_fingerprint"]
    runner(sales_pg, {"shop_daily": probe}, sync, now=at("08:54:59"))
    assert len(calls) == 2  # 初次接收前后核对；恢复/冷却都不能请求上游。
    runner(sales_pg, {"shop_daily": probe}, sync, now=at("08:55"))
    runner(sales_pg, {"shop_daily": probe}, sync, now=at("08:56"))
    assert len(calls) == 3 and received == ["shop_daily"]


def test_http_replaces_page_without_reingesting_unchanged_source(sales_pg):
    import hashlib
    import json

    from app.jobs.source_schedule import run_scheduled_sources

    page = dict(basis="warehouse_web_page", table_id="cAc3IVT4i3", updated_at="2026-11-04 08:00:00")
    old_digest = hashlib.sha256(json.dumps(page, sort_keys=True).encode()).hexdigest()
    calls = []

    def sync(ids, c):
        calls.extend(ids)
        return dict.fromkeys(ids, True)

    run_scheduled_sources(sales_pg, {"shop_daily": lambda: page}, sync, now=at("08:30"))
    with sales_pg.connect() as c:
        assert (
            c.scalar(sa.text("SELECT accepted_fingerprint FROM source_checkpoints")) == old_digest
        )
    metadata = dict(page, basis="warehouse_web_metadata")
    run_scheduled_sources(sales_pg, {"shop_daily": lambda: metadata}, sync, now=at("09:40"))
    assert calls == ["shop_daily"]
    metadata["updated_at"] = "2026-11-04 10:01:00"
    run_scheduled_sources(sales_pg, {"shop_daily": lambda: metadata}, sync, now=at("10:10"))
    assert calls == ["shop_daily", "shop_daily"]


def test_default_runtime_http_auth_failure_retries_in_five_minutes_without_browser(
    sales_pg, tmp_path, monkeypatch
):
    import builtins
    import json
    import time
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError
    from urllib.request import HTTPSHandler, build_opener
    from urllib.response import addinfourl

    from app.connectors import warehouse_metadata
    from app.jobs import source_monitor_runtime as runtime
    from tests.test_project_sync_runtime import paths_for

    paths = paths_for(tmp_path)
    state = paths.feishu_root / "var/local-db/warehouse-browser/state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "YCSESSIONID",
                        "value": "fixture-session",
                        "domain": "bi.kktree.cn",
                        "path": "/",
                        "expires": time.time() + 3600,
                        "secure": False,
                    }
                ]
            }
        )
    )
    state.chmod(0o600)
    calls, received = [], []
    expired = [False]

    class Protocol(HTTPSHandler):
        def https_open(self, request):
            assert request.full_url == warehouse_metadata.ENDPOINT + "?tableId=cAc3IVT4i3"
            assert request.get_header("Cookie") == "YCSESSIONID=fixture-session"
            calls.append(request.full_url)
            if expired[0]:
                raise HTTPError(request.full_url, 401, "private detail", {}, None)
            response = addinfourl(
                BytesIO(
                    json.dumps(
                        {
                            "success": True,
                            "code": 200,
                            "data": {
                                "id": "cAc3IVT4i3",
                                "tableName": "ods_rpa_pdd_traffic_kanban_overview_f",
                                "dataLastUpdateTime": "2026-11-04 08:00:00",
                            },
                        }
                    ).encode()
                ),
                Message(),
                request.full_url,
                200,
            )
            response.msg = "OK"
            return response

    original_import = builtins.__import__

    def no_browser(name, *args, **kwargs):
        assert not name.startswith("playwright") and name != "app.connectors.warehouse_page"
        return original_import(name, *args, **kwargs)

    def receive(*args, **kwargs):
        received.extend(kwargs["only"])
        return {"datasets": {code: {"status": "succeeded"} for code in kwargs["only"]}}

    monkeypatch.setattr(builtins, "__import__", no_browser)
    monkeypatch.setattr(warehouse_metadata, "build_opener", lambda *h: build_opener(*h, Protocol()))
    monkeypatch.setattr(runtime.SourceProbes, "check_upstream_day", lambda self, code, day: None)
    monkeypatch.setattr(runtime, "execute_project", receive)

    def run(t):
        return runtime.execute_source_schedule(
            sales_pg,
            paths=paths,
            warehouse_password=lambda: pytest.fail("不应读取数仓SQL密码"),
            source_ids=("shop_daily",),
            now=at(t),
        )

    assert run("08:30")["checked"] == 1
    expired[0] = True
    run("09:40")
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
    assert row["error_code"] == "WAREHOUSE_WEB_LOGIN_REQUIRED"
    assert row["accepted_at"] == at("08:30")
    assert row["web_checked_at"] == at("08:30")
    before = len(calls)
    assert run("09:44:59")["checked"] == 0
    assert len(calls) == before
    expired[0] = False
    assert run("09:45")["checked"] == 1
    assert run("09:50")["checked"] == 0
    assert received == ["shop_daily"]
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
    assert row["check_status"] == "succeeded" and row["error_code"] is None
    assert row["web_checked_at"] == at("09:45")


def test_source_policy_checks_every_slot_and_preserves_legacy_scope():
    from app.modules.data_status.schedule_policy import LEGACY_SOURCES, LEGACY_STEPS, due_sources

    assert due_sources(at("08:19")) == {}
    assert set(due_sources(at("08:20"))) == {"shop_refunds", "sales_targets"}
    assert "shop_daily" not in due_sources(at("08:29"))
    assert due_sources(at("09:39"))["shop_daily"] == at("08:30")
    assert due_sources(at("09:40"))["shop_daily"] == at("09:40")
    first = due_sources(at("08:30"))
    assert len(first) == 10
    assert len(due_sources(at("09:40"))) == 10  # 飞书最近到期槽仍08:20，仓储负责去重
    assert first["shop_refunds"] == due_sources(at("09:40"))["shop_refunds"]
    assert due_sources(at("10:10"))["shop_refunds"] != first["shop_refunds"]
    assert due_sources(at("16:10"))["shop_daily"].strftime("%H:%M") == "16:10"
    assert set(LEGACY_SOURCES) == {
        "product_regions",
        "jst_returns",
        "jst_sales",
        "jst_master",
        "weather_bulletin",
        "weather_mid_range",
    }
    assert "products" not in LEGACY_STEPS and "shop_refunds" not in LEGACY_STEPS
    assert {"product_regions", "jst_returns", "weather"} <= set(LEGACY_STEPS)


def test_success_does_not_stop_later_checks_and_same_slot_deduplicates(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    revision = ["v1"]
    calls = []

    def sync(sources, c):
        calls.extend(sources)
        return dict.fromkeys(sources, True)

    def run(t):
        return run_scheduled_sources(
            sales_pg, {"shop_daily": lambda: {"revision": revision[0]}}, sync, now=at(t)
        )

    assert run("08:30")["checked"] == 1
    assert run("08:31")["checked"] == 0
    assert run("09:40")["checked"] == 1
    assert calls == ["shop_daily"]
    revision[0] = "v2"
    run("10:10")
    assert calls == ["shop_daily", "shop_daily"]
    run("16:10")  # 普通检查不重读未变化来源
    run("16:11")
    assert calls == ["shop_daily"] * 2


def test_failed_reception_retries_after_each_finish_until_success(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    outcomes = iter((False, False, False, True))
    calls = []
    now = at("08:30")

    def sync(sources, c):
        nonlocal now
        if not sources:
            return {}
        calls.append((now, sources))
        now += timedelta(minutes=2)
        return {**dict.fromkeys(sources, next(outcomes)), "_processing_failed": True}

    probes = {"shop_daily": lambda: {"revision": "same"}}

    def clock():
        return now

    for stamp in ("08:30", "08:36:59", "08:37", "08:43:59", "08:44", "08:51", "08:58", "09:40"):
        now = at(stamp)
        run_scheduled_sources(sales_pg, probes, sync, clock=clock)
    assert calls == [(at(t), ("shop_daily",)) for t in ("08:30", "08:37", "08:44", "08:51")]
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["accepted_fingerprint"] == p["observed_fingerprint"]
        assert p["sync_status"] == "succeeded"
        assert p["accepted_at"] == at("08:53")
        assert p["scheduled_attempts"] == 1


def test_retry_waits_across_fixed_slot_and_status_shows_retry_time(sales_pg):
    from app.jobs.project_sync import Schedule
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.repository import read_status

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    finish = (now - timedelta(minutes=2)).replace(second=0, microsecond=0)
    start = finish - timedelta(minutes=2)
    regular_slots = (start.strftime("%H:%M"), finish.strftime("%H:%M"))
    probe = {"shop_daily": lambda: {"revision": "new"}}
    run_scheduled_sources(
        sales_pg,
        probe,
        lambda ids, c: dict.fromkeys(ids, False),
        now=start,
        warehouse_times=regular_slots,
    )
    with sales_pg.begin() as c:
        c.execute(sa.text("UPDATE source_checkpoints SET sync_finished_at=:at"), {"at": finish})
    skipped = run_scheduled_sources(
        sales_pg,
        probe,
        lambda ids, c: dict.fromkeys(ids, True),
        now=finish + timedelta(minutes=4),
        warehouse_times=regular_slots,
    )
    assert skipped["checked"] == 0
    status = read_status(
        sales_pg,
        source_schedule=Schedule(
            source_schedule_enabled=True,
            warehouse_times=regular_slots,
        ),
    )["data"]
    source = next(row for row in status["sources"] if row["id"] == "shop_daily")
    assert source["upstream"]["next_check_at"] == finish + timedelta(minutes=5)
    resumed = run_scheduled_sources(
        sales_pg,
        probe,
        lambda ids, c: dict.fromkeys(ids, True),
        now=finish + timedelta(minutes=5),
        warehouse_times=regular_slots,
    )
    assert resumed["sources"] == ["shop_daily"]
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT scheduled_slot_at FROM source_checkpoints")) == finish


def test_metadata_failure_preserves_observation_and_next_slot_retries(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    def fail():
        raise ValueError("secret must never persist")

    run_scheduled_sources(sales_pg, {"shop_daily": fail}, lambda ids, c: {}, now=at("08:30"))
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["check_status"] == "failed" and p["accepted_at"] is None
        assert p["error_code"] == "CHECK_FAILED"


@pytest.mark.parametrize("start", ("08:30", "23:58"))
def test_failed_checks_retry_from_finish_across_midnight_until_success(sales_pg, start):
    from app.jobs.project_sync import Schedule
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.repository import read_status

    current = at(start)
    probes_seen, downloads = [], []
    failures = 3

    def probe():
        nonlocal current, failures
        probes_seen.append(current)
        if failures:
            failures -= 1
            current += timedelta(minutes=2)
            raise ValueError("private metadata failure")
        return {"revision": "stable"}

    def sync(ids, c):
        downloads.extend(ids)
        return dict.fromkeys(ids, True)

    def run():
        return run_scheduled_sources(
            sales_pg,
            {"shop_daily": probe},
            sync,
            clock=lambda: current,
            warehouse_times=(start,),
        )

    for _attempt in range(3):
        assert run()["failed"] == 1
        finished = current
        data = read_status(
            sales_pg,
            source_schedule=Schedule(source_schedule_enabled=True, warehouse_times=(start,)),
        )["data"]
        source = next(row for row in data["sources"] if row["id"] == "shop_daily")
        assert source["upstream"]["next_check_at"] == finished + timedelta(minutes=5)
        current = finished + timedelta(seconds=299)
        assert run()["checked"] == 0
        current += timedelta(seconds=1)
    assert run()["status"] == "checked"
    current += timedelta(minutes=5)
    assert run()["checked"] == 0
    assert probes_seen == [at(start) + timedelta(minutes=offset) for offset in (0, 7, 14, 21, 21)]
    assert downloads == ["shop_daily"]
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["check_status"] == "succeeded"
        assert p["error_code"] is None
        assert p["scheduled_attempts"] == 1


def test_weekly_success_refreshes_failed_legacy_check_without_ordinary_download(sales_pg):
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    revision = "accepted"

    def probe():
        if revision is None:
            raise ValueError("private metadata failure")
        return {"revision": revision}

    probes = {"product_regions": probe}
    run_monitor(
        sales_pg, probes, lambda ids, c: dict.fromkeys(ids, True), now=sunday - timedelta(hours=1)
    )
    revision = None
    run_monitor(sales_pg, probes, lambda ids, c: {}, now=sunday - timedelta(minutes=30))
    revision = "accepted"
    before = run_scheduled_sources(
        sales_pg, probes, lambda ids, c: {}, now=sunday - timedelta(minutes=20)
    )
    assert before["checked"] == 0  # 旧来源的五分钟检查重试仍由 monitor 负责。
    result = run_scheduled_sources(
        sales_pg, probes, lambda ids, c: dict.fromkeys(ids, True), now=sunday
    )
    assert result["sources"] == ["product_regions"]
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["check_status"] == "succeeded"
        assert p["checked_at"] == sunday
        assert p["observed_at"] == sunday
        assert p["accepted_at"] == sunday
        assert p["error_code"] is None
        assert p["sync_status"] is None
        assert p["scheduled_slot_at"] is None


def test_weekly_success_keeps_unresolved_ordinary_failure_but_refreshes_check(sales_pg):
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    probes = {"product_regions": lambda: {"revision": "old"}}
    old = sunday - timedelta(hours=1)
    run_monitor(sales_pg, probes, lambda ids, c: dict.fromkeys(ids, True), now=old)
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""
            UPDATE source_checkpoints SET sync_status='failed',sync_started_at=:old,
              sync_finished_at=:old,error_code='SYNC_FAILED'
        """),
            {"old": old},
        )
    result = run_scheduled_sources(
        sales_pg,
        {"product_regions": lambda: {"revision": "new"}},
        lambda ids, c: dict.fromkeys(ids, True),
        now=sunday,
    )
    assert result["sources"] == ["product_regions"]
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["checked_at"] == sunday
        assert p["check_status"] == "succeeded"
        assert p["sync_status"] == "failed"
        assert p["sync_finished_at"] == old
        assert p["error_code"] == "SYNC_FAILED"


def test_failed_check_cooldown_cannot_be_bypassed_by_new_fixed_slot(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    def unavailable():
        raise ValueError("private metadata failure")

    options = dict(warehouse_times=("08:30", "08:32"))
    run_scheduled_sources(
        sales_pg, {"shop_daily": unavailable}, lambda ids, c: {}, now=at("08:30"), **options
    )
    assert (
        run_scheduled_sources(
            sales_pg, {"shop_daily": unavailable}, lambda ids, c: {}, now=at("08:34:59"), **options
        )["checked"]
        == 0
    )
    result = run_scheduled_sources(
        sales_pg,
        {"shop_daily": lambda: {"revision": "stable"}},
        lambda ids, c: dict.fromkeys(ids, True),
        now=at("08:35"),
        **options,
    )
    assert result["sources"] == ["shop_daily"]
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT scheduled_slot_at FROM source_checkpoints")) == at("08:32")


def test_weekly_deferred_cooldown_blocks_overlapping_ordinary_slot(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    probes = {"shop_daily": lambda: {"revision": "stable"}}
    options = dict(warehouse_times=("16:10", "17:02"))
    run_scheduled_sources(
        sales_pg,
        probes,
        lambda ids, c: dict.fromkeys(ids, True),
        now=sunday - timedelta(minutes=50),
        **options,
    )
    run_scheduled_sources(
        sales_pg,
        probes,
        lambda ids, c: {"_deferred": dict.fromkeys(ids, "MAINTENANCE_DEFERRED")},
        now=sunday,
        **options,
    )
    assert (
        run_scheduled_sources(
            sales_pg, probes, lambda ids, c: {}, now=sunday + timedelta(minutes=2), **options
        )["checked"]
        == 0
    )


def test_weekly_failed_probes_retry_after_finish_and_success_stops_cycle(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    current = sunday - timedelta(minutes=50)
    unavailable = False
    downloads = []

    def probe():
        nonlocal current
        if unavailable:
            current += timedelta(minutes=1)
            raise ValueError("private metadata failure")
        return {"revision": "stable"}

    def sync(ids, c):
        downloads.extend(ids)
        return dict.fromkeys(ids, True)

    def run():
        return run_scheduled_sources(sales_pg, {"shop_daily": probe}, sync, clock=lambda: current)

    run()
    downloads.clear()
    current, unavailable = sunday, True
    for _attempt in range(3):
        assert run()["status"] == "partial"
        with sales_pg.connect() as c:
            cycle = c.execute(sa.text("SELECT * FROM source_reconciliations")).mappings().one()
            assert cycle["status"] == "failed"
            assert cycle["last_finished_at"] == current
            assert cycle["error_code"] == "CHECK_FAILED"
            assert cycle["cycle_at"] == sunday
        current += timedelta(seconds=299)
        assert run()["checked"] == 0
        current += timedelta(seconds=1)
    unavailable = False
    assert run()["status"] == "checked"
    current += timedelta(minutes=5)
    assert run()["checked"] == 0
    assert downloads == ["shop_daily"] * 4


def test_legacy_monitor_yields_pending_weekly_source_and_resumes_after_success(sales_pg):
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    probes = {"product_regions": lambda: {"revision": "new"}}
    calls = []
    successful = False

    def sync(ids, c):
        calls.extend(ids)
        return dict.fromkeys(ids, successful)

    def monitor(stamp):
        return run_monitor(
            sales_pg, probes, sync, now=stamp, reconciliation_sources=("product_regions",)
        )

    assert monitor(sunday)["checked"] == 0
    for minutes in (0, 5, 10):
        stamp = sunday + timedelta(minutes=minutes)
        assert monitor(stamp)["checked"] == 0
        run_scheduled_sources(sales_pg, probes, sync, now=stamp)
        assert monitor(stamp + timedelta(minutes=1))["checked"] == 0
    assert calls == ["product_regions"] * 3
    successful = True
    run_scheduled_sources(sales_pg, probes, sync, now=sunday + timedelta(minutes=15))
    assert monitor(sunday + timedelta(minutes=16))["checked"] == 0
    assert monitor(sunday + timedelta(minutes=30))["checked"] == 1
    assert calls == ["product_regions"] * 4


def test_weekly_success_closes_matching_legacy_sync_failure_without_repeat(sales_pg):
    from app.jobs.project_sync import Schedule
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.repository import read_status
    from tests.test_published_versions import publish_pointer

    sunday = at("17:00") + timedelta(days=4)
    probes = {"product_regions": lambda: {"revision": "unchanged"}}
    publish_pointer(sales_pg, "product_regions", "available")
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""
            INSERT INTO task_runs
              (id,job_code,request_ref,lock_key,status,queued_at,started_at,finished_at,error_code)
            VALUES ('ordinary-failed','sync_product_regions','test','product_regions',
                    'failed',:stamp,:stamp,:stamp,'INGESTION_FAILED')
        """),
            {"stamp": sunday - timedelta(hours=2)},
        )
    run_monitor(
        sales_pg, probes, lambda ids, c: dict.fromkeys(ids, False), now=sunday - timedelta(hours=1)
    )
    run_scheduled_sources(sales_pg, probes, lambda ids, c: dict.fromkeys(ids, True), now=sunday)
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["error_code"] is None
        assert p["accepted_at"] == sunday
    result = run_monitor(
        sales_pg,
        probes,
        lambda ids, c: {},
        now=sunday + timedelta(minutes=5),
        reconciliation_sources=("product_regions",),
    )
    assert result["checked"] == 0
    row = next(
        row
        for row in read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))[
            "data"
        ]["sources"]
        if row["id"] == "product_regions"
    )
    assert row["platform_state"] == "available"
    assert row["datasets"][0]["attempt_status"] == "succeeded"
    assert row["datasets"][0]["last_finished_at"] == sunday


def test_ordinary_project_yields_weekly_source_under_project_lock(sales_pg, tmp_path):
    from app.jobs.project_sync import run_project
    from tests.test_published_versions import publish_pointer

    publish_pointer(sales_pg, "shop_daily", "available")
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""
            INSERT INTO source_reconciliations
              (source_id,cycle_at,status,last_attempt_at,last_finished_at,error_code)
            VALUES ('product_regions',now()-interval '1 hour','failed',
                    now()-interval '1 hour',now(),'SYNC_FAILED')
        """)
        )
        version = c.scalar(
            sa.text("SELECT version_id FROM current_datasets WHERE dataset_code='shop_daily'")
        )

    def forbidden():
        raise AssertionError("普通固定任务不应重复接收待周对账来源")

    result = run_project(
        sales_pg,
        "ordinary-overlap",
        {"product_regions": forbidden, "shop_daily": lambda: {"version_id": version}},
        tmp_path,
        reconciliation_sources=("product_regions",),
    )
    assert result["status"] == "succeeded"
    assert set(result["datasets"]) == {"shop_daily"}


@pytest.mark.parametrize(
    "category",
    (
        "ARCHIVE_CAPACITY",
        "INCREMENTAL_BASELINE_MISMATCH",
        "WAREHOUSE_CONCURRENCY_LIMIT",
        "SYNC_FAILED",
    ),
)
def test_runtime_keeps_safe_failure_categories_in_source_and_weekly_state(
    sales_pg, tmp_path, monkeypatch, capsys, category
):
    from pymysql.err import ProgrammingError

    from app.connectors.warehouse_metadata import WarehouseMetadataReader
    from app.jobs import project_runtime
    from app.jobs.project_sync import Schedule
    from app.jobs.source_monitor_runtime import execute_source_schedule
    from app.modules.data_status.repository import read_status
    from tests.test_project_sync_runtime import paths_for

    def fail():
        if category == "WAREHOUSE_CONCURRENCY_LIMIT":
            raise ProgrammingError(1064, "Exceed concurrency limit: private upstream query")
        raise ValueError("private upstream query" if category == "SYNC_FAILED" else category)

    monkeypatch.setattr(
        project_runtime, "build_steps", lambda *args, **kwargs: {"shop_daily": fail}
    )
    monkeypatch.setattr(WarehouseMetadataReader, "read", lambda self, code: {"revision": "stable"})
    sunday = at("17:00") + timedelta(days=4)
    result = execute_source_schedule(
        sales_pg,
        paths=paths_for(tmp_path),
        warehouse_password=lambda: "unused",
        warehouse_headers=lambda: {},
        now=sunday,
        source_ids=("shop_daily",),
    )
    assert result["failed"] == 1
    source = next(
        row
        for row in read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))[
            "data"
        ]["sources"]
        if row["id"] == "shop_daily"
    )
    assert source["upstream"]["error_code"] == category
    assert source["datasets"][0]["error_code"] == category
    assert source["reconciliation"]["error_code"] == "SYNC_FAILED"  # 沿用现行周状态DB约束。
    assert source["platform_state"] == "failed"
    logs = capsys.readouterr().out
    assert f'"failure_category": "{category}"' in logs
    assert "private upstream query" not in logs


def test_policy_config_is_validated_and_survives_legacy_time_edits(tmp_path):
    from app.jobs.project_sync import read_schedule

    path = tmp_path / "sync.toml"
    path.write_text(
        'times=["10:00"]\nsource_schedule_enabled=true\nwarehouse_times=["08:30","16:10"]\nfeishu_times=["08:20","10:10"]\n'
    )
    config = read_schedule(path)
    assert config.source_schedule_enabled is True
    assert config.warehouse_times == ("08:30", "16:10")


def test_selected_product_reader_only_projects_requested_table():
    from app.connectors.product_board import ProductBoardReader

    reader = object.__new__(ProductBoardReader)
    reader.latest = lambda selected=None: at("08:30").date()
    calls = []
    reader.projection = lambda code, end: calls.append(code) or []
    reader.read_stable(("product_promotion",))
    assert calls == ["product_promotion", "product_promotion"]


def test_check_change_during_receipt_is_not_accepted_and_other_source_succeeds(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    value = ["one"]

    def sync(ids, c):
        value[0] = "two"
        return dict.fromkeys(ids, True)

    result = run_scheduled_sources(
        sales_pg,
        {
            "shop_daily": lambda: {"revision": value[0]},
            "shop_ratings": lambda: {"revision": "stable"},
        },
        sync,
        now=at("08:30"),
    )
    assert result["pending"] == 1
    with sales_pg.connect() as c:
        rows = {
            r["source_id"]: r
            for r in c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings()
        }
        assert rows["shop_daily"]["accepted_fingerprint"] is None
        assert rows["shop_ratings"]["accepted_fingerprint"] is not None


def test_project_lock_prevents_scheduled_probe(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    with sales_pg.connect() as c:
        c.execute(sa.text("SELECT pg_advisory_lock(hashtext('project-source-sync'))"))
        c.commit()
        try:
            result = run_scheduled_sources(
                sales_pg, {"shop_daily": lambda: 1 / 0}, lambda ids, c: 1 / 0, now=at("08:30")
            )
            assert result["status"] == "already_running"
        finally:
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
            c.commit()


def test_fixed_schedule_status_has_next_slot_and_source_specific_sync(sales_pg):
    from app.jobs.project_sync import Schedule
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.repository import read_status

    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=8, minute=30, second=0, microsecond=0
    ) - timedelta(days=1)
    run_scheduled_sources(
        sales_pg,
        {
            "shop_daily": lambda: {
                "basis": "warehouse_web_metadata",
                "updated_at": "2026-11-03 10:02:10",
            }
        },
        lambda ids, c: dict.fromkeys(ids, True),
        now=stamp,
    )
    data = read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))["data"]
    row = next(r for r in data["sources"] if r["id"] == "shop_daily")
    assert row["upstream"]["next_check_at"] is not None
    assert row["datasets"][0]["last_attempt_at"] == stamp
    assert data["monitor"]["source_schedule_enabled"] is True


def test_interrupted_receipt_recovers_after_five_minutes_without_using_fixed_slot(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    calls = []
    probe = {"shop_daily": lambda: {"revision": "stable"}}

    def sync(ids, c):
        calls.append(ids)
        return dict.fromkeys(ids, True)

    run_scheduled_sources(sales_pg, probe, sync, now=at("08:30"))
    with sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE source_checkpoints SET sync_status='running',sync_finished_at=NULL")
        )
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("08:35"))["checked"] == 0
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("08:39:59"))["checked"] == 0
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("08:40"))["checked"] == 1
    with sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE source_checkpoints SET sync_status='running',sync_finished_at=NULL")
        )
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("09:11"))["checked"] == 0
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("09:16"))["checked"] == 1
    assert calls == [("shop_daily",)] * 3
    assert (
        run_scheduled_sources(sales_pg, probe, sync, now=at("08:30") + timedelta(days=1))["checked"]
        == 1
    )


def test_schedule_database_rejects_incomplete_sync_times(sales_pg):
    import pytest

    from app.jobs.source_schedule import run_scheduled_sources

    run_scheduled_sources(
        sales_pg,
        {"shop_daily": lambda: {"revision": "one"}},
        lambda ids, c: dict.fromkeys(ids, True),
        now=at("08:30"),
    )
    with pytest.raises(sa.exc.IntegrityError), sales_pg.begin() as c:
        c.execute(sa.text("UPDATE source_checkpoints SET sync_status=NULL"))


def test_new_schedule_does_not_retry_legacy_aftersales_processing(sales_pg, tmp_path, monkeypatch):
    from app.jobs import source_monitor_runtime as runtime
    from tests.test_project_sync_runtime import paths_for

    monkeypatch.setattr(runtime, "pending_processing", lambda c: ["aftersales_report"])
    monkeypatch.setattr(runtime.SourceProbes, "read", lambda self, code: {"revision": "one"})
    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    monkeypatch.setattr(WarehouseMetadataReader, "read", lambda self, code: {"revision": "one"})
    selected = []

    def execute(*args, **kwargs):
        selected.extend(kwargs["only"])
        return {"datasets": {step: {"status": "succeeded"} for step in kwargs["only"]}}

    monkeypatch.setattr(runtime, "execute_project", execute)
    runtime.execute_source_schedule(
        sales_pg,
        paths=paths_for(tmp_path),
        warehouse_password=lambda: "unused",
        warehouse_headers=lambda: {},
        now=at("08:30"),
    )
    assert "aftersales_report" not in selected


def test_manual_reception_uses_same_watermark_without_consuming_scheduled_slot(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    calls = []
    probes = {"shop_daily": lambda: {"revision": "v2"}}

    def fail(ids, c):
        return dict.fromkeys(ids, False)

    def succeed(ids, c):
        calls.extend(ids)
        return dict.fromkeys(ids, True)

    run_scheduled_sources(sales_pg, probes, fail, now=at("10:10"))
    run_scheduled_sources(sales_pg, probes, succeed, now=at("10:15"), force=True)
    run_scheduled_sources(sales_pg, probes, succeed, now=at("10:50"))
    assert calls == ["shop_daily"]
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["sync_finished_at"] == at("10:15")
        assert row["sync_status"] == "succeeded"
        assert row["accepted_fingerprint"] == row["observed_fingerprint"]


def test_profile_receipt_does_not_query_unselected_business_sources():
    from app.connectors.product_board import ProductBoardReader

    reader = object.__new__(ProductBoardReader)

    class Connection:
        def cursor(self):
            raise AssertionError("资料更新不应查询经营表")

    reader.connection = Connection()
    reader.heartbeat = lambda: None
    calls = []
    reader.projection = lambda code, end: calls.append(code) or []
    reader.read_stable(("profile_records",))
    assert calls == ["profile_records", "profile_records"]


def test_changed_product_sources_fail_independently_using_published_inputs(
    sales_pg, tmp_path, monkeypatch
):
    from app.connectors import product_board
    from app.connectors.warehouse_metadata import WarehouseMetadataReader
    from app.jobs.source_monitor_runtime import execute_source_schedule
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate
    from tests.test_project_sync_runtime import paths_for

    seed = ProductBoardStore(sales_pg, tmp_path / "seed").run(lambda hb: candidate())
    end, rows = candidate()
    rows["profile_records"][0]["source_product_name_raw"] = "更新后资料"
    calls = []

    class Reader:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

        def latest(self, selected=None):
            return end

        def read_stable(self, selected=None, *, periods=None, end=None):
            calls.append(tuple(selected))
            if "product_daily" in selected:
                raise ValueError("SOURCE_TEMPORARILY_UNAVAILABLE")
            return end, {k: rows[k] for k in selected}

    monkeypatch.setattr(product_board, "ProductBoardReader", Reader)
    monkeypatch.setattr(WarehouseMetadataReader, "read", lambda self, code: {"revision": "two"})
    result = execute_source_schedule(
        sales_pg,
        paths=paths_for(tmp_path),
        warehouse_password=lambda: "unused",
        warehouse_headers=lambda: {},
        now=at("10:10"),
        source_ids=("product_daily", "profile_records"),
        force=True,
        process_products=False,
    )
    assert result["failed"] == 1
    assert calls == [("product_daily",), ("profile_records",)]
    with sales_pg.connect() as c:
        heads = dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        )
        states = dict(
            c.execute(sa.text("SELECT source_id,sync_status FROM source_checkpoints")).all()
        )
    assert heads["product_daily"] == seed["versions"]["product_daily"]
    assert heads["profile_records"] != seed["versions"]["profile_records"]
    assert states == {"product_daily": "failed", "profile_records": "succeeded"}


def test_page_reader_updates_web_evidence_and_login_failure_keeps_success(sales_pg):
    from app.connectors.warehouse_page import WarehousePageError
    from app.jobs.source_schedule import run_scheduled_sources

    observation = dict(
        basis="warehouse_web_page", table_id="cAc3IVT4i3", updated_at="2026-11-03 10:02:10"
    )
    run_scheduled_sources(
        sales_pg,
        {"shop_daily": lambda: observation},
        lambda ids, c: dict.fromkeys(ids, True),
        now=at("08:30"),
    )

    def login_expired():
        raise WarehousePageError("WAREHOUSE_WEB_LOGIN_REQUIRED")

    run_scheduled_sources(
        sales_pg, {"shop_daily": login_expired}, lambda ids, c: {}, now=at("09:40")
    )
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
    assert row["web_updated_at"] == observation["updated_at"]
    assert row["web_checked_at"] == at("08:30")
    assert row["accepted_at"] == at("08:30")
    assert row["error_code"] == "WAREHOUSE_WEB_LOGIN_REQUIRED"


def test_injected_page_reader_is_used_without_backend_auth(sales_pg, tmp_path, monkeypatch):
    from app.jobs import source_monitor_runtime as runtime
    from tests.test_project_sync_runtime import paths_for

    class Reader:
        def read(self, code):
            assert code == "shop_daily"
            return dict(
                basis="warehouse_web_page", table_id="cAc3IVT4i3", updated_at="2026-11-03 10:02:10"
            )

    def forbidden():
        raise AssertionError("网页读取不应取后台登录凭据")

    monkeypatch.setattr(runtime, "pending_processing", lambda c: [])
    monkeypatch.setattr(
        runtime,
        "execute_project",
        lambda *a, **kw: {"datasets": {x: {"status": "succeeded"} for x in kw["only"]}},
    )
    result = runtime.execute_source_schedule(
        sales_pg,
        paths=paths_for(tmp_path),
        warehouse_password=forbidden,
        warehouse_reader=Reader(),
        now=at("08:30"),
        source_ids=("shop_daily",),
    )
    assert result["checked"] == 1


def test_weekly_reconciliation_uses_sunday_shanghai_and_retries_only_failures(sales_pg):
    from datetime import UTC

    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.schedule_policy import WAREHOUSE_SOURCES

    sunday = at("17:00") + timedelta(days=4)
    failure = WAREHOUSE_SOURCES[0]
    bad = {failure}
    downloads = []

    def sync(ids, c):
        downloads.extend(ids)
        return {code: code not in bad for code in ids}

    def run(stamp):
        codes = (*WAREHOUSE_SOURCES, "shop_refunds")
        return run_scheduled_sources(
            sales_pg,
            {code: lambda: {"revision": "unchanged"} for code in codes},
            sync,
            now=stamp.astimezone(UTC),
        )

    bad.clear()
    run(sunday - timedelta(minutes=50))
    assert set(downloads) == {*WAREHOUSE_SOURCES, "shop_refunds"}
    downloads.clear()
    run(sunday - timedelta(seconds=1))
    assert not downloads
    bad.add(failure)
    assert run(sunday)["status"] == "partial"
    assert downloads == list(WAREHOUSE_SOURCES)
    downloads.clear()
    run(sunday + timedelta(seconds=299))
    assert not downloads
    for retry in range(1, 5):
        run(sunday + timedelta(minutes=5 * retry))
        assert downloads == [failure] * retry
    bad.clear()
    run(sunday + timedelta(minutes=25))
    run(sunday + timedelta(minutes=30))
    assert downloads == [failure] * 5


def test_weekly_state_survives_restart_missed_sunday_and_cross_week(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    downloads = []
    successful = False

    def sync(ids, c):
        downloads.extend(ids)
        return dict.fromkeys(ids, successful)

    def run(stamp):
        return run_scheduled_sources(
            sales_pg, {"shop_daily": lambda: {"revision": "one"}}, sync, now=stamp
        )

    successful = True
    run(sunday - timedelta(hours=2))
    downloads.clear()
    successful = False
    sales_pg.dispose()
    run(sunday + timedelta(days=1, hours=6))
    sales_pg.dispose()
    successful = True
    run(sunday + timedelta(days=7))
    run(sunday + timedelta(days=7, minutes=5))
    assert downloads == ["shop_daily"] * 2
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_reconciliations")).mappings().one()
        assert row["cycle_at"] == sunday
        assert row["last_success_at"] == sunday + timedelta(days=7)
    run(sunday + timedelta(days=14))
    assert downloads == ["shop_daily"] * 3


def test_weekly_retry_waits_from_slow_finish_and_recovers_interrupted_worker(sales_pg):
    import pytest

    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    current = sunday - timedelta(minutes=50)
    outcome = "success"
    runs = []

    def sync(ids, c):
        nonlocal current
        runs.extend(ids)
        if outcome == "slow":
            current += timedelta(minutes=12)
            assert run()["status"] == "already_running"
        if outcome == "crash":
            raise KeyboardInterrupt
        return dict.fromkeys(ids, outcome == "success")

    def run():
        return run_scheduled_sources(
            sales_pg, {"shop_daily": lambda: {"revision": "one"}}, sync, clock=lambda: current
        )

    run()
    runs.clear()
    current = sunday
    outcome = "slow"
    run()
    assert runs == ["shop_daily"]
    current += timedelta(seconds=299)
    run()
    assert runs == ["shop_daily"]
    current += timedelta(seconds=1)
    outcome = "crash"
    with pytest.raises(KeyboardInterrupt):
        run()
    current += timedelta(minutes=1)
    outcome = "success"
    run()
    assert runs == ["shop_daily"] * 2
    current += timedelta(minutes=5)
    run()
    assert runs == ["shop_daily"] * 3


def test_weekly_outcomes_do_not_advance_unstable_watermark_or_hide_available_data(
    sales_pg, tmp_path
):
    from app.jobs.project_sync import Schedule
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.repository import read_status
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    seed = ProductBoardStore(sales_pg, tmp_path / "seed").run(lambda hb: candidate())
    sunday = at("17:00") + timedelta(days=4)
    revision = "first"
    outcome = "stable"

    def probe():
        if revision is None:
            raise ValueError("private source information")
        return {"revision": revision}

    def sync(ids, c):
        nonlocal revision
        if ids:
            if outcome == "deferred":
                return {"_deferred": dict.fromkeys(ids, "MAINTENANCE_DEFERRED")}
            if outcome == "changed":
                revision = "second"
            if outcome == "unreadable":
                revision = None
        return dict.fromkeys(ids, True)

    def run(stamp):
        return run_scheduled_sources(sales_pg, {"product_daily": probe}, sync, now=stamp)

    run(sunday - timedelta(minutes=50))
    outcome = "deferred"
    assert run(sunday)["status"] == "deferred"
    outcome = "changed"
    assert run(sunday + timedelta(minutes=5))["status"] == "partial"
    outcome = "unreadable"
    assert run(sunday + timedelta(minutes=10))["status"] == "partial"
    data = read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))["data"]
    row = next(r for r in data["sources"] if r["id"] == "product_daily")
    assert row["platform_state"] == "available"
    assert row["upstream"]["state"] == "unchanged"
    assert row["datasets"][0]["version_id"] == seed["versions"]["product_daily"]
    assert row["reconciliation"]["status"] == "failed"
    assert row["reconciliation"]["error_code"] == "CHECK_FAILED"
    assert row["reconciliation"]["last_success_at"] is None
    assert row["reconciliation"]["next_retry_at"] == sunday + timedelta(minutes=15)
    with sales_pg.connect() as c:
        checkpoint = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert checkpoint["accepted_fingerprint"] == checkpoint["observed_fingerprint"]
        assert checkpoint["accepted_at"] == sunday - timedelta(minutes=50)


def test_weekly_success_closes_matching_pending_change_and_reuses_same_round_download(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = at("17:00") + timedelta(days=4)
    downloads = []
    revision = "one"
    accepted = False

    def sync(ids, c):
        downloads.extend(ids)
        return dict.fromkeys(ids, accepted)

    def run(stamp):
        return run_scheduled_sources(
            sales_pg, {"shop_daily": lambda: {"revision": revision}}, sync, now=stamp
        )

    run(sunday - timedelta(minutes=50))
    accepted = True
    run(sunday)
    assert downloads == ["shop_daily"] * 2
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["sync_status"] == "succeeded"
        assert row["accepted_fingerprint"] == row["observed_fingerprint"]
    revision = "two"
    run(sunday + timedelta(days=7))
    assert downloads == ["shop_daily"] * 3  # 普通检查和对账都需要读取，但共用一次


def test_reconciliation_schema_permissions_and_guarded_downgrade(sales_pg):
    import pytest
    from alembic import command
    from alembic.config import Config

    from migrations_v2.local import ROOT, migrate

    def downgrade(c):
        config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
        config.attributes["connection"] = c
        config.attributes["v2_expected_database"] = c.engine.url.database
        command.downgrade(config, "20261110_14")

    with sales_pg.begin() as c:
        downgrade(c)
        assert c.scalar(sa.text("SELECT to_regclass('public.source_reconciliations')")) is None
        migrate(c)
        before_revision = c.scalar(sa.text("SELECT version_num FROM alembic_version_v2"))
        assert c.scalar(sa.text("SELECT count(*) FROM source_reconciliations")) == 0
        assert c.scalar(
            sa.text("SELECT has_table_privilege('pdd_app','source_reconciliations','SELECT')")
        )
        assert not c.scalar(
            sa.text("SELECT has_table_privilege('pdd_app','source_reconciliations','UPDATE')")
        )
        assert c.scalar(
            sa.text("SELECT has_table_privilege('pdd_ingest','source_reconciliations','UPDATE')")
        )
        assert not c.scalar(
            sa.text("SELECT has_table_privilege('pdd_ingest','source_reconciliations','DELETE')")
        )
    with pytest.raises(sa.exc.IntegrityError), sales_pg.begin() as c:
        c.execute(
            sa.text("""
            INSERT INTO source_reconciliations(source_id,cycle_at,status)
            VALUES ('shop_daily','2026-11-08 17:00:00+08','succeeded')
        """)
        )
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""
            INSERT INTO source_reconciliations(source_id,cycle_at,status)
            VALUES ('shop_daily','2026-11-08 17:00:00+08','waiting')
        """)
        )
    with pytest.raises(sa.exc.DBAPIError, match="保留每周对账证据"), sales_pg.begin() as c:
        downgrade(c)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT status FROM source_reconciliations")) == "waiting"
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == before_revision


def test_first_activation_registers_future_sunday_and_manual_does_not_consume_cycle(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    probes = {"shop_daily": lambda: {"revision": "one"}}
    calls = []

    def sync(ids, c):
        calls.extend(ids)
        return dict.fromkeys(ids, True)

    sunday = at("17:00") + timedelta(days=4)
    run_scheduled_sources(sales_pg, probes, sync, now=at("08:00"), force=True)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM source_reconciliations")) == 0
    run_scheduled_sources(sales_pg, probes, sync, now=at("08:01"))
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT cycle_at,status FROM source_reconciliations")).one() == (
            sunday,
            "waiting",
        )
    run_scheduled_sources(sales_pg, probes, sync, now=sunday, force=True)
    run_scheduled_sources(sales_pg, probes, sync, now=sunday)
    assert calls == ["shop_daily"] * 3


def test_first_weekly_attempt_preserves_legacy_failure_and_available_version(sales_pg):
    from app.jobs.project_sync import Schedule
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.repository import read_status
    from tests.test_published_versions import publish_pointer

    sunday = at("17:00") + timedelta(days=4)
    legacy_at = sunday - timedelta(hours=2)
    probes = {"shop_daily": lambda: {"revision": "unchanged"}}
    publish_pointer(sales_pg, "shop_daily", "available")
    run_monitor(sales_pg, probes, lambda ids, c: dict.fromkeys(ids, True), now=legacy_at)
    run_scheduled_sources(sales_pg, probes, lambda ids, c: {}, now=sunday - timedelta(minutes=50))

    def task(c, task_id, stamp, state, finished, lease, error):
        c.execute(
            sa.text("""
                INSERT INTO task_runs
                  (id,job_code,request_ref,lock_key,status,queued_at,started_at,finished_at,
                   worker_ref,lease_expires_at,error_code)
                VALUES (:id,'sync_shop_daily','test','test',:state,:at,:at,
                        :finished,'test',:lease,:error)
            """),
            dict(id=task_id, state=state, at=stamp, finished=finished, lease=lease, error=error),
        )

    with sales_pg.begin() as c:
        task(c, "legacy", legacy_at, "running", None, legacy_at + timedelta(minutes=5), None)

    def row():
        data = read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))["data"]
        return next(item for item in data["sources"] if item["id"] == "shop_daily")

    def fail(ids, c):
        with c.begin():
            task(c, "weekly", sunday, "failed", sunday, None, "SYNTHETIC_WEEKLY_FAILURE")
        return dict.fromkeys(ids, False)

    assert row()["datasets"][0]["attempt_status"] == "abandoned"
    run_scheduled_sources(sales_pg, probes, fail, now=sunday)
    result = row()
    assert result["platform_state"] == "failed"
    assert result["datasets"][0]["attempt_status"] == "abandoned"
    assert result["datasets"][0]["last_attempt_at"] == legacy_at
    assert result["datasets"][0]["version_id"] == "shop_daily-available"
    assert result["reconciliation"]["status"] == "failed"

    run_scheduled_sources(
        sales_pg,
        probes,
        lambda ids, c: {"_deferred": dict.fromkeys(ids, "MAINTENANCE_DEFERRED")},
        now=sunday + timedelta(minutes=5),
    )
    assert row()["datasets"][0]["last_attempt_at"] == legacy_at
    assert row()["reconciliation"]["status"] == "deferred"

    monday = sunday + timedelta(hours=15, minutes=30)
    run_scheduled_sources(sales_pg, probes, lambda ids, c: dict.fromkeys(ids, True), now=monday)
    result = row()
    assert result["platform_state"] == "available"
    assert result["datasets"][0]["attempt_status"] == "succeeded"
    assert result["datasets"][0]["last_attempt_at"] == monday
    assert result["reconciliation"]["status"] == "succeeded"


def test_unconfirmed_ordinary_receipt_retries_when_marker_reverts_and_probe_fails(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    revision = "accepted"
    downloads = []

    def probe():
        if revision is None:
            raise ValueError("synthetic unavailable")
        return {"revision": revision}

    def sync(ids, c):
        nonlocal revision
        downloads.extend(ids)
        if ids and revision == "new":
            revision = "accepted"
        return dict.fromkeys(ids, True)

    def run(stamp):
        return run_scheduled_sources(sales_pg, {"shop_daily": probe}, sync, now=at(stamp))

    run("08:30")
    revision = "new"
    assert run("09:40")["pending"] == 1
    revision = None
    run("10:10")
    revision = "accepted"
    run("10:50")
    assert downloads == ["shop_daily"] * 3
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT accepted_at FROM source_checkpoints")) == at("10:50")
