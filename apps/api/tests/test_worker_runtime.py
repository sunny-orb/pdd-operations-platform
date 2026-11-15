"""真实子进程被信号终止后，常驻外壳留下可归因日志并可正常停止。"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import sqlalchemy as sa


def test_worker_records_killed_child_without_losing_signal_or_peak_memory(tmp_path):
    root = tmp_path / "isolated"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/sync-project.py").write_text(
        "import json,os,signal,sys\n"
        "print(json.dumps(dict(event='invoked',args=sys.argv[1:])),flush=True)\n"
        "os.kill(os.getpid(),signal.SIGKILL)\n"
    )
    worker = Path(__file__).resolve().parents[3] / "deploy/worker.py"
    wrapper = (
        "import importlib.util;from pathlib import Path;"
        f"s=importlib.util.spec_from_file_location('worker',{str(worker)!r});"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        f"m.ROOT=Path({str(root)!r});m.main()"
    )
    output = tmp_path / "worker.log"
    records = []
    with output.open("w") as log:
        process = subprocess.Popen([sys.executable, "-c", wrapper], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                records = [json.loads(line) for line in output.read_text().splitlines()]
                if any(r.get("event") == "sync_child_finished" for r in records):
                    break
                time.sleep(0.05)
            assert process.poll() is None
        finally:
            process.terminate()
            process.wait(timeout=5)
    finished = [r for r in records if r.get("event") == "sync_child_finished"]
    assert len(finished) == 1, records
    assert finished[0]["returncode"] == -9
    assert finished[0]["signal"] == "SIGKILL"
    assert finished[0]["children_peak_rss_bytes"] > 0
    assert finished[0]["elapsed_seconds"] >= 0
    invoked = [r for r in records if r.get("event") == "invoked"]
    assert invoked == [{"event": "invoked", "args": ["--scheduled"]}]
    assert process.returncode == 0


@pytest.mark.parametrize("source_schedule_enabled", [False, True])
def test_worker_scheduled_cli_forwards_region_reconciliation_policy_to_both_paths(
    tmp_path, monkeypatch, source_schedule_enabled
):
    """容器复用CLI；只替换外部作业执行边界，检查真实入口选择和参数。"""
    from app.jobs import source_monitor_runtime
    from tests.test_project_sync_runtime import cli_main

    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text(
        'times=["00:00"]\nmonitor_enabled=true\nreconciliation_time="18:25"\n'
        f"source_schedule_enabled={str(source_schedule_enabled).lower()}\n"
    )
    main = cli_main(monkeypatch, tmp_path, ["--scheduled"])
    engine = sa.create_engine("postgresql+psycopg://unused")
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: engine)
    monkeypatch.setitem(main.__globals__, "due_slot", lambda *_: "2026-11-16T18:25")
    calls = []

    def execute(received_engine, slot, **kwargs):
        assert received_engine is engine
        assert slot == "2026-11-16T18:25"
        calls.append(("project", kwargs))
        return {"status": "already_succeeded"}

    def monitor(received_engine, **kwargs):
        assert received_engine is engine
        calls.append(("monitor", kwargs))
        return {"status": "checked", "checked": 0}

    monkeypatch.setitem(main.__globals__, "execute_project", execute)
    monkeypatch.setattr(source_monitor_runtime, "execute_monitor", monitor)
    monkeypatch.setattr(
        source_monitor_runtime,
        "execute_source_schedule",
        lambda *_args, **_kwargs: {"status": "checked", "checked": 0},
    )
    assert main() == 0
    assert [kind for kind, _ in calls] == ["project", "monitor"]
    for kind, values in calls:
        assert values.get("reconciliation_sources", ()) == (
            ("product_regions",) if source_schedule_enabled else ()
        )
        if source_schedule_enabled or kind == "monitor":
            assert values["reconciliation_time"] == "18:25"
