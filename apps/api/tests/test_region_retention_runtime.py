"""地区回收调度预算与五分钟续做；真实PG会话，不执行生产维护。"""

import json
import runpy
import sys
import time
from pathlib import Path

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "config/region-retention.toml").write_text(
        (ROOT / "config/region-retention.toml").read_text()
    )
    monkeypatch.setattr("os.getloadavg", lambda: (0.0, 0.0, 0.0))
    return root


def test_incomplete_sync_never_opens_database(tmp_path):
    from app.modules.region_retention_runtime import maintain_regions

    assert maintain_regions(None, tmp_path, sync_complete=False) == {
        "status": "deferred",
        "code": "SYNC_INCOMPLETE",
    }


def test_disabled_policy_never_opens_database(project):
    from app.modules.region_retention_runtime import maintain_regions

    p = project / "config/region-retention.toml"
    p.write_text(p.read_text().replace("enabled = true", "enabled = false"))
    assert maintain_regions(None, project, sync_complete=True) == {"status": "disabled"}


def test_high_load_never_opens_database(project, monkeypatch):
    from app.modules.region_retention_runtime import maintain_regions

    monkeypatch.setattr("os.getloadavg", lambda: (10.0, 10.0, 10.0))
    assert maintain_regions(None, project, sync_complete=True) == {
        "status": "deferred",
        "code": "HOST_LOAD",
    }


@pytest.mark.parametrize("kind", ["config", "directory", "state", "lock"])
def test_symlink_never_writes_outside_project(sales_pg, project, kind):
    from app.modules.region_retention_runtime import maintain_regions

    outside = project.parent / "outside"
    outside.mkdir()
    directory = project / "var/maintenance"
    directory.mkdir(parents=True)
    target = {
        "config": project / "config/region-retention.toml",
        "directory": directory,
        "state": directory / "region-retention.json",
        "lock": directory / ".region-retention.lock",
    }[kind]
    if kind == "directory":
        target.rmdir()
        target.symlink_to(outside, target_is_directory=True)
    else:
        target.unlink(missing_ok=True)
        target.symlink_to(outside / "victim")
    assert maintain_regions(sales_pg, project, sync_complete=True) == {
        "status": "failed",
        "code": "REGION_RETENTION_PATH",
    }
    assert list(outside.iterdir()) == []


def test_idle_suppresses_repeated_work_for_five_minutes(sales_pg, project, monkeypatch):
    from app.modules import region_retention
    from app.modules.region_retention_runtime import maintain_regions

    calls = []

    def idle(c, policy):
        calls.append(c.scalar(sa.text("SELECT current_database()")))
        return {"status": "idle", "checked_rows": 0, "deleted_rows": 0}

    monkeypatch.setattr(region_retention, "step", idle)
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1000)["status"] == "idle"
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1299)["status"] == "not_due"
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1301)["status"] == "idle"
    assert len(calls) == 2
    path = project / "var/maintenance/region-retention.json"
    assert path.stat().st_size < 4096
    assert path.stat().st_mode & 0o077 == 0


def test_failure_retries_and_does_not_leak_error_or_mark_success(sales_pg, project, monkeypatch):
    from app.modules import region_retention
    from app.modules.region_retention_runtime import maintain_regions

    def fail(c, policy):
        raise RuntimeError("secret-should-never-appear")

    monkeypatch.setattr(region_retention, "step", fail)
    first = maintain_regions(sales_pg, project, sync_complete=True, now=1000)
    assert first["status"] == "failed"
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1299)["status"] == "not_due"
    monkeypatch.setattr(region_retention, "step", lambda c, policy: {"status": "idle"})
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1301)["status"] == "idle"
    assert "secret" not in json.dumps(first)
    assert "secret" not in (project / "var/maintenance/region-retention.json").read_text()


def test_retry_waits_five_minutes_after_failure_finishes(sales_pg, project, monkeypatch):
    from app.modules import region_retention
    from app.modules.region_retention_runtime import maintain_regions

    clock = [50.0]
    monkeypatch.setattr("app.modules.region_retention_runtime.time.monotonic", lambda: clock[0])

    def fail(c, policy):
        clock[0] += 5
        raise RuntimeError("failed after work")

    monkeypatch.setattr(region_retention, "step", fail)
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1000)["status"] == "failed"
    monkeypatch.setattr(region_retention, "step", lambda c, policy: {"status": "idle"})
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1304)["status"] == "not_due"
    assert maintain_regions(sales_pg, project, sync_complete=True, now=1305)["status"] == "idle"


def test_statement_and_transaction_budgets_do_not_escape_session(sales_pg, project, monkeypatch):
    from app.modules import database_retention, region_retention
    from app.modules.region_retention_runtime import maintain_regions

    observed = []

    def idle(c, policy):
        observed.append(
            tuple(
                c.execute(
                    sa.text(
                        "SELECT current_setting('statement_timeout'),"
                        "current_setting('lock_timeout'),"
                        "current_setting('transaction_timeout'),current_setting('work_mem'),"
                        "current_setting('max_parallel_workers_per_gather')"
                    )
                ).one()
            )
        )
        return {"status": "idle"}

    validate = database_retention.validate_schema

    def validate_budget(c):
        idle(c, None)
        assert c.scalar(sa.text("SELECT current_setting('transaction_read_only')")) == "on"
        validate(c)

    monkeypatch.setattr(database_retention, "validate_schema", validate_budget)
    monkeypatch.setattr(region_retention, "step", idle)
    assert maintain_regions(sales_pg, project, sync_complete=True)["status"] == "idle"
    assert observed == [("1500ms", "100ms", "2s", "4MB", "0")] * 2
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT current_setting('transaction_timeout')")) == "0"


def test_commit_failure_is_failed_not_reclaimed(sales_pg, project, monkeypatch):
    from app.modules import region_retention
    from app.modules.region_retention_runtime import maintain_regions

    monkeypatch.setattr(region_retention, "step", lambda c, p: {"status": "reclaimed"})

    def fail(c):
        raise RuntimeError("commit-private-error")

    sa.event.listen(sales_pg, "commit", fail)
    try:
        result = maintain_regions(sales_pg, project, sync_complete=True)
    finally:
        sa.event.remove(sales_pg, "commit", fail)
    assert result["status"] == "failed"
    assert "private" not in json.dumps(result)


def test_slow_catalog_is_cancelled_before_any_retirement(sales_pg, project, monkeypatch):
    from app.modules import database_retention, region_retention
    from app.modules.region_retention_runtime import maintain_regions

    monkeypatch.setattr(
        database_retention, "validate_schema", lambda c: c.exec_driver_sql("SELECT pg_sleep(10)")
    )
    monkeypatch.setattr(region_retention, "step", lambda *a: pytest.fail("预检失败不得回收"))
    start = time.monotonic()
    result = maintain_regions(sales_pg, project, sync_complete=True)
    assert result["status"] == "failed" and result["code"] == "REGION_RETENTION_TIMEOUT"
    assert time.monotonic() - start < 4
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM region_retirements")) == 0


def test_real_ingest_worker_reclaims_only_proven_obsolete_version(sales_pg, project):
    from app.modules.region_retention_runtime import maintain_regions
    from migrations_v2.local import local_url
    from tests.test_region_retention import sequence

    old = sequence(sales_pg)
    password = (ROOT / "var/local-db/sync.env").read_text().strip().split("=", 1)[1]
    ingest = sa.create_engine(
        local_url(sales_pg.url.database).set(username="pdd_ingest", password=password),
        hide_parameters=True,
    )
    try:
        with ingest.connect() as c:
            assert c.scalar(sa.text("SELECT session_user")) == "pdd_ingest"
            before = c.scalar(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='product_regions'"
                )
            )
        result = maintain_regions(ingest, project, sync_complete=True)
        assert result["status"] == "reclaimed" and result["deleted_rows"] == 3
        with ingest.connect() as c:
            assert (
                c.scalar(
                    sa.text(
                        "SELECT version_id FROM current_datasets "
                        "WHERE dataset_code='product_regions'"
                    )
                )
                == before
            )
            assert (
                c.scalar(
                    sa.text("SELECT state FROM dataset_storage WHERE version_id=:v"), {"v": old}
                )
                == "reclaimed"
            )
            assert (
                c.scalar(
                    sa.text(
                        "SELECT count(*) FROM read_product_region_records "
                        "WHERE dataset_version_id=:v"
                    ),
                    {"v": before},
                )
                == 3
            )
    finally:
        ingest.dispose()


@pytest.mark.parametrize("source_status", ["succeeded", "failed"])
def test_scheduled_maintenance_failure_preserves_source_result(
    sales_pg, project, monkeypatch, capsys, source_status
):
    from app.modules import archive_compaction, region_retention_runtime
    from app.modules.aftersales import statistics

    (project / "config/sync.toml").write_text('times=["10:00"]\n')
    main = runpy.run_path(str(ROOT / "scripts/sync-project.py"))["main"]
    monkeypatch.setitem(main.__globals__, "ROOT", project)
    monkeypatch.setattr(sys, "argv", ["sync-project.py", "--scheduled"])
    monkeypatch.setitem(main.__globals__, "due_slot", lambda *a: "test-slot")
    monkeypatch.setitem(main.__globals__, "runtime_engine", lambda: sales_pg)
    monkeypatch.setitem(
        main.__globals__, "execute_project", lambda *a, **kw: {"status": source_status}
    )
    monkeypatch.setattr(statistics, "maintain_statistics", lambda *a, **kw: {"status": "disabled"})
    monkeypatch.setattr(archive_compaction, "compact_archives", lambda *a, **kw: {})
    called = []

    def fail(engine, root, *, sync_complete):
        called.append(sync_complete)
        raise RuntimeError("private-maintenance-error")

    monkeypatch.setattr(region_retention_runtime, "maintain_regions", fail)
    assert main() == int(source_status == "failed")
    assert called == [source_status == "succeeded"]
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[0] == {"status": source_status}
    assert output[-1] == {
        "stage": "region_retention",
        "status": "failed",
        "code": "REGION_RETENTION_FAILED",
    }
