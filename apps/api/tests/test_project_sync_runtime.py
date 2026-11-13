"""平台触发与本机CLI共用整链；来源fake不替代真实PostgreSQL发布。"""

import importlib.util
import json
import runpy
import sys
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]
ROOT = Path(__file__).resolve().parents[3]


def runtime():
    assert importlib.util.find_spec("app.jobs.project_runtime") is not None, (
        "完整同步必须可从应用模块导入，不能要求平台加载本机脚本"
    )
    from app.jobs import project_runtime

    return project_runtime


def paths_for(tmp_path):
    return runtime().ProjectPaths(
        ingestion_root=tmp_path / "inputs",
        run_archive_root=tmp_path / "runs",
        refund_targets_config=tmp_path / "targets.toml",
        feishu_root=tmp_path / "feishu",
        scoring_archive_root=tmp_path / "score-evidence",
    )


def test_runtime_requires_explicit_absolute_paths_and_rejects_unknown_selection(tmp_path):
    module = runtime()
    with pytest.raises(ValueError, match="ABSOLUTE_SYNC_PATH_REQUIRED"):
        module.ProjectPaths(Path("inputs"), tmp_path, tmp_path, tmp_path, tmp_path)
    with pytest.raises(ValueError, match="INVALID_SYNC_DATASETS"):
        module.execute_project(
            object(),
            "fake-trigger",
            paths=paths_for(tmp_path),
            warehouse_password=lambda: pytest.fail("未执行不得读取凭据"),
            only=("unknown",),
        )


def test_runtime_weather_and_scores_never_read_warehouse_credentials(tmp_path, monkeypatch):
    from app.jobs import sync_weather
    from app.modules.product_scoring import publisher

    def no_credentials():
        pytest.fail("天气和评分不得读取仓库口令")

    paths = paths_for(tmp_path)
    engine = object()
    (tmp_path / "unrelated-cwd").mkdir()
    monkeypatch.chdir(tmp_path / "unrelated-cwd")

    def weather(received_engine, archive):
        assert received_engine is engine
        assert archive == tmp_path / "inputs/weather"
        return {"version_id": "weather-version"}

    def scores(received_engine, *, archive_root=None):
        assert received_engine is engine
        assert archive_root == tmp_path / "score-evidence"
        return {"versions": {"product_scores": "score-version"}}

    monkeypatch.setattr(sync_weather, "run_weather", weather)
    monkeypatch.setattr(publisher, "publish_scores", scores)
    steps = runtime().build_steps(engine, paths, no_credentials)
    assert steps["weather"]() == {"version_id": "weather-version"}
    assert steps["product_scores"]()["version_id"] == "score-version"


def test_portable_trigger_real_pg_complete_pipeline_idempotence_lock_and_failure(
    sales_pg, tmp_path, monkeypatch, capsys
):
    from app.connectors import aftersales
    from tests.test_aftersales_ingestion import sample

    paths = paths_for(tmp_path)
    paths.refund_targets_config.write_text(
        'application_policy_code="rolling_30d_and_natural_month"\n'
        '[total_refund_rate]\nlower="0.40"\nupper="0.43"\n'
        '[aftersale_refund_rate]\nlower="0.32"\nupper="0.36"\n'
    )
    received, closed = [], []
    broken = False

    class Reader:
        def __init__(self, *, password, heartbeat):
            assert password == "synthetic-secret-not-for-output"
            self.heartbeat = heartbeat

        def latest(self, code):
            if broken and code == "jst_returns":
                raise ConnectionError("synthetic-secret-not-for-output")
            return date(2026, 10, 27)

        def read_stable(self, code, end):
            self.heartbeat()
            received.append(code)
            _, raw, rows = sample(end)(code)
            return raw, rows

        def close(self):
            closed.append(1)

    monkeypatch.setattr(aftersales, "AftersalesReader", Reader)
    (tmp_path / "unrelated-cwd").mkdir()
    monkeypatch.chdir(tmp_path / "unrelated-cwd")
    module = runtime()
    only = (
        "jst_returns",
        "jst_shipped_sales",
        "jst_cancelled_sales",
        "refund_targets",
        "aftersales_report",
    )

    def trigger(slot):
        return module.execute_project(
            sales_pg,
            slot,
            paths=paths,
            warehouse_password=lambda: "synthetic-secret-not-for-output",
            only=only,
        )

    first = trigger("platform-batch-1")
    assert first["status"] == "succeeded"
    assert tuple(first["datasets"]) == only
    assert len(closed) == 3
    assert trigger("platform-batch-1") == {"status": "already_succeeded"}
    assert len(received) == 3
    with sales_pg.connect() as other:
        other.execute(sa.text("SELECT pg_advisory_lock(hashtext('project-source-sync'))"))
        other.commit()
        try:
            assert trigger("platform-batch-locked") == {"status": "already_running"}
        finally:
            other.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
            other.commit()
    second = trigger("platform-batch-2")
    assert second["status"] == "succeeded"
    assert {key: value["result"]["version_id"] for key, value in first["datasets"].items()} == {
        key: value["result"]["version_id"] for key, value in second["datasets"].items()
    }
    with sales_pg.connect() as c:
        heads = dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        )
        assert c.scalar(sa.text("SELECT count(*) FROM aftersales_reports")) == 3
        assert (
            c.scalar(sa.text("SELECT count(*) FROM version_inputs WHERE input_role='targets'")) == 3
        )
    broken = True
    for _ in range(2):
        failed = trigger("platform-batch-failed")
        assert failed["status"] == "failed"
        assert failed["datasets"]["jst_returns"]["error"] == "ConnectionError"
        assert failed["datasets"]["aftersales_report"] == {
            "status": "failed",
            "error": "DEPENDENCY_FAILED",
        }
        assert failed["datasets"]["jst_shipped_sales"]["status"] == "succeeded"
    assert trigger("platform-batch-failed") == {"status": "retry_budget_exhausted"}
    with sales_pg.connect() as c:
        assert (
            dict(c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all())
            == heads
        )
        assert c.scalar(sa.text("SELECT count(*) FROM aftersales_reports")) == 3
        assert (
            c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE lease_expires_at IS NOT NULL"))
            == 0
        )
        rows = c.execute(
            sa.text("SELECT status, evidence_ref FROM task_runs WHERE job_code='sync_project'")
        ).all()
    assert sorted(row.status for row in rows) == ["failed", "failed", "succeeded", "succeeded"]
    for row in rows:
        evidence = Path(row.evidence_ref)
        assert evidence.parent == tmp_path / "runs"
        assert evidence.stat().st_mode & 0o077 == 0
        assert json.loads(evidence.read_text())["status"] == row.status
        assert "synthetic-secret-not-for-output" not in evidence.read_text()
    assert "synthetic-secret-not-for-output" not in capsys.readouterr().out
    # 重跑复用三份原始来源，不再为十次成功接收保留十份重复输入。
    assert len(list((tmp_path / "inputs/aftersales").glob("*/input.ndjson"))) == 3


def cli_main(monkeypatch, tmp_path, args):
    main = runpy.run_path(str(ROOT / "scripts/sync-project.py"))["main"]
    monkeypatch.setitem(main.__globals__, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["sync-project.py", *args])
    return main


def test_cli_status_works_from_other_cwd_without_engine_or_credentials(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text('times=["10:00", "14:00", "18:00"]\n')
    main = cli_main(monkeypatch, tmp_path, ["--status"])
    for name in ("runtime_engine", "source_password"):
        monkeypatch.setitem(main.__globals__, name, lambda: pytest.fail("status不得连接/读口令"))
    (tmp_path / "unrelated-cwd").mkdir()
    monkeypatch.chdir(tmp_path / "unrelated-cwd")
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "times": ["10:00", "14:00", "18:00"],
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "max_attempts_per_slot": 2,
        "monitor_enabled": False,
        "monitor_interval_seconds": 900,
        "source_schedule_enabled": False,
        "warehouse_times": ["08:30", "09:40", "10:10", "10:50", "13:10", "15:10", "16:10"],
        "feishu_times": ["08:20", "10:10"],
        "reconciliation_time": "17:00",
        "reconciliation_retry_seconds": 300,
    }


@pytest.mark.parametrize(
    "status,exitcode,silent",
    [
        ("succeeded", 0, False),
        ("failed", 1, False),
        ("already_succeeded", 0, True),
        ("already_running", 0, True),
        ("retry_budget_exhausted", 0, True),
    ],
)
def test_cli_uses_portable_execution_and_preserves_output_exitcode(
    status, exitcode, silent, tmp_path, monkeypatch, capsys
):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text('times=["10:00"]\n')
    main = cli_main(monkeypatch, tmp_path, ["--run-now", "--only", "weather"])
    engine = sa.create_engine("postgresql+psycopg://unused")
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: engine)
    assert "execute_project" in main.__globals__, "CLI必须使用同一可迁移执行接口"

    def execute(received_engine, slot, *, paths, warehouse_password, only, max_attempts):
        assert received_engine is engine
        assert slot.startswith("manual:")
        assert paths.ingestion_root == tmp_path / "var/ingestion"
        assert paths.run_archive_root == tmp_path / "var/project-sync"
        assert paths.refund_targets_config == tmp_path / "config/aftersales-targets.toml"
        assert paths.feishu_root == tmp_path
        assert paths.scoring_archive_root == tmp_path / "var/scoring/product_board"
        assert callable(warehouse_password)
        assert only == ["weather"]
        assert max_attempts == 2
        return {"status": status}

    monkeypatch.setitem(main.__globals__, "execute_project", execute)
    assert main() == exitcode
    output = capsys.readouterr().out
    assert output == "" if silent else json.loads(output) == {"status": status}


def test_sales_targets_use_explicit_feishu_runtime_without_warehouse_password(
    sales_pg, tmp_path, monkeypatch
):
    from app.connectors import sales_targets
    from app.modules.sales.source_policy import SHOPS
    from tests.test_sales_targets import candidate

    with sales_pg.begin() as c:
        c.execute(
            sa.text("INSERT INTO shops (id,shop_name,status) VALUES (:id,:name,'active')"),
            [{"id": shop_id, "name": name} for shop_id, name, _ in SHOPS.values()],
        )
    expected = candidate()
    paths = paths_for(tmp_path)
    (tmp_path / "unrelated-cwd").mkdir()
    monkeypatch.chdir(tmp_path / "unrelated-cwd")

    class Reader:
        def __init__(self, *, root, heartbeat):
            assert root == tmp_path / "feishu"
            self.heartbeat = heartbeat

        def read_stable(self, *, basis):
            assert basis == "original_gmv"
            self.heartbeat()
            return expected

    monkeypatch.setattr(sales_targets, "FeishuTargetReader", Reader)
    result = runtime().execute_project(
        sales_pg,
        "targets-batch",
        paths=paths,
        warehouse_password=lambda: pytest.fail("飞书目标不得读仓库口令"),
        only=("sales_targets",),
    )
    assert result["status"] == "succeeded"
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM sales_targets")) == 12
        assert c.scalar(sa.text("SELECT count(*) FROM sales_target_shops")) == 72
    assert list((tmp_path / "inputs/sales_targets").glob("*/input.ndjson"))


@pytest.mark.parametrize("attempts", [0, 4])
def test_platform_cannot_bypass_bounded_attempts(attempts, tmp_path):
    with pytest.raises(ValueError, match="INVALID_SYNC_ATTEMPTS"):
        runtime().execute_project(
            object(),
            "batch",
            paths=paths_for(tmp_path),
            max_attempts=attempts,
            warehouse_password=lambda: pytest.fail("未执行不得读取凭据"),
            only=("weather",),
        )


def test_scoring_archive_path_is_required_and_absolute(tmp_path):
    module = runtime()
    with pytest.raises(TypeError):
        module.ProjectPaths(
            ingestion_root=tmp_path / "inputs",
            run_archive_root=tmp_path / "runs",
            refund_targets_config=tmp_path / "targets.toml",
            feishu_root=tmp_path / "feishu",
        )
    with pytest.raises(ValueError, match="ABSOLUTE_SYNC_PATH_REQUIRED"):
        module.ProjectPaths(
            ingestion_root=tmp_path / "inputs",
            run_archive_root=tmp_path / "runs",
            refund_targets_config=tmp_path / "targets.toml",
            feishu_root=tmp_path / "feishu",
            scoring_archive_root=Path("relative/scoring"),
        )


def test_scheduled_cli_excludes_migrated_sources_from_both_legacy_paths(
    sales_pg, tmp_path, monkeypatch
):
    """真实PG引擎装配CLI；在外部执行边界捕获所选范围，禁止混入迁移来源。"""
    from app.jobs import source_monitor_runtime
    from app.modules.data_status.schedule_policy import LEGACY_SOURCES, LEGACY_STEPS

    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text(
        'times=["00:00"]\nmonitor_enabled=true\nsource_schedule_enabled=true\n'
    )
    main = cli_main(monkeypatch, tmp_path, ["--scheduled"])
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: sales_pg)
    selections = []

    def execute(engine, slot, **kwargs):
        selections.append(("full", tuple(kwargs["only"])))
        return {"status": "succeeded"}

    def monitor(engine, **kwargs):
        selections.append(("monitor", tuple(kwargs["source_ids"])))
        return {"status": "checked", "checked": 0}

    def schedule(engine, **kwargs):
        selections.append(("schedule", tuple(kwargs["warehouse_times"])))
        assert callable(kwargs["warehouse_reader"].read)
        assert "warehouse_headers" not in kwargs
        assert kwargs["warehouse_reader"].context is None  # 未到源读取时不打开浏览器
        return {"status": "checked", "checked": 0}

    monkeypatch.setitem(main.__globals__, "execute_project", execute)
    monkeypatch.setattr(source_monitor_runtime, "execute_monitor", monitor)
    monkeypatch.setattr(source_monitor_runtime, "execute_source_schedule", schedule)
    assert main() == 0
    assert selections[0] == ("full", LEGACY_STEPS)
    assert selections[1] == ("monitor", LEGACY_SOURCES)
    assert selections[2][1][-1] == "16:10"


def test_new_schedule_runs_before_legacy_first_slot_when_old_monitor_disabled(
    sales_pg, tmp_path, monkeypatch
):
    from datetime import datetime

    from app.jobs import source_monitor_runtime

    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text(
        'times=["10:00"]\nmonitor_enabled=false\nsource_schedule_enabled=true\n'
    )
    main = cli_main(monkeypatch, tmp_path, ["--scheduled"])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 4, 8, 20, tzinfo=tz)

    monkeypatch.setitem(main.__globals__, "datetime", Clock)
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: sales_pg)
    calls = []

    def schedule(*args, **kwargs):
        calls.append(True)
        return {"status": "checked", "checked": 0}

    monkeypatch.setattr(source_monitor_runtime, "execute_source_schedule", schedule)
    assert main() == 0
    assert calls == [True]
