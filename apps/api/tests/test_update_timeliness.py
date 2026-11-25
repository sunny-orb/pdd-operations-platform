"""固定日报核对已发布业务日；其余来源独立核对上游时间证据。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from app.modules.data_status.catalog import SOURCES
from app.modules.data_status.timeliness import merge_update_timing, update_timeliness

pytest_plugins = ["tests.sales_pg_support"]
TZ = ZoneInfo("Asia/Shanghai")
SOURCE = SOURCES[0]


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def checkpoint(updated="2026-11-09 09:30:00", checked="2026-11-10 10:30:01"):
    return {"web_updated_at": updated, "web_checked_at": at(checked)}


@pytest.mark.parametrize(
    "now,state",
    [
        ("2026-11-10 10:29:59", "waiting"),
        ("2026-11-10 10:30:00", "waiting"),
        ("2026-11-10 10:30:01", "delayed"),
    ],
)
def test_strict_one_hour_boundary(now, state):
    result = update_timeliness(SOURCE, checkpoint(checked=now), at(now))
    assert result["state"] == state
    assert result["expected_by"] == "2026-11-10 10:30:00"
    assert result["baseline_updated_at"] == "2026-11-09 09:30:00"


def test_late_clock_without_late_observation_waits_for_check():
    result = update_timeliness(
        SOURCE, checkpoint(checked="2026-11-10 10:29:59"), at("2026-11-10 12:00:00")
    )
    assert result["state"] == "awaiting_check"


@pytest.mark.parametrize(
    "updated",
    [None, "invalid", "2026-02-30 09:30:00", "2026-11-08 09:30:00", "2026-11-11 09:30:00"],
)
def test_missing_old_or_invalid_time_does_not_infer_delay(updated):
    assert (
        update_timeliness(SOURCE, checkpoint(updated), at("2026-11-10 12:00:00"))["state"]
        == "unknown"
    )


def test_today_updated_is_separate_from_platform_receipt_or_missing_records():
    p = checkpoint("2026-11-10 09:45:00")
    p.update(sync_status="failed", platform_snapshot={"history": {"missing_count": 20}})
    assert update_timeliness(SOURCE, p, at("2026-11-10 12:00:00"))["state"] == "updated"
    assert (
        update_timeliness(
            next(s for s in SOURCES if s.kind == "reference"), p, at("2026-11-10 12:00:00")
        )["state"]
        == "not_applicable"
    )


@pytest.mark.parametrize(
    "updated,now,expected",
    [
        ("2026-12-31 09:30:00", "2027-01-01 10:31:00", "2027-01-01 10:30:00"),
        ("2026-11-09 23:30:00", "2026-11-10 23:59:00", "2026-11-11 00:30:00"),
    ],
)
def test_calendar_projection_preserves_midnight_carry(updated, now, expected):
    assert update_timeliness(SOURCE, checkpoint(updated, now), at(now))["expected_by"] == expected


def test_merging_keeps_last_observed_update_per_day_and_bounds_cache():
    p = checkpoint("2026-11-09 09:30:00", "2026-11-09 10:00:00")
    history = merge_update_timing(p, "2026-11-10 09:45:00", at("2026-11-10 10:00:00"))
    p = {
        **checkpoint("2026-11-10 09:45:00", "2026-11-10 10:00:00"),
        "platform_snapshot": {"update_timing": history},
    }
    result = update_timeliness(SOURCE, p, at("2026-11-10 12:00:00"))
    assert result["state"] == "updated"
    assert result["expected_by"] == "2026-11-10 10:30:00"
    history = merge_update_timing(p, "2026-11-10 15:00:00", at("2026-11-10 15:10:00"))
    p["platform_snapshot"]["update_timing"] = history
    p.update(checkpoint("2026-11-10 15:00:00", "2026-11-10 15:10:00"))
    history = merge_update_timing(p, "2026-11-10 15:00:00", at("2026-11-11 16:01:00"))
    assert set(history["days"]) == {"2026-11-10"}
    p["platform_snapshot"]["update_timing"] = history
    p.update(checkpoint("2026-11-10 15:00:00", "2026-11-11 16:01:00"))
    assert update_timeliness(SOURCE, p, at("2026-11-11 16:01:00"))["state"] == "delayed"


def test_both_web_writers_and_snapshot_refresh_preserve_evidence(sales_pg):
    from app.modules.data_status.checkpoint_store import CheckpointStore
    from app.modules.data_status.repository import read_status, refresh_platform_snapshots
    from app.modules.data_status.web_observations import record_web_updates

    store = CheckpointStore(sales_pg)
    with store.locked() as c:
        with c.begin():
            store.write(
                c,
                "shop_daily",
                checked_at=at("2026-11-09 10:00:00"),
                check_status="succeeded",
                observed_fingerprint="first",
                web_updated_at="2026-11-09 09:30:00",
                web_checked_at=at("2026-11-09 10:00:00"),
            )
    record_web_updates(
        sales_pg,
        [
            {
                "tableId": "cAc3IVT4i3",
                "table": "ods_rpa_pdd_traffic_kanban_overview_f",
                "updated": "2026-11-10 09:45:00",
            }
        ],
        at("2026-11-10 10:00:00"),
    )
    with store.locked() as c:
        refresh_platform_snapshots(c)
        row = store.read(c)["shop_daily"]
        assert set(row["platform_snapshot"]["update_timing"]["days"]) == {
            "2026-11-09",
            "2026-11-10",
        }
        assert row["observed_fingerprint"] == "first"
        assert row["accepted_fingerprint"] is None
    before = row["platform_snapshot"]
    assert "timeliness" in read_status(sales_pg)["data"]["sources"][0]
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT platform_snapshot FROM source_checkpoints WHERE source_id='shop_daily'"
                )
            )
            == before
        )


def test_today_evidence_survives_a_later_page_regression():
    p = checkpoint("2026-11-09 09:30:00", "2026-11-09 10:00:00")
    saved = merge_update_timing(p, "2026-11-10 09:45:00", at("2026-11-10 10:00:00"))
    p = {
        **checkpoint("2026-11-10 09:45:00", "2026-11-10 10:00:00"),
        "platform_snapshot": {"update_timing": saved},
    }
    p["platform_snapshot"]["update_timing"] = merge_update_timing(
        p, "2026-11-09 09:30:00", at("2026-11-10 12:00:00")
    )
    p.update(checkpoint("2026-11-09 09:30:00", "2026-11-10 12:00:00"))
    result = update_timeliness(SOURCE, p, at("2026-11-10 12:00:00"))
    assert result["state"] == "updated"
    # 展示的核对时刻必须指向已证实今日更新的那次，而不是后来回退的网页。
    assert result["observed_at"] == "2026-11-10T10:00:00+08:00"


def test_new_day_without_yesterday_update_does_not_reuse_an_older_deadline():
    p = checkpoint("2026-11-09 23:30:00", "2026-11-11 00:31:00")
    assert update_timeliness(SOURCE, p, at("2026-11-11 00:31:00"))["state"] == "unknown"


@pytest.mark.parametrize(
    "checked", [None, datetime(2026, 11, 10, 10, 0), at("2026-11-11 10:00:00")]
)
def test_invalid_or_future_observation_is_not_a_time_basis(checked):
    p = {**checkpoint(), "web_checked_at": checked}
    assert update_timeliness(SOURCE, p, at("2026-11-10 12:00:00"))["state"] == "unknown"


def direct(day, present, stamp):
    return {
        "observation": {"upstream_day": {"date": day, "present": present}},
        "observed_at": at(stamp),
        "checked_at": at(stamp),
        "check_status": "succeeded",
        "observed_fingerprint": "unchanged",
    }


def daily_status(p, now):
    from app.modules.data_status.timeliness import source_timeliness

    return source_timeliness(SOURCE, p, at(now))


@pytest.mark.parametrize(
    "stamp,now,state",
    [
        ("10:29:59", "10:29:59", "waiting"),
        ("10:30:00", "10:30:00", "waiting"),
        ("10:30:00", "11:00:00", "awaiting_check"),
        ("10:30:01", "11:00:00", "delayed"),
    ],
)
def test_daily_deadline_requires_strictly_later_negative(stamp, now, state):
    p = direct("2026-11-23", False, f"2026-11-24 {stamp}")
    p["platform_snapshot"] = {
        "daily_arrivals": {"2026-11-22": {"first_seen_at": "2026-11-23T09:30:00+08:00"}}
    }
    result = daily_status(p, f"2026-11-24 {now}")
    assert result["state"] == state
    assert result["expected_by"] == "2026-11-24T10:30:00+08:00"


def test_no_baseline_from_web_clock_platform_or_wrong_business_day():
    p = direct("2026-11-23", False, "2026-11-24 12:00:00")
    p.update(checkpoint("2026-11-23 06:00:00", "2026-11-24 12:00:00"))
    p["platform_snapshot"] = {
        "history": {"missing_count": 350},
        "daily_arrivals": {"2026-11-22": {"first_seen_at": "2026-11-24T06:00:00+08:00"}},
    }
    result = daily_status(p, "2026-11-24 13:00:00")
    assert result["state"] == "unknown"
    assert "expected_by" not in result


@pytest.mark.parametrize("baseline,timing", [(True, "within_deadline"), (False, "unknown")])
def test_first_seen_at_deadline_is_available_with_separate_time_comparison(baseline, timing):
    p = direct("2026-11-23", True, "2026-11-24 10:30:00")
    arrivals = {"2026-11-23": {"first_seen_at": "2026-11-24T10:30:00+08:00"}}
    if baseline:
        arrivals["2026-11-22"] = {"first_seen_at": "2026-11-23T09:30:00+08:00"}
    p["platform_snapshot"] = {"daily_arrivals": arrivals}
    result = daily_status(p, "2026-11-24 12:00:00")
    assert (result["state"], result["arrival_timing"]) == ("updated", timing)


def test_daily_deadline_crossing_midnight_and_new_business_day():
    p = direct("2026-12-30", False, "2026-12-31 23:59:00")
    p["platform_snapshot"] = {
        "daily_arrivals": {"2026-12-29": {"first_seen_at": "2026-12-30T23:30:00+08:00"}}
    }
    result = daily_status(p, "2026-12-31 23:59:59")
    assert result["expected_by"] == "2027-01-01T00:30:00+08:00"
    assert result["state"] == "waiting"
    result = daily_status(p, "2027-01-01 00:31:00")
    assert result["state"] == "unknown"
    assert result["required_date"] == "2026-12-31"


@pytest.mark.parametrize(
    "prior_negative,timing",
    [("10:29:00", "first_seen_after_deadline"), ("10:31:00", "confirmed_late")],
)
def test_arrival_survives_later_checks_failure_web_registration_and_refresh(
    sales_pg, prior_negative, timing
):
    from app.modules.data_status.checkpoint_store import CheckpointStore
    from app.modules.data_status.repository import refresh_platform_snapshots
    from app.modules.data_status.web_observations import record_web_updates

    store = CheckpointStore(sales_pg)
    with store.locked() as c:
        for day, present, stamp in [
            ("2026-11-21", True, "2026-11-22 08:00"),
            ("2026-11-22", True, "2026-11-23 09:30"),
            ("2026-11-22", True, "2026-11-23 15:00"),
            ("2026-11-23", False, f"2026-11-24 {prior_negative}"),
            ("2026-11-23", True, "2026-11-24 11:01"),
            ("2026-11-23", True, "2026-11-24 11:25"),
        ]:
            with c.begin():
                store.write(c, SOURCE.id, **direct(day, present, stamp))
        with c.begin():
            store.write(c, SOURCE.id, check_status="failed", checked_at=at("2026-11-24 11:40"))
        refresh_platform_snapshots(c)
    record_web_updates(
        sales_pg,
        [
            dict(
                tableId="cAc3IVT4i3",
                table="ods_rpa_pdd_traffic_kanban_overview_f",
                updated="2026-11-24 08:15:00",
            )
        ],
        at("2026-11-24 12:00"),
    )
    with store.locked() as c:
        p = store.read(c)[SOURCE.id]
    assert set(p["platform_snapshot"]["daily_arrivals"]) == {"2026-11-22", "2026-11-23"}
    result = daily_status(p, "2026-11-24 12:00:00")
    assert result["state"] == "updated"
    assert result["arrival_timing"] == timing
    assert result["first_seen_at"] == "2026-11-24T11:01:00+08:00"
    assert result["baseline_first_seen_at"] == "2026-11-23T09:30:00+08:00"
    assert result["expected_by"] == "2026-11-24T10:30:00+08:00"
    assert p["accepted_fingerprint"] is None


def test_daily_evidence_rejects_wrong_date_failed_naive_and_future_checks():
    p = direct("2026-11-23", False, "2026-11-24 11:01:00")
    p["platform_snapshot"] = {
        "daily_arrivals": {"2026-11-22": {"first_seen_at": "2026-11-23T09:30:00+08:00"}}
    }
    for changes in (
        {"observation": {"upstream_day": {"date": "2026-11-22", "present": False}}},
        {"observation": {"upstream_day": {"date": "2026-11-23", "present": 0}}},
        {"check_status": "failed"},
        {"observed_at": at("2026-11-25 09:00:00")},
        {"observed_at": datetime(2026, 11, 24, 11, 1)},
    ):
        assert daily_status({**p, **changes}, "2026-11-24 12:00:00")["state"] == "awaiting_check"


def test_weekly_only_direct_check_survives_its_receipt_failure(sales_pg):
    from app.jobs.source_monitor import run_monitor
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.catalog import BY_ID
    from app.modules.data_status.timeliness import source_timeliness

    saturday = at("2026-11-21 09:30:00")
    sunday = at("2026-11-22 17:00:00")
    code = "product_regions"

    def upstream(day, present):
        return {
            code: lambda: {"revision": "stable", "upstream_day": {"date": day, "present": present}}
        }

    run_monitor(
        sales_pg,
        upstream("2026-11-20", True),
        lambda ids, c: {"product_regions": True},
        now=saturday,
    )
    run_scheduled_sources(sales_pg, upstream("2026-11-21", False), lambda ids, c: {}, now=sunday)
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["checked_at"] == saturday  # 独立对账失败保留普通检查结果。
        result = source_timeliness(BY_ID[code], row, sunday)
        assert result["state"] == "delayed"
        assert result["observed_at"] == sunday.isoformat()


def test_concurrent_writers_and_rollback_preserve_earliest_time_and_snapshot_keys(sales_pg):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.modules.data_status.checkpoint_store import CheckpointStore
    from app.modules.data_status.repository import refresh_platform_snapshots

    store = CheckpointStore(sales_pg)

    def check(stamp):
        return direct("2026-11-23", True, f"2026-11-24 {stamp}")

    with sales_pg.begin() as c:
        store.write(c, SOURCE.id, **check("11:00"))
        c.exec_driver_sql(
            """UPDATE source_checkpoints SET platform_snapshot=platform_snapshot
                            || '{"history":{"sample":1},"gap_repair":{"sample":2}}'::jsonb"""
        )
    started = Event()

    def replay_earlier():
        with sales_pg.begin() as c:
            started.set()
            store.write(c, SOURCE.id, **check("09:30"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        with sales_pg.begin() as c:
            store.write(c, SOURCE.id, **check("10:00"))
            replay = pool.submit(replay_earlier)
            assert started.wait(timeout=5)
        replay.result(timeout=5)
    with sales_pg.connect() as c:
        with pytest.raises(RuntimeError), c.begin():
            store.write(c, SOURCE.id, **check("08:00"))
            raise RuntimeError("roll back evidence")
        refresh_platform_snapshots(c)
        row = store.read(c)[SOURCE.id]
    assert row["platform_snapshot"]["history"] == {"sample": 1}
    assert row["platform_snapshot"]["gap_repair"] == {"sample": 2}
    assert daily_status(row, "2026-11-24 12:00")["first_seen_at"] == "2026-11-24T09:30:00+08:00"
