"""旧售后停用只限制采集/派生，不删除数据、不影响RPA和其他业务。"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.jobs import project_runtime, source_monitor_runtime
from app.jobs.project_sync import Schedule, read_schedule
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_storage import candidate
from tests.test_project_sync_runtime import paths_for

pytest_plugins = ["tests.sales_pg_support"]
ROOT = Path(__file__).resolve().parents[3]
OLD_STEPS = ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales", "aftersales_report")
OLD_SOURCES = ("jst_returns", "jst_sales", "jst_master")


def test_switch_defaults_to_enabled_and_rejects_non_boolean(tmp_path):
    assert Schedule().jst_aftersales_enabled is True
    path = tmp_path / "sync.toml"
    path.write_text("jst_aftersales_enabled = false\n")
    assert read_schedule(path).jst_aftersales_enabled is False
    with pytest.raises(ValueError, match="INVALID_SYNC_SCHEDULE"):
        Schedule(jst_aftersales_enabled="false")


def test_disabled_project_skips_old_steps_but_keeps_shared_targets_real_pg(
    sales_pg, tmp_path, monkeypatch
):
    seed = ProductBoardStore(sales_pg, tmp_path / "seed").run(lambda hb: candidate())
    version = seed["versions"]["profile_links"]
    called = []

    def step(name):
        def run():
            called.append(name)
            return {"version_id": version}

        return run

    monkeypatch.setattr(
        project_runtime,
        "build_steps",
        lambda *a, **k: {
            name: step(name)
            for name in (*OLD_STEPS, "refund_targets", "weather", "product_regions")
        },
    )
    with sales_pg.connect() as c:
        before = c.execute(
            sa.text("SELECT dataset_code,version_id FROM current_datasets ORDER BY 1")
        ).all()
    args = dict(paths=paths_for(tmp_path), warehouse_password=lambda: pytest.fail("不应读凭据"))
    result = project_runtime.execute_project(
        sales_pg, "disabled", **args, jst_aftersales_enabled=False
    )
    assert result["status"] == "succeeded"
    assert set(called) == {"refund_targets", "weather", "product_regions"}
    with pytest.raises(ValueError, match="JST_AFTERSALES_DISABLED"):
        project_runtime.execute_project(
            sales_pg,
            "explicit-disabled",
            **args,
            only=("jst_returns",),
            jst_aftersales_enabled=False,
        )
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT dataset_code,version_id FROM current_datasets ORDER BY 1")
            ).all()
            == before
        )
        assert (
            c.scalar(
                sa.text("SELECT count(*) FROM task_runs WHERE request_ref='explicit-disabled'")
            )
            == 0
        )
    called.clear()
    assert (
        project_runtime.execute_project(sales_pg, "enabled", **args, only=OLD_STEPS)["status"]
        == "succeeded"
    )
    assert tuple(called) == OLD_STEPS


@pytest.mark.parametrize("selected", [None, (*OLD_SOURCES, "weather_bulletin", "aftersales_rpa")])
def test_disabled_monitor_ignores_failed_jst_and_pending_old_reports_real_pg(
    sales_pg, tmp_path, monkeypatch, selected
):
    now = datetime.now(UTC)
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO task_runs
            (id,job_code,request_ref,lock_key,status,queued_at,started_at,finished_at,error_code)
            VALUES (:id,'sync_jst_returns','old-failure','old-jst','failed',
                    :t,:t,:t,'SYNC_FAILED')"""),
            {"id": str(uuid4()), "t": now - timedelta(hours=1)},
        )
    calls = []

    def probe(code):
        assert code not in OLD_SOURCES, "停用来源不能继续检查或失败重试"
        calls.append(code)
        return {"revision": "new"}

    monkeypatch.setattr(
        source_monitor_runtime.SourceProbes,
        "functions",
        lambda self: {
            code: lambda code=code: probe(code)
            for code in (*OLD_SOURCES, "weather_bulletin", "aftersales_rpa")
        },
    )
    monkeypatch.setattr(
        source_monitor_runtime, "pending_processing", lambda c: ["aftersales_report"]
    )
    executed = []

    def execute(*a, **kwargs):
        executed.extend(kwargs["only"])
        return {"datasets": {step: {"status": "succeeded"} for step in kwargs["only"]}}

    monkeypatch.setattr(source_monitor_runtime, "execute_project", execute)
    received = []
    monkeypatch.setattr(source_monitor_runtime, "sync_rpa", lambda *args: received.append(args[2]))
    result = source_monitor_runtime.execute_monitor(
        sales_pg,
        paths=paths_for(tmp_path),
        warehouse_password=lambda: pytest.fail("不应读凭据"),
        source_ids=selected,
        jst_aftersales_enabled=False,
    )
    assert result["status"] == "checked"
    assert set(calls) == {"weather_bulletin", "aftersales_rpa"}
    assert executed == ["weather"]
    assert received == [paths_for(tmp_path).ingestion_root / "aftersales_rpa"]
    with sales_pg.connect() as c:
        assert set(c.execute(sa.text("SELECT source_id FROM source_checkpoints")).scalars()) == set(
            calls
        )
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs")) == 1


def cli(monkeypatch, tmp_path, argv):
    spec = importlib.util.spec_from_file_location("jst_stop_cli", ROOT / "scripts/sync-project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["sync-project.py", *argv])
    return module


def test_changing_times_preserves_disabled_switch(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config/sync.toml"
    path.write_text("jst_aftersales_enabled = false\n")
    module = cli(monkeypatch, tmp_path, ["--set-times", "12:00"])
    assert module.main() == 0
    assert read_schedule(path).jst_aftersales_enabled is False
    assert read_schedule(path).times == ("12:00",)


@pytest.mark.parametrize(
    "mode,new_schedule",
    [
        ("--scheduled", True),
        ("--scheduled", False),
        ("--run-now", True),
        ("--run-now", False),
        ("--check-updates", True),
    ],
)
def test_cli_applies_switch_to_every_project_and_monitor_entry(
    tmp_path, monkeypatch, mode, new_schedule
):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text(
        "jst_aftersales_enabled = false\nmonitor_enabled = true\n"
        f"source_schedule_enabled = {str(new_schedule).lower()}\n"
    )
    module = cli(monkeypatch, tmp_path, [mode])
    monkeypatch.setattr(module, "due_slot", lambda *a: "test-slot")
    monkeypatch.setattr(
        module, "runtime_engine", lambda: type("Engine", (), {"dispose": lambda self: None})()
    )
    from app.modules import archive_compaction

    monkeypatch.setattr(archive_compaction, "compact_archives", lambda *a, **kw: {})
    calls = []

    def execute(*a, **kw):
        assert kw.get("jst_aftersales_enabled") is False
        assert not set(kw.get("only") or ()) & set(OLD_STEPS)
        calls.append("project")
        return {"status": "succeeded", "datasets": {}}

    def monitor(*a, **kw):
        assert kw.get("jst_aftersales_enabled") is False
        calls.append("monitor")
        return {"status": "checked", "checked": 0}

    monkeypatch.setattr(module, "execute_project", execute)
    monkeypatch.setattr(source_monitor_runtime, "execute_monitor", monitor)
    monkeypatch.setattr(
        source_monitor_runtime,
        "execute_source_schedule",
        lambda *a, **kw: {"status": "checked", "checked": 0},
    )
    assert module.main() == 0
    assert ("project" in calls) == (mode != "--check-updates")
    assert ("monitor" in calls) == (mode != "--run-now")
