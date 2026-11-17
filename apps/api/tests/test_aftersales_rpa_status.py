"""RPA上游只读监测与统一状态：真实PG验证，检查不冒充本地接收。"""

from datetime import UTC, datetime, timedelta

import psycopg
import sqlalchemy as sa

from tests.test_aftersales_rpa import delivery

pytest_plugins = ["tests.sales_pg_support"]


def test_rpa_probe_detects_history_edits_deletes_and_empty_table(sales_pg):
    from app.connectors.aftersales_rpa_status import read_update_status

    with sales_pg.begin() as c:
        c.exec_driver_sql("""CREATE TABLE public.aftersales_reports_rpa (
            id bigint PRIMARY KEY, start_date date, end_date date, time_granularity text,
            class text, json_data jsonb, updated_at timestamptz)""")
        c.exec_driver_sql("""INSERT INTO public.aftersales_reports_rpa VALUES
            (1,'2025-10-18','2025-11-16','weekly','按退货原因','{"数量":1}',
             '2026-11-16 00:55:39+00'),
            (2,'2026-10-18','2026-11-16','weekly','销售已发货','{"金额":100}',
             '2026-11-16 00:54:00+00')""")
    url = sales_pg.url.set(drivername="postgresql").render_as_string(hide_password=False)

    def connect():
        return psycopg.connect(url)

    first = read_update_status(connect)
    assert first["source_updated"] == "2026-11-16T00:55:39+00:00"
    assert first["date"] == "2026-11-16"
    assert first["count"] == 2 and first["period_count"] == 2
    assert "数量" not in str(first)
    with sales_pg.begin() as c:
        c.exec_driver_sql("UPDATE public.aftersales_reports_rpa SET json_data='{}' WHERE id=1")
    edited = read_update_status(connect)
    assert edited["source_updated"] == first["source_updated"]
    assert edited["revision"] != first["revision"]
    with sales_pg.begin() as c:
        c.exec_driver_sql("DELETE FROM public.aftersales_reports_rpa WHERE id=2")
    assert read_update_status(connect)["count"] == 1
    with sales_pg.begin() as c:
        c.exec_driver_sql("DELETE FROM public.aftersales_reports_rpa")
    empty = read_update_status(connect)
    assert empty["count"] == 0 and empty["source_updated"] is None and empty["date"] is None


def test_receiving_rpa_retries_failed_revision_and_accepts_once(sales_pg, tmp_path):
    from app.jobs.source_monitor import run_monitor
    from app.modules.aftersales.rpa_publisher import sync_rpa

    start = datetime.now(UTC)
    source = {"basis": "rpa_table", "revision": "v1", "count": len(delivery())}
    calls = []

    def synchronize(groups, _):
        assert groups == ("aftersales_rpa",)
        calls.append(source["revision"])
        if len(calls) == 1:
            return {"aftersales_rpa": False}
        sync_rpa(sales_pg, delivery, tmp_path)
        return {"aftersales_rpa": True}

    def check(minutes):
        return run_monitor(
            sales_pg,
            {"aftersales_rpa": lambda: source},
            synchronize,
            now=start + timedelta(minutes=minutes),
            receiving_sources=("aftersales_rpa",),
        )

    assert check(0)["status"] == "failed"
    assert check(4)["checked"] == 0
    assert check(5)["status"] == "checked"
    assert check(21)["groups"] == []
    assert calls == ["v1", "v1"]
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT accepted_at FROM source_checkpoints")) is not None
        assert c.scalar(sa.text("SELECT count(*) FROM current_datasets")) == 4


def test_rpa_monitor_is_periodic_read_only_and_preserves_real_receipt(sales_pg, tmp_path):
    from app.jobs.source_monitor import run_monitor
    from app.modules.aftersales.rpa_publisher import sync_rpa
    from app.modules.data_status.repository import read_status
    from app.modules.data_status.schedule_policy import MONITOR_ONLY_SOURCES

    assert "aftersales_rpa" in MONITOR_ONLY_SOURCES
    sync_rpa(sales_pg, delivery, tmp_path)
    stamp = datetime.now(UTC)
    observation = dict(
        source_updated="2026-11-16T00:55:39+00:00",
        count=6,
        date="2026-11-16",
        period_count=1,
        revision="original",
        basis="rpa_table",
    )

    def forbid_sync(groups, c):
        raise AssertionError("只监测RPA不得触发接收或其他任务")

    def check(at, probe=lambda: observation):
        return run_monitor(sales_pg, {"aftersales_rpa": probe}, forbid_sync, now=at)

    def row():
        return next(
            r for r in read_status(sales_pg)["data"]["sources"] if r["id"] == "aftersales_rpa"
        )

    check(stamp)
    first = row()
    assert first["upstream"]["state"] == "observed"
    assert first["platform_state"] == "available"
    assert first["source_url"] is None
    assert first["history"]["state"] == first["timeliness"]["state"] == "not_applicable"
    assert {d["date"] for d in first["datasets"]} == {"2026-11-16"}
    assert all(d["last_success_at"] for d in first["datasets"])
    assert first["upstream"]["accepted_at"] is None
    assert check(stamp + timedelta(minutes=1))["checked"] == 0
    check(stamp + timedelta(minutes=16))
    assert row()["upstream"]["state"] == "unchanged"
    observation = {
        **observation,
        "revision": "revised",
        "source_updated": "2026-11-17T00:00:00+00:00",
    }
    check(stamp + timedelta(minutes=32))
    changed = row()
    assert changed["upstream"]["state"] == "changed"
    assert changed["datasets"] == first["datasets"]
    assert changed["upstream"]["accepted_at"] is None

    def failure():
        raise RuntimeError("private credentials must not appear")

    check(stamp + timedelta(minutes=48), failure)
    failed = row()
    assert failed["upstream"]["state"] == "failed"
    assert failed["upstream"]["observation"] == changed["upstream"]["observation"]
    assert failed["platform_state"] == "available"
    assert failed["upstream"]["error_code"] == "CHECK_FAILED"
    assert check(stamp + timedelta(minutes=52))["checked"] == 0
    assert check(stamp + timedelta(minutes=53))["checked"] == 1
    status = read_status(sales_pg)["data"]["sources"]
    assert [r["id"] for r in status if "aftersales" in r["pages"]] == ["aftersales_rpa"]
    assert not {"jst_returns", "jst_sales", "jst_master"} & {r["id"] for r in status}
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs")) == 1


def test_failed_rpa_check_does_not_block_other_source_receipt(sales_pg):
    from app.jobs.source_monitor import run_monitor
    from app.modules.data_status.repository import read_status

    def failed_rpa():
        raise RuntimeError("private connection detail")

    def receive(groups, c):
        assert groups == ("shop_daily",)
        return {"shop_daily": True}

    result = run_monitor(
        sales_pg,
        {"aftersales_rpa": failed_rpa, "shop_daily": lambda: {"revision": "fresh"}},
        receive,
    )
    assert result["status"] == "partial" and result["failed"] == 1
    rows = {r["id"]: r for r in read_status(sales_pg)["data"]["sources"]}
    assert rows["aftersales_rpa"]["upstream"]["state"] == "failed"
    assert rows["aftersales_rpa"]["upstream"]["accepted_at"] is None
    assert rows["shop_daily"]["upstream"]["state"] == "unchanged"
    assert rows["shop_daily"]["upstream"]["accepted_at"] is not None
