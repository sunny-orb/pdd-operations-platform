"""维护与发布并发：快照过期/占锁/断言失败必须完整保留旧数据。"""

import os

import pytest
import sqlalchemy as sa

from app.modules.database_archive import drill, export
from app.modules.database_retention import apply, maintenance, plan
from tests.test_database_archive import old_candidate

pytest_plugins = ["tests.sales_pg_support"]


def test_busy_lock_preserves_schedule_slot_and_watermark(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources
    from tests.test_source_schedule import at

    with maintenance(sales_pg):
        result = run_scheduled_sources(
            sales_pg,
            {"shop_daily": lambda: pytest.fail("probe")},
            lambda *_: pytest.fail("sync"),
            now=at("08:10"),
        )
        assert result["status"] == "already_running"
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM source_checkpoints")) == 0


def test_new_current_invalidates_prepared_archive(sales_pg, tmp_path):
    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "stale", key)
    drill(bundle, key)
    with sales_pg.begin() as c:
        c.execute(sa.text("UPDATE current_datasets SET version_id='product_daily-a'"))
    with pytest.raises(ValueError, match="RETENTION_PLAN_CHANGED"):
        apply(sales_pg, selected, bundle, key)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_daily_facts")) == 3
        assert c.scalar(sa.text("SELECT count(*) FROM retention_operations")) == 0


def test_failure_after_delete_rolls_back_whole_version(sales_pg, tmp_path, monkeypatch):
    from app.modules import database_retention as retention

    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "crash", key)
    drill(bundle, key)

    def crash(*_):
        raise RuntimeError("synthetic-crash-before-commit")

    monkeypatch.setattr(retention, "_finish", crash)
    with pytest.raises(RuntimeError, match="synthetic-crash"):
        retention.apply(sales_pg, selected, bundle, key)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_daily_facts")) == 3
        assert (
            c.scalar(
                sa.text("SELECT state FROM dataset_storage WHERE version_id='product_daily-a'")
            )
            == "online"
        )
        assert c.scalar(sa.text("SELECT count(*) FROM retention_operations")) == 0


def test_restore_required_is_pending_not_source_failure(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources
    from tests.test_source_schedule import at

    def needs_restore(*_):
        raise ValueError("DATASET_RESTORE_REQUIRED")

    result = run_scheduled_sources(
        sales_pg, {"shop_daily": lambda: {"revision": "new"}}, needs_restore, now=at("08:40")
    )
    assert result["status"] == "deferred"
    assert result["failed"] == 0
    with sales_pg.connect() as c:
        checkpoint = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert checkpoint["accepted_fingerprint"] is None
        assert checkpoint["error_code"] == "DATASET_RESTORE_REQUIRED"
        assert checkpoint["sync_status"] is None


def test_database_rejects_committing_half_finished_maintenance(sales_pg, tmp_path):
    from app.modules.database_retention import _operation, _receipt

    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "partial", key)
    drill(bundle, key)
    manifest, receipt = _receipt(bundle, key)
    with pytest.raises(sa.exc.DBAPIError, match="RETENTION_INCOMPLETE"):
        with maintenance(sales_pg) as c:
            _operation(c, manifest, receipt, "offline")
            c.execute(
                sa.text(
                    "DELETE FROM product_daily_facts WHERE dataset_version_id='product_daily-a'"
                )
            )
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_daily_facts")) == 3


def test_maintenance_rechecks_use_fresh_read_committed_snapshot(sales_pg):
    with maintenance(sales_pg.execution_options(isolation_level="REPEATABLE READ")) as c:
        assert c.scalar(sa.text("SHOW transaction_isolation")) == "read committed"


def test_existing_repeatable_reader_keeps_rows_during_offline(sales_pg, tmp_path):
    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "reader", key)
    drill(bundle, key)
    query = sa.text(
        "SELECT count(*) FROM product_daily_facts WHERE dataset_version_id='product_daily-a'"
    )
    with sales_pg.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        c.execute(sa.text("SET TRANSACTION READ ONLY"))
        assert c.scalar(query) == 1
        apply(sales_pg, selected, bundle, key)
        assert c.scalar(query) == 1
    with sales_pg.connect() as c:
        assert c.scalar(query) == 0


def test_whole_transaction_budget_rolls_back_and_honors_stricter_policy(sales_pg, monkeypatch):
    from app.modules import database_retention as retention

    old_candidate(sales_pg)
    settings = {**retention.policy(), "statement_timeout_ms": 100, "lock_timeout_ms": 50}
    monkeypatch.setattr(retention, "policy", lambda: settings)
    with pytest.raises(sa.exc.DBAPIError):
        with retention.maintenance(sales_pg) as c:
            assert c.scalar(sa.text("SHOW statement_timeout")) == "100ms"
            assert c.scalar(sa.text("SHOW lock_timeout")) == "50ms"
            c.execute(
                sa.text(
                    "UPDATE dataset_storage SET keep_online=true WHERE version_id='product_daily-a'"
                )
            )
            c.execute(sa.text("SELECT pg_sleep(0.06)"))
            c.execute(sa.text("SELECT pg_sleep(0.06)"))
    with sales_pg.connect() as c:
        assert not c.scalar(
            sa.text("SELECT keep_online FROM dataset_storage WHERE version_id='product_daily-a'")
        )
        assert c.scalar(
            sa.text("SELECT pg_try_advisory_xact_lock(hashtext('project-source-sync'))")
        )


def test_offline_version_cannot_be_pinned_as_online(sales_pg, tmp_path):
    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "pin", key)
    drill(bundle, key)
    apply(sales_pg, selected, bundle, key)
    with pytest.raises(sa.exc.DBAPIError, match="RETENTION_PIN_REQUIRES_ONLINE"):
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "UPDATE dataset_storage SET keep_online=true WHERE version_id='product_daily-a'"
                )
            )


def test_pin_waiting_for_cleanup_cannot_succeed_on_offline_data(sales_pg, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.modules import database_retention as retention

    old_candidate(sales_pg)
    selected = plan(sales_pg)
    key = os.urandom(32)
    bundle = export(sales_pg, selected, tmp_path / "pin-race", key)
    drill(bundle, key)
    started = Event()
    finish = retention._finish
    pending = []

    def pin():
        with sales_pg.begin() as c:
            started.set()
            c.execute(
                sa.text(
                    "UPDATE dataset_storage SET keep_online=true WHERE version_id='product_daily-a'"
                )
            )

    with ThreadPoolExecutor(max_workers=1) as pool:

        def finishing(c, operation):
            pending.append(pool.submit(pin))
            assert started.wait(2)
            c.execute(sa.text("SELECT pg_sleep(0.03)"))
            assert not pending[0].done()
            finish(c, operation)

        monkeypatch.setattr(retention, "_finish", finishing)
        retention.apply(sales_pg, selected, bundle, key)
        with pytest.raises(sa.exc.DBAPIError, match="RETENTION_PIN_REQUIRES_ONLINE"):
            pending[0].result(timeout=2)
