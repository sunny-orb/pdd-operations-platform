"""单日接收保留历史、对账与失败恢复；来源合成，发布使用真实PostgreSQL。"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from test_sales_extended_sources import sample

from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import SHOPS
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def scope(end="2026-11-12"):
    return ReadWindow(date(2026, 2, 1), date.fromisoformat(end), tuple(SHOPS))


def row(key, day, amount="100.01"):
    return sample("product_regions", id=key, dt=day, actual_shipped_amount=Decimal(amount))


def publish(engine, root, rows, window=None):
    window = window or scope()
    store = ShopDailyStore(
        engine, archive_root=root, approved=window, spec=SPECS["product_regions"]
    )
    return store.publish_candidate(normalize_source("product_regions", rows, window))


def incremental(engine, root, rows, *, full=False, windows=None):
    from app.modules.sales.product_regions_incremental import receive_product_regions

    store = ShopDailyStore(
        engine, archive_root=root, approved=scope(), spec=SPECS["product_regions"]
    )

    def read(window):
        if windows is not None:
            windows.append(window)
        return normalize_source("product_regions", rows, window)

    return store.run(lambda hb: receive_product_regions(store, read, heartbeat=hb, full=full))


def values(engine, version):
    with engine.connect() as c:
        return dict(
            c.execute(
                sa.text(
                    "SELECT source_row_key,actual_shipped_amount FROM read_product_region_records "
                    "WHERE dataset_version_id=:v"
                ),
                {"v": version},
            ).all()
        )


@pytest.mark.parametrize("shared", [False, True])
def test_incremental_equals_full_and_old_version_stays_readable(
    sales_pg, tmp_path, monkeypatch, shared
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(shared).lower())
    old = [row("old", "2026-08-24"), row("recent", "2026-11-11")]
    first = publish(sales_pg, tmp_path, old, scope("2026-11-11"))
    delta = [row("new", "2026-11-12", "10")]
    windows = []
    result = incremental(sales_pg, tmp_path, delta, windows=windows)
    assert windows == [ReadWindow(date(2026, 11, 12), date(2026, 11, 12), tuple(SHOPS))]
    assert values(sales_pg, result.version_id) == {
        "old": Decimal("100.01"),
        "recent": Decimal("100.01"),
        "new": Decimal("10"),
    }
    assert values(sales_pg, first.version_id) == {
        "old": Decimal("100.01"),
        "recent": Decimal("100.01"),
    }
    full = publish(sales_pg, tmp_path, [*old, *delta])
    assert full.version_id == result.version_id and full.reused
    from app.modules.sales.archive_replay import restore_candidate

    assert (
        restore_candidate(tmp_path / str(result.version_id)).rows
        == normalize_source("product_regions", [*old, *delta], scope()).rows
    )
    assert incremental(sales_pg, tmp_path, delta).version_id == result.version_id


@pytest.mark.parametrize(
    "changed",
    [
        [],
        [row("new", "2026-11-12")],
        [row("recent", "2026-10-21")],
        [row("old", "2026-10-21"), row("recent", "2026-11-12")],
    ],
)
def test_missing_or_moved_keys_preserve_previous(sales_pg, tmp_path, changed):
    first = publish(sales_pg, tmp_path, [row("old", "2026-08-24"), row("recent", "2026-11-12")])
    with pytest.raises(SalesError):
        incremental(sales_pg, tmp_path, changed)
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='product_regions'"
                )
            )
            == first.version_id
        )
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")) == 1
    assert len(values(sales_pg, first.version_id)) == 2


def test_bootstrap_and_long_outage_expand_window(sales_pg, tmp_path):
    windows = []
    incremental(sales_pg, tmp_path, [row("old", "2026-08-24")], windows=windows)
    assert windows == [scope()]
    # 独立旧基线模拟停机：不能从最新事实最大日期猜已经验证到哪一天。
    old = publish(sales_pg, tmp_path, [row("old", "2026-08-24")], scope("2026-09-01"))
    windows.clear()
    result = incremental(sales_pg, tmp_path, [row("gap", "2026-09-15")], windows=windows)
    assert windows[0].start == date(2026, 9, 2)
    assert values(sales_pg, old.version_id) == {"old": Decimal("100.01")}
    assert "gap" in values(sales_pg, result.version_id)


def test_full_reconciliation_corrects_old_dates_but_rejects_unexplained_removal(sales_pg, tmp_path):
    first = publish(sales_pg, tmp_path, [row("old", "2026-08-24"), row("recent", "2026-11-12")])
    windows = []
    corrected = [row("old", "2026-08-24", "20"), row("recent", "2026-11-12")]
    result = incremental(sales_pg, tmp_path, corrected, full=True, windows=windows)
    assert windows == [scope()]
    assert values(sales_pg, result.version_id)["old"] == Decimal("20")
    assert values(sales_pg, first.version_id)["old"] == Decimal("100.01")
    with pytest.raises(SalesError):
        incremental(sales_pg, tmp_path, corrected[1:], full=True)


def test_regions_weekly_only_retries_until_success(sales_pg):
    from app.jobs.source_schedule import run_scheduled_sources

    sunday = datetime.fromisoformat("2026-11-15T17:00:00+08:00")
    calls = []

    def sync(codes, connection):
        calls.extend(codes)
        return dict.fromkeys(codes, len(calls) >= 2)

    def run(now):
        return run_scheduled_sources(
            sales_pg, {"product_regions": lambda: {"revision": "same"}}, sync, now=now
        )

    assert run(sunday - timedelta(minutes=1))["checked"] == 0
    assert run(sunday)["failed"] == 1
    assert run(sunday + timedelta(minutes=4))["checked"] == 0
    assert run(sunday + timedelta(minutes=5))["failed"] == 0
    assert run(sunday + timedelta(minutes=10))["checked"] == 0
    assert calls == ["product_regions", "product_regions"]
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT status FROM source_reconciliations WHERE source_id='product_regions'"
                )
            )
            == "succeeded"
        )


def test_project_entry_uses_window_and_weekly_entry_uses_full(sales_pg, tmp_path, monkeypatch):
    from app.connectors import sales_sources
    from app.jobs.project_runtime import build_steps
    from app.modules.sales import source_policy
    from tests.test_project_sync_runtime import paths_for

    monkeypatch.setattr(source_policy, "approved_window", lambda code=None: scope())
    monkeypatch.setattr("app.modules.sales.storage.approved_window", lambda code=None: scope())
    paths = paths_for(tmp_path)
    root = paths.ingestion_root / "product_regions"
    original = [row("old", "2026-08-24"), row("recent", "2026-11-12")]
    first = publish(sales_pg, root, original)
    windows = []

    class Reader:
        def __init__(self, **kwargs):
            self.hb = kwargs["heartbeat"]

        def close(self):
            pass

        def read_stable(self, code, window, *, allow_empty=False):
            assert code == "product_regions"
            windows.append(window)
            self.hb()
            rows = [
                r for r in original if window.start <= date.fromisoformat(r["dt"]) <= window.end
            ]
            return normalize_source(code, rows, window)

    monkeypatch.setattr(sales_sources, "SalesSourceReader", Reader)
    result = build_steps(sales_pg, paths, lambda: "test-only")["product_regions"]()
    assert result["version_id"] == first.version_id
    assert windows[-1].start == date(2026, 11, 12)
    original[0] = row("old", "2026-08-24", "15")
    result = build_steps(sales_pg, paths, lambda: "test-only", reconcile_product_regions=True)[
        "product_regions"
    ]()
    assert windows[-1] == scope()
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT job_code FROM task_runs WHERE id=:id"), {"id": result["run_id"]}
            )
            == "sync_product_regions_reconciliation"
        )
    assert values(sales_pg, result["version_id"])["old"] == Decimal("15")


def test_baseline_changed_before_publish_does_not_replace_new_head(sales_pg, tmp_path):
    from app.modules.sales.product_regions_incremental import receive_product_regions

    initial = [row("old", "2026-08-24"), row("recent", "2026-11-12")]
    first = publish(sales_pg, tmp_path, initial)
    newer = publish(sales_pg, tmp_path, [initial[0], row("recent", "2026-11-12", "42")])
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "UPDATE current_datasets SET version_id=:v WHERE dataset_code='product_regions'"
            ),
            {"v": first.version_id},
        )
    store = ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=scope(), spec=SPECS["product_regions"]
    )

    def receive(hb):
        candidate = receive_product_regions(
            store,
            lambda w: normalize_source("product_regions", [row("recent", "2026-11-12", "99")], w),
            heartbeat=hb,
        )
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "UPDATE current_datasets SET version_id=:v WHERE dataset_code='product_regions'"
                ),
                {"v": newer.version_id},
            )
        return candidate

    with pytest.raises(SalesError, match="INCREMENTAL_BASELINE_CHANGED"):
        store.run(receive)
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='product_regions'"
                )
            )
            == newer.version_id
        )
    assert values(sales_pg, first.version_id)["recent"] == Decimal("100.01")


def test_source_failure_and_corrupt_baseline_leave_history(sales_pg, tmp_path):
    from app.modules.sales.product_regions_incremental import receive_product_regions

    first = publish(sales_pg, tmp_path, [row("old", "2026-08-24"), row("recent", "2026-11-12")])
    store = ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=scope(), spec=SPECS["product_regions"]
    )

    def broken(window):
        raise SalesError("SOURCE_CHANGED_DURING_READ")

    with pytest.raises(SalesError, match="SOURCE_CHANGED_DURING_READ"):
        store.run(lambda hb: receive_product_regions(store, broken, heartbeat=hb))
    assert (
        incremental(sales_pg, tmp_path, [row("recent", "2026-11-12")]).version_id
        == first.version_id
    )
    (tmp_path / first.version_id / "input.ndjson").write_text("corrupt")
    with pytest.raises(ValueError, match="ARCHIVE_CHECKSUM"):
        incremental(sales_pg, tmp_path, [row("recent", "2026-11-12")])
    assert len(values(sales_pg, first.version_id)) == 2


def test_read_budget_does_not_limit_retained_history(sales_pg, tmp_path, monkeypatch):
    from app.modules.sales import product_regions_incremental as incremental_module

    first = publish(sales_pg, tmp_path, [row("old", "2026-08-24"), row("recent", "2026-11-12")])
    monkeypatch.setattr(incremental_module, "PRODUCT_REGION_SOURCE_MAX_ROWS", 2, raising=False)
    incoming = [row("recent", "2026-11-12"), row("new", "2026-11-12")]
    result = incremental(sales_pg, tmp_path, incoming)
    assert len(values(sales_pg, result.version_id)) == 3
    assert len(values(sales_pg, first.version_id)) == 2
    assert incremental(sales_pg, tmp_path, incoming).version_id == result.version_id
    with pytest.raises(SalesError, match="SOURCE_ROW_LIMIT"):
        incremental(sales_pg, tmp_path, [*incoming, row("too-many", "2026-11-12")])
    assert incremental(sales_pg, tmp_path, incoming).version_id == result.version_id


def test_ordinary_status_continues_after_weekly_failure(sales_pg):
    from app.jobs.project_sync import Schedule
    from app.modules.data_status.repository import read_status
    from tests.test_published_versions import publish_pointer

    publish_pointer(sales_pg, "product_regions", "available")
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""
            INSERT INTO task_runs(id,job_code,request_ref,lock_key,status,queued_at,started_at,
                                 finished_at,worker_ref,error_code,result_version_id)
            VALUES ('before','sync_product_regions','test','test','failed','2026-11-08 15:00+08',
                    '2026-11-08 15:00+08','2026-11-08 15:01+08','test','SOURCE_FAILED',NULL),
                   ('after','sync_product_regions','test','test','succeeded','2026-11-09 10:00+08',
                    '2026-11-09 10:00+08','2026-11-09 10:01+08','test',NULL,
                    'product_regions-available'),
                   ('weekly','sync_product_regions_reconciliation','test','test','failed',
                    '2026-11-09 11:00+08','2026-11-09 11:00+08','2026-11-09 11:01+08',
                    'test','SOURCE_FAILED',NULL)
        """)
        )
        c.execute(
            sa.text("""
            INSERT INTO source_reconciliations(source_id,cycle_at,status,last_attempt_at,
                  last_finished_at,legacy_task_id,error_code)
            VALUES ('product_regions','2026-11-08 17:00+08','failed','2026-11-09 11:00+08',
                    '2026-11-09 11:01+08','before','SYNC_FAILED')
        """)
        )
    result = read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))["data"]
    source = next(r for r in result["sources"] if r["id"] == "product_regions")
    assert source["datasets"][0]["attempt_status"] == "succeeded"
    assert source["datasets"][0]["last_attempt_at"] == datetime.fromisoformat(
        "2026-11-09T10:00:00+08:00"
    )
    assert source["reconciliation"]["status"] == "failed"

    with sales_pg.begin() as c:
        c.execute(
            sa.text("""
            UPDATE source_reconciliations SET status='succeeded',error_code=NULL,
                last_success_at=last_finished_at WHERE source_id='product_regions';
            INSERT INTO task_runs(id,job_code,request_ref,lock_key,status,queued_at,started_at,
                                 finished_at,worker_ref,error_code)
            VALUES ('later-failure','sync_product_regions','test','test','failed',
                    '2026-11-10 10:00+08','2026-11-10 10:00+08','2026-11-10 10:01+08',
                    'test','SOURCE_FAILED')
        """)
        )
    result = read_status(sales_pg, source_schedule=Schedule(source_schedule_enabled=True))["data"]
    source = next(r for r in result["sources"] if r["id"] == "product_regions")
    assert source["datasets"][0]["attempt_status"] == "failed"
    assert source["reconciliation"]["status"] == "succeeded"


def test_archive_replay_renews_lease_without_changing_candidate(sales_pg, tmp_path):
    from app.modules.sales.archive_replay import restore_candidate

    first = publish(sales_pg, tmp_path, [row("old", "2026-08-24"), row("recent", "2026-11-12")])
    beats = []
    candidate = restore_candidate(tmp_path / first.version_id, heartbeat=lambda: beats.append(1))
    assert beats
    assert {r["source_row_key"] for r in candidate.rows} == {"old", "recent"}


def test_archive_changed_during_replay_is_rejected(sales_pg, tmp_path, monkeypatch):
    from pathlib import Path

    from app.modules.sales.archive_replay import restore_candidate
    from app.modules.sales.normalization import canonical

    first = publish(sales_pg, tmp_path, [row("old", "2026-08-24")])
    target = tmp_path / first.version_id / "input.ndjson"
    original_open = Path.open
    reads = 0

    def open_with_change(path, mode="r", *args, **kwargs):
        nonlocal reads
        if path == target and mode == "rb":
            reads += 1
            if reads == 2:
                with original_open(path, "a") as stream:
                    stream.write(canonical(row("added", "2026-11-12")) + "\n")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_change)
    with pytest.raises(SalesError, match="ARCHIVE_CHECKSUM_MISMATCH"):
        restore_candidate(target.parent)
    assert len(values(sales_pg, first.version_id)) == 1
