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
    first = due_sources(at("08:20"))
    assert len(first) == 10
    assert len(due_sources(at("09:10"))) == 10  # 飞书最近到期槽仍08:20，仓储负责去重
    assert first["shop_refunds"] == due_sources(at("09:10"))["shop_refunds"]
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

    assert run("08:20")["checked"] == 1
    assert run("08:21")["checked"] == 0
    assert run("09:10")["checked"] == 1
    assert calls == ["shop_daily"]
    revision[0] = "v2"
    run("10:10")
    assert calls == ["shop_daily", "shop_daily"]
    run("16:10")  # 弱信号来源每日一次获准范围对账，同轮只接收一次
    run("16:11")
    assert calls == ["shop_daily"] * 3


def test_failed_reception_retries_next_slot_and_processing_failure_does_not_redownload(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    outcomes = [False, True, True]
    calls = []

    def sync(sources, c):
        calls.append(sources)
        return {**dict.fromkeys(sources, outcomes.pop(0)), "_processing_failed": True}

    probes = {"shop_daily": lambda: {"revision": "same"}}
    for stamp in ("08:20", "09:10", "10:10"):
        run_scheduled_sources(sales_pg, probes, sync, now=at(stamp))
    assert calls == [("shop_daily",), ("shop_daily",), ()]
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["accepted_fingerprint"] == p["observed_fingerprint"]
        assert p["sync_status"] == "succeeded"
        assert p["accepted_at"] == at("09:10")


def test_metadata_failure_preserves_observation_and_next_slot_retries(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    def fail():
        raise ValueError("secret must never persist")

    run_scheduled_sources(sales_pg, {"shop_daily": fail}, lambda ids, c: {}, now=at("08:20"))
    with sales_pg.connect() as c:
        p = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert p["check_status"] == "failed" and p["accepted_at"] is None
        assert p["error_code"] == "CHECK_FAILED"


def test_policy_config_is_validated_and_survives_legacy_time_edits(tmp_path):
    from app.jobs.project_sync import read_schedule

    path = tmp_path / "sync.toml"
    path.write_text(
        'times=["10:00"]\nsource_schedule_enabled=true\nwarehouse_times=["08:20","16:10"]\nfeishu_times=["08:20","10:10"]\n'
    )
    config = read_schedule(path)
    assert config.source_schedule_enabled is True
    assert config.warehouse_times == ("08:20", "16:10")


def test_selected_product_reader_only_projects_requested_table():
    from app.connectors.product_board import ProductBoardReader

    reader = object.__new__(ProductBoardReader)
    reader.latest = lambda selected=None: at("08:20").date()
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
        now=at("08:20"),
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
                sales_pg, {"shop_daily": lambda: 1 / 0}, lambda ids, c: 1 / 0, now=at("08:20")
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
        hour=8, minute=20, second=0, microsecond=0
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


def test_interrupted_slot_recovery_is_bounded_and_next_day_is_new(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    calls = []
    probe = {"shop_daily": lambda: {"revision": "stable"}}

    def sync(ids, c):
        calls.append(ids)
        return dict.fromkeys(ids, True)

    run_scheduled_sources(sales_pg, probe, sync, now=at("08:20"))
    with sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE source_checkpoints SET sync_status='running',sync_finished_at=NULL")
        )
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("08:25"))["checked"] == 0
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("08:41"))["checked"] == 1
    with sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE source_checkpoints SET sync_status='running',sync_finished_at=NULL")
        )
    assert run_scheduled_sources(sales_pg, probe, sync, now=at("09:01"))["checked"] == 0
    assert (
        run_scheduled_sources(sales_pg, probe, sync, now=at("08:20") + timedelta(days=1))["checked"]
        == 1
    )


def test_schedule_database_rejects_incomplete_sync_times(sales_pg):
    import pytest

    from app.jobs.source_schedule import run_scheduled_sources

    run_scheduled_sources(
        sales_pg,
        {"shop_daily": lambda: {"revision": "one"}},
        lambda ids, c: dict.fromkeys(ids, True),
        now=at("08:20"),
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
        now=at("08:20"),
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
        now=at("08:20"),
    )

    def login_expired():
        raise WarehousePageError("WAREHOUSE_WEB_LOGIN_REQUIRED")

    run_scheduled_sources(
        sales_pg, {"shop_daily": login_expired}, lambda ids, c: {}, now=at("09:10")
    )
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
    assert row["web_updated_at"] == observation["updated_at"]
    assert row["web_checked_at"] == at("08:20")
    assert row["accepted_at"] == at("08:20")
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
        now=at("08:20"),
        source_ids=("shop_daily",),
    )
    assert result["checked"] == 1
