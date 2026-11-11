from datetime import date


def test_missing_keys_preserve_shop_day_scope_and_zero_presence():
    from app.modules.record_coverage import missing_shop_days

    a, b = date(2026, 11, 7), date(2026, 11, 9)
    assert missing_shop_days({("a", a), ("a", b)}, a, b, ["a", "b"]) == [
        ("a", date(2026, 11, 8)),
        ("b", a),
        ("b", date(2026, 11, 8)),
        ("b", b),
    ]


def test_record_window_always_includes_yesterday_independent_of_sync_schedule():
    from datetime import datetime

    from app.modules.data_status.history_coverage import coverage_window

    assert coverage_window("shop_daily", datetime.fromisoformat("2026-11-10T08:00:00+08:00")) == (
        date(2026, 10, 11),
        date(2026, 11, 9),
        date(2026, 11, 9),
    )
    assert coverage_window("shop_daily", datetime.fromisoformat("2026-11-10T09:00:00+08:00"))[
        -1
    ] == date(2026, 11, 9)
