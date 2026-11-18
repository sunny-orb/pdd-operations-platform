"""售后统计维护：真实隔离PG、固定权限/路径及发布结果不变。"""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.aftersales.rpa_publisher import sync_rpa
from tests.test_aftersales_rpa import delivery

pytest_plugins = ["tests.sales_pg_support"]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "config/aftersales-statistics.toml").write_text(
        (ROOT / "config/aftersales-statistics.toml").read_text()
    )
    monkeypatch.setattr("os.getloadavg", lambda: (0.0, 0.0, 0.0))
    return root


def test_two_tables_once_per_token_and_fixed_report_unchanged(sales_pg, project):
    from app.modules.aftersales.query import AfterQuery, overview
    from app.modules.aftersales.repository import snapshot
    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    with snapshot(sales_pg) as repo:
        expected = overview(repo, AfterQuery())
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("ANALYZE "):
            statements.append(statement)

    sa.event.listen(sales_pg, "before_cursor_execute", capture)
    try:
        first = maintain_statistics(sales_pg, project, sync_complete=True, now=1000)
        second = maintain_statistics(sales_pg, project, sync_complete=True, now=1060)
        third = maintain_statistics(sales_pg, project, sync_complete=True, now=1120)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", capture)
    assert first["status"] == second["status"] == "succeeded"
    assert first["table"] == "return_records"
    assert second["table"] == "product_sales_records"
    assert third["status"] == "unchanged"
    assert statements == [
        "ANALYZE public.return_records (dataset_version_id,row_kind,period_start,period_end)",
        "ANALYZE public.product_sales_records "
        "(dataset_version_id,row_kind,period_start,period_end)",
    ]
    with snapshot(sales_pg) as repo:
        assert overview(repo, AfterQuery()) == expected
    state = project / "var/maintenance/aftersales-statistics.json"
    assert state.stat().st_size < 8192 and state.stat().st_mode & 0o077 == 0
    assert set(json.loads(state.read_text())["tables"]) == {
        "return_records",
        "product_sales_records",
    }


def test_failure_waits_300_seconds_and_success_is_only_after_commit(sales_pg, project):
    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    calls = []

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("ANALYZE "):
            calls.append(statement)
            raise RuntimeError("secret-must-not-be-reported")

    sa.event.listen(sales_pg, "before_cursor_execute", fail)
    try:
        first = maintain_statistics(sales_pg, project, sync_complete=True, now=1000)
        maintain_statistics(sales_pg, project, sync_complete=True, now=1001)
        result = maintain_statistics(sales_pg, project, sync_complete=True, now=1299)
        assert result["status"] == "not_due"
        assert len(calls) == 2
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail)
    assert first["status"] == "failed"
    state = json.loads((project / "var/maintenance/aftersales-statistics.json").read_text())
    assert all(not row.get("succeeded_token") for row in state["tables"].values())
    assert "secret" not in json.dumps(state)
    assert (
        maintain_statistics(sales_pg, project, sync_complete=True, now=1300)["status"]
        == "succeeded"
    )


@pytest.mark.parametrize(
    "key", ["project-source-sync", "m5-source-report", "aftersales-statistics"]
)
def test_busy_lock_and_incomplete_sync_never_analyze(sales_pg, project, key):
    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    with sales_pg.connect() as held, held.begin():
        held.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
        assert maintain_statistics(sales_pg, project, sync_complete=True)["status"] == "busy"
    assert maintain_statistics(sales_pg, project, sync_complete=False)["status"] == "deferred"
    assert not (project / "var/maintenance/aftersales-statistics.json").exists()


def test_session_budgets_are_local_and_commit_failure_is_not_success(sales_pg, project):
    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    observed = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("ANALYZE "):
            observed.append(
                conn.execute(
                    sa.text("""SELECT current_setting('lock_timeout'),
                current_setting('statement_timeout'),current_setting('transaction_timeout'),
                current_setting('maintenance_work_mem'),current_setting('vacuum_buffer_usage_limit'),
                current_setting('vacuum_cost_delay'),current_setting('vacuum_cost_limit')""")
                ).one()
            )

    def fail_commit(conn):
        raise RuntimeError("commit-private-failure")

    sa.event.listen(sales_pg, "before_cursor_execute", capture)
    sa.event.listen(sales_pg, "commit", fail_commit)
    try:
        result = maintain_statistics(sales_pg, project, sync_complete=True, now=1000)
    finally:
        sa.event.remove(sales_pg, "commit", fail_commit)
        sa.event.remove(sales_pg, "before_cursor_execute", capture)
    assert result["status"] == "failed"
    assert tuple(observed[0]) == ("100ms", "20s", "25s", "16MB", "1MB", "5ms", "50")
    state = json.loads((project / "var/maintenance/aftersales-statistics.json").read_text())
    assert state["tables"]["return_records"]["succeeded_token"] is None
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT current_setting('transaction_timeout')")) == "0"


def test_interrupted_attempt_retains_retry_state_and_releases_locks(sales_pg, project):
    from app.jobs.process_guard import TaskInterrupted
    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("ANALYZE "):
            raise TaskInterrupted()

    sa.event.listen(sales_pg, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(TaskInterrupted):
            maintain_statistics(sales_pg, project, sync_complete=True, now=1000)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", interrupt)
    entry = json.loads((project / "var/maintenance/aftersales-statistics.json").read_text())[
        "tables"
    ]["return_records"]
    assert entry["status"] == "interrupted" and entry["succeeded_token"] is None
    assert (
        maintain_statistics(sales_pg, project, sync_complete=True, now=1300)["status"]
        == "succeeded"
    )


@pytest.mark.parametrize("target", ["directory", "state", "lock", "config"])
def test_symlinks_are_rejected_without_writing_outside_project(sales_pg, project, target):
    from app.modules.aftersales.statistics import maintain_statistics

    outside = project.parent / "outside"
    outside.mkdir()
    directory = project / "var/maintenance"
    directory.mkdir(parents=True)
    if target == "directory":
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
    else:
        path = (
            project / "config/aftersales-statistics.toml"
            if target == "config"
            else directory
            / ("aftersales-statistics.json" if target == "state" else ".aftersales-statistics.lock")
        )
        if path.exists():
            path.unlink()
        path.symlink_to(outside / "victim")
    result = maintain_statistics(sales_pg, project, sync_complete=True)
    assert result == {"status": "failed", "code": "STATISTICS_PATH"}
    assert list(outside.iterdir()) == []


def test_wrong_identity_load_and_missing_rpa_are_deferred_safely(sales_pg, project, monkeypatch):
    from app.modules.aftersales.statistics import maintain_statistics

    assert maintain_statistics(sales_pg, project, sync_complete=True)["status"] == "no_rpa"
    monkeypatch.setattr("os.getloadavg", lambda: (10.0, 10.0, 10.0))
    assert maintain_statistics(sales_pg, project, sync_complete=True) == {
        "status": "deferred",
        "code": "HOST_LOAD",
    }
    monkeypatch.setattr("os.getloadavg", lambda: (0.0, 0.0, 0.0))
    wrong = sa.create_engine(sales_pg.url.set(username="pdd_app"))
    try:
        assert maintain_statistics(wrong, project, sync_complete=True) == {
            "status": "failed",
            "code": "STATISTICS_IDENTITY",
        }
    finally:
        wrong.dispose()


def test_table_lock_timeout_is_bounded_and_does_not_change_publication(sales_pg, project):
    import time

    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    with sales_pg.connect() as held, held.begin():
        held.execute(sa.text("LOCK TABLE public.return_records IN ACCESS EXCLUSIVE MODE"))
        at = time.monotonic()
        result = maintain_statistics(sales_pg, project, sync_complete=True, now=1000)
        assert time.monotonic() - at < 2
    assert result == {"status": "failed", "code": "STATISTICS_TIMEOUT", "table": "return_records"}
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")) == 0


def test_maintain_privilege_is_checked_in_postgresql_before_analyze(sales_pg, project, monkeypatch):
    from app.modules.aftersales import statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")

    def reduced_privilege(conn, cursor, statement, parameters, context, executemany):
        if "has_table_privilege" in statement:
            # 仅本次真实PG事务在权限门禁前降权，不改变服务器角色或其他连接。
            conn.execute(sa.text("SET LOCAL ROLE pdd_app"))

    sa.event.listen(sales_pg, "before_cursor_execute", reduced_privilege)
    try:
        result = statistics.maintain_statistics(sales_pg, project, sync_complete=True, now=1000)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", reduced_privilege)
    assert result == {
        "status": "failed",
        "code": "STATISTICS_PERMISSION",
        "table": "return_records",
    }
    state = json.loads((project / "var/maintenance/aftersales-statistics.json").read_text())
    assert state["tables"]["return_records"]["succeeded_token"] is None


@pytest.mark.parametrize(
    "monitor_status,exitcode,maintained", [("not_due", 0, True), ("failed", 1, False)]
)
def test_idle_scheduled_hook_runs_and_monitor_failure_preserves_exitcode(
    sales_pg, project, monkeypatch, capsys, monitor_status, exitcode, maintained
):
    from app.jobs import source_monitor_runtime
    from tests.test_project_sync_runtime import cli_main

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    (project / "config/sync.toml").write_text('times=["10:00"]\nmonitor_enabled=true\n')
    main = cli_main(monkeypatch, project, ["--scheduled"])
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: sales_pg)
    monkeypatch.setitem(main.__globals__, "due_slot", lambda *_: None)
    monkeypatch.setattr(
        source_monitor_runtime, "execute_monitor", lambda *a, **k: {"status": monitor_status}
    )
    assert main() == exitcode
    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    row = next(r for r in outputs if r.get("stage") == "aftersales_statistics")
    assert row["status"] == ("succeeded" if maintained else "deferred")


def test_new_small_rpa_version_refreshes_stale_estimates_using_existing_indexes(sales_pg, project):
    from datetime import date

    from app.modules.aftersales.statistics import maintain_statistics

    def sample(end):
        raw = delivery(end)
        return [
            {
                **raw[0],
                "id": i + 100,
                "json_data": {
                    **raw[0]["json_data"],
                    "商品编码": f"SKU{i:05}",
                    "款式编码": f"K{(i * 97) % 700:04}",
                    "实际入库金额": "10",
                },
            }
            for i in range(11018)
        ] + [r for r in raw if r["class"] != "按退货原因"]

    def plan():
        from app.modules.aftersales.repository import snapshot

        with snapshot(sales_pg) as repo:
            src = repo.inputs()["j1_returns"]
            value = repo.c.execute(
                sa.text("""EXPLAIN (FORMAT JSON)
                SELECT source_style_code,SUM(actual_returned_qty) FROM read_return_records
                WHERE dataset_version_id=:version AND row_kind='detail'
                AND period_start>=:start AND period_end<=:end
                AND period_start IS NOT NULL AND period_end IS NOT NULL
                GROUP BY source_style_code"""),
                {"version": src["id"], "start": src["window_start"], "end": src["window_end"]},
            ).scalar_one()[0]["Plan"]

        def nodes(node):
            return [node] + [n for child in node.get("Plans", []) for n in nodes(child)]

        return next(n for n in nodes(value) if n.get("Relation Name") == "return_records")

    with sales_pg.begin() as c:
        c.execute(sa.text("ALTER TABLE public.return_records SET (autovacuum_enabled=false)"))
    sync_rpa(sales_pg, lambda: sample(date(2026, 11, 9)), project / "var/ingestion")
    assert (
        maintain_statistics(sales_pg, project, sync_complete=True, now=1000)["status"]
        == "succeeded"
    )
    sync_rpa(sales_pg, lambda: sample(date(2026, 11, 16)), project / "var/ingestion")
    before = plan()
    assert before["Plan Rows"] <= 2 and before["Node Type"] == "Index Scan"
    assert (
        maintain_statistics(sales_pg, project, sync_complete=True, now=1300)["status"]
        == "succeeded"
    )
    after = plan()
    # 四列统计恢复新版本规模；日期与版本相关，独立选择率估计仍可低于实际行数。
    assert after["Plan Rows"] >= 5000
    assert after["Node Type"] in {"Bitmap Heap Scan", "Seq Scan"}


def test_running_source_task_defers_even_after_advisory_lock_is_released(sales_pg, project):
    from app.modules.aftersales.statistics import maintain_statistics

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO task_runs
            (id,job_code,request_ref,lock_key,status,queued_at,started_at,worker_ref,lease_expires_at)
            VALUES('pending-rpa','sync_rpa_aftersales','synthetic','m5-source-report','running',
            clock_timestamp(),clock_timestamp(),'synthetic',
            clock_timestamp()+interval '5 minutes')""")
        )
    assert maintain_statistics(sales_pg, project, sync_complete=True) == {
        "status": "deferred",
        "code": "SYNC_RUNNING",
    }
    assert not (project / "var/maintenance/aftersales-statistics.json").exists()


def test_parent_traversal_and_invalid_existing_state_fail_closed(sales_pg, project):
    from app.modules.aftersales.statistics import maintain_statistics

    assert maintain_statistics(sales_pg, project / ".." / "outside", sync_complete=True) == {
        "status": "failed",
        "code": "STATISTICS_PATH",
    }
    directory = project / "var/maintenance"
    directory.mkdir(parents=True)
    state = directory / "aftersales-statistics.json"
    state.write_text('{"revision":1,"tables":{"other_table":{}}}')
    state.chmod(0o600)
    result = maintain_statistics(sales_pg, project, sync_complete=True)
    assert result == {"status": "failed", "code": "STATISTICS_STATE"}
    assert "other_table" in state.read_text()


def test_maintenance_failure_does_not_fail_successful_scheduled_round(
    sales_pg, project, monkeypatch, capsys
):
    from app.jobs import source_monitor_runtime
    from tests.test_project_sync_runtime import cli_main

    sync_rpa(sales_pg, delivery, project / "var/ingestion")
    (project / "config/sync.toml").write_text('times=["10:00"]\nmonitor_enabled=true\n')
    main = cli_main(monkeypatch, project, ["--scheduled"])
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: sales_pg)
    monkeypatch.setitem(main.__globals__, "due_slot", lambda *_: None)
    monkeypatch.setattr(
        source_monitor_runtime, "execute_monitor", lambda *a, **k: {"status": "not_due"}
    )

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("ANALYZE "):
            raise RuntimeError("secret-maintenance-failure")

    sa.event.listen(sales_pg, "before_cursor_execute", fail)
    try:
        assert main() == 0
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail)
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert next(r for r in rows if r.get("stage") == "aftersales_statistics")["status"] == "failed"
    assert "secret" not in output
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")) == 0
