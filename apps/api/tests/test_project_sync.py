from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from app.jobs.project_sync import Schedule, due_slot, run_project
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]
TZ = ZoneInfo("Asia/Shanghai")


def test_shanghai_slots_and_editable_times():
    cfg = Schedule(times=("10:00", "14:00", "18:00"))
    assert due_slot(cfg, datetime(2026, 10, 28, 1, 59, tzinfo=ZoneInfo("UTC"))) is None
    assert (
        due_slot(cfg, datetime(2026, 10, 28, 6, 1, tzinfo=ZoneInfo("UTC")))
        == "2026-10-28T14:00+08:00"
    )
    assert (
        due_slot(Schedule(times=("15:30",)), datetime(2026, 10, 28, 16, 0, tzinfo=TZ))
        == "2026-10-28T15:30+08:00"
    )
    with pytest.raises(ValueError):
        Schedule(times=("24:00",))
    with pytest.raises(ValueError):
        Schedule(times=("10:00", "10:00"))


def test_real_pg_slot_dedup_failure_budget_and_process_lock(sales_pg, tmp_path):
    result = ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: candidate())
    vid = result["versions"]["profile_links"]
    called = []

    def good():
        called.append(1)
        return dict(version_id=vid)

    out = run_project(sales_pg, "slot-a", {"one": good}, tmp_path)
    assert out["status"] == "succeeded"
    assert run_project(sales_pg, "slot-a", {"one": good}, tmp_path)["status"] == "already_succeeded"
    assert len(called) == 1
    with sales_pg.connect() as other:
        other.execute(sa.text("SELECT pg_advisory_lock(hashtext('project-source-sync'))"))
        other.commit()
        assert (
            run_project(sales_pg, "slot-b", {"one": good}, tmp_path)["status"] == "already_running"
        )
        other.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
        other.commit()

    def fail():
        raise ValueError("sensitive detail must not be logged")

    for _ in range(2):
        out = run_project(sales_pg, "slot-c", {"good": good, "bad": fail}, tmp_path)
        assert out["status"] == "failed"
        assert out["datasets"]["good"]["status"] == "succeeded"
        assert out["datasets"]["bad"]["error"] == "ValueError"
    assert (
        run_project(sales_pg, "slot-c", {"one": good}, tmp_path)["status"]
        == "retry_budget_exhausted"
    )


def test_dependency_failure_does_not_publish_scores_from_old_input(sales_pg, tmp_path):
    called = []

    def fail():
        raise ValueError("source failed")

    def score():
        called.append(1)

    result = run_project(
        sales_pg,
        "dependency",
        {"products": fail, "product_scores": score},
        tmp_path,
        depends_on={"product_scores": ("products",)},
    )
    assert result["status"] == "failed"
    assert result["datasets"]["product_scores"]["error"] == "DEPENDENCY_FAILED"
    assert not called


def test_schedule_file_changes_are_loaded_each_tick(tmp_path):
    from app.jobs.project_sync import read_schedule

    path = tmp_path / "sync.toml"
    path.write_text('times=["10:00"]\n')
    now = datetime(2026, 10, 28, 11, 0, tzinfo=TZ)
    assert due_slot(read_schedule(path), now) is not None
    path.write_text('times=["12:00"]\n')
    assert due_slot(read_schedule(path), now) is None


def test_manual_cli_shares_the_project_source_lock(sales_pg):
    from app.jobs.project_sync import run_guarded

    called = []
    with sales_pg.connect() as c:
        c.execute(sa.text("SELECT pg_advisory_lock(hashtext('project-source-sync'))"))
        c.commit()
        assert run_guarded(sales_pg, lambda: called.append(1)) == 1
        assert not called
        c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
        c.commit()
    assert run_guarded(sales_pg, lambda: 0) == 0


def test_weather_step_never_reads_warehouse_credentials(tmp_path, monkeypatch):
    import runpy
    from pathlib import Path

    from app.jobs import sync_weather

    calls = []

    def no_credentials():
        raise AssertionError("天气不得读取数仓凭据")

    def weather(engine, archive):
        calls.append((engine, archive))
        return {"version_id": "local-weather-version"}

    monkeypatch.setattr(sync_weather, "run_weather", weather)
    script = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/sync-project.py"))
    build = script["build_steps"]
    build.__globals__["ROOT"] = tmp_path
    engine = object()
    steps = build(engine, no_credentials)
    assert steps["weather"]() == {"version_id": "local-weather-version"}
    assert calls == [(engine, tmp_path / "var/ingestion/weather")]


@pytest.mark.parametrize(
    "name,job",
    [
        ("products", "sync_product_board"),
        ("shop_daily", "sync_shop_daily"),
        ("shop_channel_star_shop", "sync_shop_channel_star_shop"),
    ],
)
def test_connection_failure_is_recorded_in_each_source_task(
    sales_pg, tmp_path, monkeypatch, name, job
):
    import runpy
    from pathlib import Path

    from app.connectors.product_board import ProductBoardReader
    from app.connectors.sales_sources import SalesSourceReader
    from app.connectors.shop_daily import ShopDailyReader

    def broken(*_, **__):
        raise ConnectionError("no upstream credentials are used")

    for cls in (ProductBoardReader, ShopDailyReader, SalesSourceReader):
        monkeypatch.setattr(cls, "__init__", broken)
    script = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/sync-project.py"))
    build = script["build_steps"]
    build.__globals__["ROOT"] = tmp_path
    with pytest.raises(ConnectionError):
        build(sales_pg, "synthetic")[name]()
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT status FROM task_runs WHERE job_code=:job"), dict(job=job)
            ).scalar_one()
            == "failed"
        )
