"""固定时点策略与真实PG状态机；上游边界注入，事务和锁使用真实PG。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]


def at(value):
    return datetime.fromisoformat("2026-11-04T" + value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))


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

        def read_stable(self, selected=None):
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
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == "20261111_16"


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
