"""每日上游更新时效；不以经营记录/同步结果代替网页时间证据。"""

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
