"""接收后的慢阶段仍需续租；真实PG时钟、事务和旧发布保护。"""

import time
from dataclasses import replace
from decimal import Decimal

import pytest
import sqlalchemy as sa
from test_product_regions_incremental import row, scope, values

from app.modules import fact_content
from app.modules.sales import storage
from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def candidate(amount="100.01"):
    return normalize_source(
        "product_regions",
        [row(f"slow-{i}", "2026-10-20", amount) for i in range(8)],
        scope(),
    )


def store(engine, root):
    return ShopDailyStore(
        engine, archive_root=root, approved=scope(), spec=SPECS["product_regions"]
    )


@pytest.mark.parametrize("stage", ["archive", "blocks", "shared_write", "validation"])
def test_slow_publish_stages_renew_lease_without_changing_history(
    sales_pg, tmp_path, monkeypatch, stage
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    first = publisher.publish_candidate(candidate())
    # 压缩等待时间，仍由真实PG clock_timestamp决定过期；配置的120/30秒不改动。
    monkeypatch.setattr(storage, "LEASE_SECONDS", 1)
    monkeypatch.setattr(storage, "HEARTBEAT_SECONDS", 0.1)
    next_candidate = candidate("101.25")
    delays, renewals = [], []

    def delay():
        delays.append(time.monotonic())
        time.sleep(0.2)

    original_canonical = storage.canonical
    original_value = fact_content._value

    def canonical(value):
        if stage == "archive" and isinstance(value, dict) and "dt" in value:
            delay()
        return original_canonical(value)

    def value(raw, kind):
        if stage == "blocks" and isinstance(raw, str) and raw.startswith("slow-"):
            delay()
        return original_value(raw, kind)

    class SlowRow(dict):
        def items(self):
            delay()
            return super().items()

    if stage == "validation":
        next_candidate = replace(
            next_candidate, rows=tuple(SlowRow(r) for r in next_candidate.rows)
        )
    monkeypatch.setattr(storage, "canonical", canonical)
    monkeypatch.setattr(fact_content, "_value", value)

    def query(conn, cursor, statement, params, context, many):
        if stage == "shared_write" and statement.startswith(
            "INSERT INTO public.shared_product_region_records"
        ):
            delay()
        if statement.startswith("UPDATE public.task_runs SET lease_expires_at="):
            renewals.append(time.monotonic())

    sa.event.listen(sales_pg, "before_cursor_execute", query)
    started = time.monotonic()
    try:
        result = publisher.publish_candidate(next_candidate)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", query)
    assert time.monotonic() - started > 1
    assert len(delays) >= 6
    assert len(renewals) >= 6
    assert result.status == "succeeded"
    assert set(values(sales_pg, first.version_id).values()) == {Decimal("100.01")}
    assert set(values(sales_pg, result.version_id).values()) == {Decimal("101.25")}
    files = set(tmp_path.rglob("*"))
    assert publisher.publish_candidate(candidate("101.25")).version_id == result.version_id
    assert set(tmp_path.rglob("*")) == files


def assert_previous(engine, previous):
    with engine.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='product_regions'"
                )
            )
            == previous.version_id
        )
        assert (
            c.scalar(sa.text("SELECT count(*) FROM dataset_versions WHERE state='published'")) == 1
        )
    assert set(values(engine, previous.version_id).values()) == {Decimal("100.01")}


def test_single_operation_beyond_lease_is_not_revived(sales_pg, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    first = publisher.publish_candidate(candidate())
    monkeypatch.setattr(storage, "LEASE_SECONDS", 1)
    monkeypatch.setattr(storage, "HEARTBEAT_SECONDS", 0.1)

    def block(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO public.shared_product_region_records"):
            time.sleep(1.1)

    sa.event.listen(sales_pg, "before_cursor_execute", block)
    try:
        with pytest.raises(SalesError, match="TASK_LEASE_LOST"):
            publisher.publish_candidate(candidate("101.25"))
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", block)
    assert_previous(sales_pg, first)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM fact_content_manifests")) == 1
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 8
        assert (
            c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE error_code='TASK_LEASE_LOST'"))
            == 1
        )


def test_expiry_at_final_success_update_rolls_back_publication(sales_pg, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    first = publisher.publish_candidate(candidate())
    monkeypatch.setattr(storage, "LEASE_SECONDS", 1)
    monkeypatch.setattr(storage, "HEARTBEAT_SECONDS", 0.1)

    def expire(conn, cursor, statement, params, context, many):
        if statement.startswith("UPDATE public.task_runs") and "result_version_id" in statement:
            time.sleep(1.1)

    sa.event.listen(sales_pg, "before_cursor_execute", expire)
    try:
        with pytest.raises(SalesError, match="TASK_LEASE_LOST"):
            publisher.publish_candidate(candidate("101.25"))
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", expire)
    assert_previous(sales_pg, first)


@pytest.mark.parametrize("change", ["expired", "worker"])
def test_post_receive_lease_or_owner_change_rejects_publication(
    sales_pg, tmp_path, monkeypatch, change
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    first = publisher.publish_candidate(candidate())
    archive = publisher._archive

    def archive_then_change(*args):
        result = archive(*args)
        with sales_pg.begin() as c:
            update = (
                "lease_expires_at=clock_timestamp()-interval '1 second'"
                if change == "expired"
                else "worker_ref='replacement-worker'"
            )
            c.execute(sa.text(f"UPDATE task_runs SET {update} WHERE status='running'"))
        return result

    monkeypatch.setattr(publisher, "_archive", archive_then_change)
    with pytest.raises(SalesError, match="TASK_LEASE_LOST"):
        publisher.publish_candidate(candidate("101.25"))
    assert_previous(sales_pg, first)
    with sales_pg.connect() as c:
        if change == "worker":
            assert (
                c.scalar(
                    sa.text("SELECT status FROM task_runs WHERE worker_ref='replacement-worker'")
                )
                == "running"
            )


@pytest.mark.parametrize("stage", ["shared_write", "current_switch"])
def test_failure_rolls_back_content_or_publication_with_heartbeat(
    sales_pg, tmp_path, monkeypatch, stage
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    first = publisher.publish_candidate(candidate())
    monkeypatch.setattr(storage, "HEARTBEAT_SECONDS", 0)
    prefix = (
        "INSERT INTO public.fact_content_members"
        if stage == "shared_write"
        else "INSERT INTO public.current_datasets"
    )

    def fail(conn, cursor, statement, params, context, many):
        if statement.startswith(prefix):
            raise RuntimeError("synthetic publication failure")

    sa.event.listen(sales_pg, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic publication failure"):
            publisher.publish_candidate(candidate("101.25"))
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail)
    assert_previous(sales_pg, first)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")) == 1
        if stage == "shared_write":
            assert c.scalar(sa.text("SELECT count(*) FROM fact_content_manifests")) == 1
            assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 8


def test_heartbeat_sql_is_throttled_during_fast_large_candidate(sales_pg, tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    publisher = store(sales_pg, tmp_path)
    data = normalize_source(
        "product_regions",
        [row(f"throttled-{i}", "2026-10-20") for i in range(1001)],
        scope(),
    )
    renewals = []

    def query(conn, cursor, statement, params, context, many):
        if statement.startswith("UPDATE public.task_runs SET lease_expires_at="):
            renewals.append(1)

    sa.event.listen(sales_pg, "before_cursor_execute", query)
    try:
        assert publisher.publish_candidate(data).status == "succeeded"
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", query)
    assert 1 < len(renewals) < 20
