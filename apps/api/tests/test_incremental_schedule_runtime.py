"""真实PG调度状态驱动增量/全范围意图，来源网络由可控读端替代。"""

from datetime import timedelta

import sqlalchemy as sa

from tests.test_project_sync_runtime import paths_for
from tests.test_source_schedule import at

pytest_plugins = ["tests.sales_pg_support"]


def test_runtime_passes_only_this_cycles_full_sources(sales_pg, tmp_path, monkeypatch):
    from app.jobs import source_monitor_runtime as runtime

    revision = ["one"]
    calls = []

    class Reader:
        def read(self, code):
            return {"revision": revision[0]}

    monkeypatch.setattr(runtime.SourceProbes, "read", lambda self, code: {"revision": revision[0]})
    monkeypatch.setattr(runtime, "pending_processing", lambda c: [])

    def execute(*args, **kwargs):
        calls.append((kwargs["only"], kwargs.get("reconcile_sources", ())))
        return {"datasets": {x: {"status": "succeeded"} for x in kwargs["only"]}}

    monkeypatch.setattr(runtime, "execute_project", execute)

    def run(now, force=False):
        return runtime.execute_source_schedule(
            sales_pg,
            paths=paths_for(tmp_path),
            warehouse_password=lambda: "unused",
            warehouse_reader=Reader(),
            source_ids=("shop_daily", "product_regions"),
            now=now,
            force=force,
        )

    run(at("08:30"))
    assert calls == [(("shop_daily",), ())]
    sunday = at("17:00") + timedelta(days=4)
    run(sunday)
    assert calls[-1] == (("shop_daily", "product_regions"), ("shop_daily", "product_regions"))
    count = len(calls)
    run(sunday + timedelta(minutes=5))
    assert len(calls) == count
    revision[0] = "two"
    run(sunday + timedelta(days=1), force=True)
    assert calls[-1] == (("shop_daily", "product_regions"), ())
    with sales_pg.connect() as c:
        assert set(c.execute(sa.text("SELECT status FROM source_reconciliations")).scalars()) == {
            "succeeded"
        }


def test_failed_weekly_retries_only_failed_source_in_full_mode(sales_pg, tmp_path, monkeypatch):
    from app.jobs import source_monitor_runtime as runtime

    calls = []

    class Reader:
        def read(self, code):
            return {"revision": "stable"}

    monkeypatch.setattr(runtime.SourceProbes, "read", lambda self, code: {"revision": "stable"})
    monkeypatch.setattr(runtime, "pending_processing", lambda c: [])

    def execute(*args, **kwargs):
        calls.append((kwargs["only"], kwargs.get("reconcile_sources", ())))
        return {
            "datasets": {
                x: {"status": "failed" if len(calls) == 1 and x == "shop_daily" else "succeeded"}
                for x in kwargs["only"]
            }
        }

    monkeypatch.setattr(runtime, "execute_project", execute)
    sunday = at("17:00") + timedelta(days=4)

    def run(now):
        return runtime.execute_source_schedule(
            sales_pg,
            paths=paths_for(tmp_path),
            warehouse_password=lambda: "unused",
            warehouse_reader=Reader(),
            source_ids=("shop_daily", "product_regions"),
            now=now,
        )

    assert run(sunday)["failed"] == 1
    count = len(calls)
    assert run(sunday + timedelta(seconds=299))["checked"] == 0
    assert len(calls) == count
    run(sunday + timedelta(seconds=300))
    assert calls[-1] == (("shop_daily",), ("shop_daily",))
    run(sunday + timedelta(seconds=600))
    assert len(calls) == count + 1
