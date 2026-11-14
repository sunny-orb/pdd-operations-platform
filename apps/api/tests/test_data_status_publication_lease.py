"""长发布事务的续租不可见时，状态只读任务行与来源执行锁，不等待写事务。"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
import sqlalchemy as sa
from test_sales_publication_heartbeat import candidate, store

from app.modules.data_status.repository import read_status
from app.modules.sales import storage

pytest_plugins = ["sales_pg_support"]


def region_status(engine):
    return next(
        row for row in read_status(engine)["data"]["sources"] if row["id"] == "product_regions"
    )


@pytest.fixture
def status_reader(sales_pg):
    engine = sa.create_engine(sales_pg.url, hide_parameters=True)

    @sa.event.listens_for(engine, "connect")
    def bounded_reader(connection, _record):
        connection.execute("SET statement_timeout='1s'")
        connection.commit()

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("phase", ["shared_write", "validation"])
def test_status_stays_running_during_live_long_transaction(
    sales_pg, status_reader, tmp_path, monkeypatch, phase
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    publisher.publish_candidate(candidate())
    monkeypatch.setattr(storage, "LEASE_SECONDS", 2)
    monkeypatch.setattr(storage, "HEARTBEAT_SECONDS", 0.2)
    reached, resume = Event(), Event()
    delayed = 0

    def delay():
        nonlocal delayed
        time.sleep(0.4)
        delayed += 1
        if delayed == 6:
            reached.set()
            assert resume.wait(5)

    def slow_query(conn, cursor, statement, params, context, many):
        if phase == "shared_write" and statement.startswith(
            "INSERT INTO public.shared_product_region_records"
        ):
            delay()

    class SlowRow(dict):
        def items(self):
            delay()
            return super().items()

    data = candidate("101.25")
    if phase == "validation":
        data = replace(data, rows=tuple(SlowRow(row) for row in data.rows))
    sa.event.listen(sales_pg, "before_cursor_execute", slow_query)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(publisher.publish_candidate, data)
            try:
                assert reached.wait(8)
                with sales_pg.connect() as c:
                    assert c.scalar(
                        sa.text(
                            "SELECT lease_expires_at < clock_timestamp() FROM task_runs "
                            "WHERE status='running'"
                        )
                    )
                started = time.monotonic()
                observed = region_status(status_reader)
                assert time.monotonic() - started < 1
                assert observed["platform_state"] == "syncing"
                assert observed["datasets"][0]["attempt_status"] == "running"
                assert observed["datasets"][0]["error_code"] is None
            finally:
                resume.set()
            assert pending.result(timeout=8).status == "succeeded"
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", slow_query)
    assert region_status(status_reader)["datasets"][0]["attempt_status"] == "succeeded"


@pytest.mark.parametrize("evidence", ["both", "advisory_only", "row_only", "other_source"])
def test_expired_task_requires_its_own_transaction_and_source_lock(
    sales_pg, status_reader, evidence
):
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO task_runs
            (id,job_code,request_ref,lock_key,status,queued_at,started_at,worker_ref,lease_expires_at)
            VALUES ('writer','sync_product_regions','test','product_regions','running',
            clock_timestamp(),clock_timestamp(),'worker',clock_timestamp()+interval '0.2 seconds')
        """)
        )
    with sales_pg.connect() as writer:
        if evidence != "row_only":
            key = "m3-shop_daily" if evidence == "other_source" else "m3-product_regions"
            writer.execute(sa.text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": key})
            writer.commit()
        if evidence != "advisory_only":
            writer.execute(
                sa.text(
                    "UPDATE task_runs SET lease_expires_at="
                    "clock_timestamp()+interval '120 seconds' "
                    "WHERE id='writer'"
                )
            )
        time.sleep(0.25)
        try:
            state = region_status(status_reader)["datasets"][0]
            assert state["attempt_status"] == ("running" if evidence == "both" else "abandoned")
        finally:
            writer.rollback()
            writer.execute(sa.text("SELECT pg_advisory_unlock_all()"))
            writer.commit()
    # 崩溃回滚后只剩过期的已提交任务，不能凭旧xmax或旧来源锁继续显示运行。
    state = region_status(status_reader)["datasets"][0]
    assert state["attempt_status"] == "abandoned"
    assert state["error_code"] == "WORKER_LOST"


def test_status_retries_snapshot_if_publisher_commits_during_lock_probe(
    sales_pg, status_reader, tmp_path
):
    published = store(sales_pg, tmp_path).publish_candidate(candidate())
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO task_runs
            (id,job_code,request_ref,lock_key,status,queued_at,started_at,worker_ref,lease_expires_at)
            VALUES ('writer','sync_product_regions','test','product_regions','running',
            clock_timestamp(),clock_timestamp(),'worker',clock_timestamp()+interval '0.2 seconds')
        """)
        )
    with sales_pg.connect() as writer:
        writer.execute(sa.text("SELECT pg_advisory_lock(hashtext('m3-product_regions'))"))
        writer.commit()
        writer.execute(
            sa.text("""UPDATE task_runs SET status='succeeded',
            result_version_id=:v,finished_at=clock_timestamp(),lease_expires_at=NULL
            WHERE id='writer'"""),
            {"v": published.version_id},
        )
        time.sleep(0.25)
        snapshots, probes = [], []

        def finish_during_probe(conn, cursor, statement, params, context, many):
            if "SELECT DISTINCT ON(job_code)" in statement:
                snapshots.append(1)
            if "JOIN pg_catalog.pg_locks tx" in statement and not probes:
                probes.append(1)
                writer.commit()
                writer.execute(sa.text("SELECT pg_advisory_unlock_all()"))
                writer.commit()

        sa.event.listen(status_reader, "before_cursor_execute", finish_during_probe)
        try:
            observed = region_status(status_reader)
        finally:
            sa.event.remove(status_reader, "before_cursor_execute", finish_during_probe)
        assert len(snapshots) == 2 and len(probes) == 1
        assert observed["platform_state"] == "available"
        assert observed["datasets"][0]["attempt_status"] == "succeeded"


def test_task_transaction_cannot_borrow_source_lock_from_another_session(sales_pg, status_reader):
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO task_runs
            (id,job_code,request_ref,lock_key,status,queued_at,started_at,worker_ref,lease_expires_at)
            VALUES ('writer','sync_product_regions','test','product_regions','running',
            clock_timestamp(),clock_timestamp(),'worker',clock_timestamp()+interval '0.2 seconds')
        """)
        )
    with sales_pg.connect() as writer, sales_pg.connect() as other:
        other.execute(sa.text("SELECT pg_advisory_lock(hashtext('m3-product_regions'))"))
        other.commit()
        writer.execute(
            sa.text("""UPDATE task_runs
            SET lease_expires_at=clock_timestamp()+interval '120 seconds' WHERE id='writer'""")
        )
        time.sleep(0.25)
        try:
            assert region_status(status_reader)["datasets"][0]["attempt_status"] == "abandoned"
        finally:
            writer.rollback()
            other.execute(sa.text("SELECT pg_advisory_unlock_all()"))
            other.commit()


def test_lock_evidence_catalog_is_readable_without_monitor_privileges(sales_pg):
    with sales_pg.begin() as c:
        c.execute(sa.text("SET LOCAL ROLE pdd_app"))
        assert c.scalar(sa.text("SELECT count(*) FROM pg_catalog.pg_locks")) > 0
