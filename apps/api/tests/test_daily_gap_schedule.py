"""补缺只消费自己的每日检查点，普通接收和周对账沿用各自水位。"""

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from app.jobs.source_schedule import run_scheduled_sources
from tests.test_source_schedule import at

pytest_plugins = ["tests.sales_pg_support"]


def checkpoint(engine, code="shop_daily"):
    with engine.connect() as c:
        return (
            c.execute(
                sa.text("SELECT * FROM source_checkpoints WHERE source_id=:code"), {"code": code}
            )
            .mappings()
            .one()
        )


def test_gap_receipt_independent_of_unchanged_fingerprint_and_combines_with_changes(sales_pg):
    revision = ["first"]
    gap = date(2026, 10, 31)
    plans, calls = [], []

    def plan(c, code, today):
        plans.append((code, today))
        return (gap,)

    def sync(codes, c, **kwargs):
        calls.append((codes, kwargs))
        return dict.fromkeys(codes, True)

    def run(now, **kwargs):
        return run_scheduled_sources(
            sales_pg,
            {"shop_daily": lambda: {"revision": revision[0]}},
            sync,
            now=now,
            gap_candidates=plan,
            **kwargs,
        )

    run(at("15:10"))
    prior = checkpoint(sales_pg)
    run(at("16:11"))
    assert calls[-1] == (
        ("shop_daily",),
        {"repair_dates": {"shop_daily": (gap,)}, "repair_only": {"shop_daily"}},
    )
    assert checkpoint(sales_pg)["accepted_at"] == prior["accepted_at"]
    run(at("16:12"))
    run(at("18:12"), force=True)
    assert plans == [("shop_daily", at("16:11").date())]
    assert calls[-1] == (("shop_daily",), {})
    revision[0] = "second"
    next_day = at("16:20") + timedelta(days=1)
    run(next_day)
    assert calls[-1] == (
        ("shop_daily",),
        {"repair_dates": {"shop_daily": (gap,)}, "repair_only": set()},
    )
    assert checkpoint(sales_pg)["accepted_at"] == next_day
    assert len(plans) == 2


def test_gap_scan_and_receipt_failures_retry_across_invocations_with_other_checks_intact(sales_pg):
    gap = date(2026, 11, 2)
    attempts, received = [], []
    results = iter([False, True])

    def plan(c, code, day):
        attempts.append(code)
        if len(attempts) == 1:
            raise ValueError("source unavailable")
        return (gap,)

    def sync(codes, c, **kwargs):
        if kwargs:
            received.extend(codes)
            return {"shop_daily": next(results)}
        return dict.fromkeys(codes, True)

    def run(t):
        return run_scheduled_sources(
            sales_pg,
            {"shop_daily": lambda: {"revision": "v1"}},
            sync,
            now=at(t),
            gap_candidates=plan,
        )

    run("15:10")
    prior = checkpoint(sales_pg)
    assert run("16:10")["failed"] == 1
    assert checkpoint(sales_pg)["check_status"] == "succeeded"
    assert run("16:14")["checked"] == 0
    assert run("16:15")["failed"] == 1
    assert run("16:19")["checked"] == 0
    assert run("16:20")["failed"] == 0
    run("16:21")
    assert len(attempts) == 3 and received == ["shop_daily", "shop_daily"]
    assert checkpoint(sales_pg)["accepted_at"] == prior["accepted_at"]
    assert checkpoint(sales_pg)["platform_snapshot"]["gap_repair"]["status"] == "succeeded"


def test_interrupted_gap_check_recovers_under_project_lock(sales_pg):
    gap = date(2026, 11, 2)
    attempts = []

    def plan(c, code, day):
        attempts.append(day)
        if len(attempts) == 1:
            raise KeyboardInterrupt
        return (gap,)

    def sync(codes, c, **kwargs):
        return dict.fromkeys(codes, True)

    def run(t):
        return run_scheduled_sources(
            sales_pg,
            {"shop_daily": lambda: {"revision": "v1"}},
            sync,
            now=at(t),
            gap_candidates=plan,
        )

    run("15:10")
    with pytest.raises(KeyboardInterrupt):
        run("16:10")
    assert checkpoint(sales_pg)["platform_snapshot"]["gap_repair"]["status"] == "running"
    run("16:14")
    assert len(attempts) == 1
    assert run("16:15")["sources"] == ["shop_daily"]
    assert len(attempts) == 2


def test_legacy_region_keeps_its_ordinary_watermark_and_weekly_owns_full_read(sales_pg):
    gap = date(2026, 11, 2)
    calls, plans = [], []

    def plan(c, code, day):
        plans.append(day)
        return (gap,)

    def sync(codes, c, **kwargs):
        calls.append((codes, kwargs))
        return dict.fromkeys(codes, True)

    def run(now, **kwargs):
        return run_scheduled_sources(
            sales_pg,
            {"product_regions": lambda: {"revision": "v1"}},
            sync,
            now=now,
            gap_candidates=plan,
            **kwargs,
        )

    run(at("15:10"), force=True)
    prior = checkpoint(sales_pg, "product_regions")
    run(at("16:11"))
    assert calls[-1] == (
        ("product_regions",),
        {"repair_dates": {"product_regions": (gap,)}, "repair_only": {"product_regions"}},
    )
    assert checkpoint(sales_pg, "product_regions")["accepted_at"] == prior["accepted_at"]
    sunday = at("17:00") + timedelta(days=4)
    run(sunday)
    assert len(plans) == 1
    assert calls[-1] == (("product_regions",), {})
    assert (
        checkpoint(sales_pg, "product_regions")["platform_snapshot"]["gap_repair"]["status"]
        == "succeeded"
    )
    assert run(sunday + timedelta(minutes=1))["checked"] == 0


def test_unchanged_next_day_schedule_publishes_late_shop_day_with_current_tail(
    sales_pg, tmp_path, monkeypatch
):
    """真实计划/覆盖/项目编排/发布；只替代远端数仓和元信息读取。"""
    from decimal import Decimal

    from test_sales_window_incremental import publish, row, scope, values

    from app.connectors import shop_daily
    from app.jobs import source_monitor_runtime as runtime
    from app.modules.data_status import gap_candidates
    from app.modules.sales import source_policy, storage
    from tests.test_project_sync_runtime import paths_for

    code = "shop_daily"
    paths = paths_for(tmp_path)
    rows = [
        row(code, "old", "2026-11-01", "1"),
        row(code, "official", "2026-11-02", "2"),
        row(code, "latest", "2026-11-03", "3"),
    ]
    first = publish(sales_pg, paths.ingestion_root / code, code, rows, "2026-11-03")
    approved = [scope(code, "2026-11-03")]
    for module in (source_policy, storage, gap_candidates):
        monkeypatch.setattr(module, "approved_window", lambda code=None: approved[0])
    scans = []

    def scan(window):
        scans.append((window.start, window.end))
        return [
            item for item in rows if window.start <= date.fromisoformat(item["dt"]) <= window.end
        ]

    reader = shop_daily.ShopDailyReader.for_test(scan)
    reader.close = lambda: None
    monkeypatch.setattr(shop_daily, "ShopDailyReader", lambda **kwargs: reader)

    def present(self, source, gaps):
        assert source == code
        matched = set()
        for item in rows:
            day = date.fromisoformat(item["dt"])
            if item["shop_id"] in gaps.get(day, ()):
                matched.add(day)
        return tuple(sorted(matched))

    monkeypatch.setattr(runtime.SourceProbes, "available_gap_days", present)

    class Metadata:
        def read(self, source):
            return {"revision": "stable"}

    def run(now):
        return runtime.execute_source_schedule(
            sales_pg,
            paths=paths,
            warehouse_password=lambda: "fixture",
            warehouse_reader=Metadata(),
            source_ids=(code,),
            now=now,
        )

    run(at("15:10"))
    initial_accepted = checkpoint(sales_pg)["accepted_at"]
    scans.clear()
    approved[0] = scope(code, "2026-11-04")
    rows[1] = row(code, "official", "2026-11-02", "8")
    rows += [
        row(
            code,
            "outdoor",
            "2026-11-02",
            "9",
            shop_id="799193231",
            shop_name=source_policy.SHOPS["799193231"][1],
        ),
        row(code, "current", "2026-11-04", "4"),
    ]
    assert run(at("16:20") + timedelta(days=1))["sources"] == [code]
    assert (
        scans
        == [(date(2026, 11, 2), date(2026, 11, 2))] * 2
        + [(date(2026, 11, 4), date(2026, 11, 4))] * 2
    )
    with sales_pg.connect() as c:
        head = c.scalar(
            sa.text("SELECT version_id FROM current_datasets WHERE dataset_code='shop_daily'")
        )
    assert values(sales_pg, code, head) == {
        "old": Decimal(1),
        "official": Decimal(8),
        "latest": Decimal(3),
        "outdoor": Decimal(9),
        "current": Decimal(4),
    }
    assert values(sales_pg, code, first.version_id) == {
        "old": Decimal(1),
        "official": Decimal(2),
        "latest": Decimal(3),
    }
    current = checkpoint(sales_pg)
    assert current["accepted_at"] == initial_accepted
    assert current["platform_snapshot"]["gap_repair"]["status"] == "succeeded"
    history = current["platform_snapshot"]["history"]
    assert history["inputs"][code] == head
    outdoor_gaps = next(
        group["dates"] for group in history["groups"] if group["shop_id"] == "spihw"
    )
    assert "2026-11-02" not in outdoor_gaps


def test_unfinished_daily_gap_retries_after_midnight():
    from app.jobs.gap_repair import due_gap_day

    failed = at("23:58")
    state = {
        "platform_snapshot": {
            "gap_repair": {
                "day": failed.date().isoformat(),
                "status": "failed",
                "at": failed.isoformat(),
            }
        }
    }
    assert due_gap_day("shop_daily", state, failed + timedelta(minutes=4), ("16:10",)) is None
    assert (
        due_gap_day("shop_daily", state, failed + timedelta(minutes=5), ("16:10",)) == failed.date()
    )
