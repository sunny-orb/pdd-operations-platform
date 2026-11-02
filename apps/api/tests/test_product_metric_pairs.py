"""七主卡比较只使用共同商品日；全量本期、其他区块与评分范围保持独立。"""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.product_board.query import BoardQuery, overview
from app.modules.product_board.source import normalize
from tests.test_product_board_review import END, Repo, row

pytest_plugins = ["tests.sales_pg_support"]


def fact(day, lid="x", **patch):
    return dict(row(day, lid), favorites=3, **patch)


def test_seven_cards_pair_both_periods_and_keep_full_current_totals():
    current = fact(END, product_revenue=D(100), buyers=10, visitors=100)
    prior = fact(END - timedelta(days=2), product_revenue=D(50), buyers=5, visitors=100)
    year = fact(date(2025, 10, 26), product_revenue=D(25), buyers=5, visitors=50)
    result = overview(
        Repo([current, prior, year, fact(END, "new")]), BoardQuery(END - timedelta(days=1), END)
    )
    m = result["metrics"]
    assert m["revenue"]["value"] == 200
    assert m["revenue"]["yoy"] == 3 and m["revenue"]["mom"] == 1
    assert m["revenue"]["yoy_comparison"]["value"] == 100
    assert m["revenue"]["mom_comparison"]["previous"] == 50
    assert m["aov"]["yoy"] == 1 and m["aov"]["mom"] == 0
    assert m["conversion"]["yoy"] == 0 and m["conversion"]["mom"] == 1
    assert all(m[k]["yoy"] == 0 for k in ("units", "favorites"))
    assert m["visitors"]["yoy"] == 1 and m["buyers"]["yoy"] == 1
    assert result["windows"][0]["metrics"]["revenue"]["change"] is None


def test_ratios_use_matched_sums_and_never_average_daily_rates():
    rows = [
        fact(END, product_revenue=D(100), buyers=10, visitors=100),
        fact(END - timedelta(days=1), product_revenue=D(300), buyers=10, visitors=300),
        fact(date(2025, 10, 26), product_revenue=D(100), buyers=10, visitors=100),
        fact(date(2025, 10, 25), product_revenue=D(100), buyers=10, visitors=100),
    ]
    m = overview(Repo(rows), BoardQuery(END - timedelta(days=1), END))["metrics"]
    assert m["conversion"]["yoy"] == D("-.5")
    assert m["aov"]["yoy"] == 1


def test_missing_field_only_blocks_its_metric_without_silently_dropping_pairs():
    rows = [fact(END), fact(date(2025, 10, 26), visitors=None)]
    m = overview(Repo(rows), BoardQuery(END, END))["metrics"]
    assert m["revenue"]["yoy"] == 0
    assert m["visitors"]["yoy"] is None
    assert m["visitors"]["yoy_comparison"]["reason"] == "invalid_values"
    assert m["conversion"]["yoy_comparison"]["reason"] == "invalid_values"


@pytest.mark.parametrize("base,want,reason", [(0, None, "zero_baseline"), (100, D(-1), None)])
def test_real_zero_is_distinct_from_no_matching_records(base, want, reason):
    rows = [fact(END, product_revenue=D(0)), fact(date(2025, 10, 26), product_revenue=D(base))]
    metric = overview(Repo(rows), BoardQuery(END, END))["metrics"]["revenue"]
    assert metric["yoy"] == want
    assert metric["previous"] == base
    assert metric["yoy_comparison"]["reason"] == reason


def test_pair_identity_includes_shop_and_no_match_never_means_zero():
    rows = [fact(END), fact(date(2025, 10, 26), "other-shop", source_product_id="x")]
    metric = overview(Repo(rows), BoardQuery(END, END))["metrics"]["revenue"]
    assert metric["yoy"] is None and metric["previous"] is None
    assert metric["yoy_comparison"]["reason"] == "no_pairs"


def test_mom_pairs_calendar_days_across_year_boundary():
    repo = Repo([fact(date(2027, 1, 1)), fact(date(2026, 12, 30), product_revenue=D(50))])
    repo.start, repo.end = date(2026, 12, 1), date(2027, 1, 1)
    m = overview(repo, BoardQuery(date(2026, 12, 31), repo.end))["metrics"]["revenue"]
    assert m["mom"] == 1


def test_leap_day_without_same_calendar_day_is_excluded_from_both_sides():
    repo = Repo(
        [
            fact(date(2028, 2, 29)),
            fact(date(2028, 2, 28)),
            fact(date(2027, 2, 28), product_revenue=D(50)),
        ]
    )
    repo.start, repo.end = date(2028, 2, 1), date(2028, 2, 29)
    m = overview(repo, BoardQuery(date(2028, 2, 28), repo.end))["metrics"]["revenue"]
    assert m["value"] == 200 and m["yoy"] == 1
    assert m["yoy_comparison"]["matched_product_days"] == 1


def test_zero_ratio_denominator_does_not_turn_into_zero_rate():
    m = overview(Repo([fact(END, visitors=0), fact(date(2025, 10, 26))]), BoardQuery(END, END))[
        "metrics"
    ]
    assert m["revenue"]["yoy"] == 0
    assert m["conversion"]["yoy"] is None
    assert m["conversion"]["yoy_comparison"]["reason"] == "zero_denominator"


def test_history_is_available_for_31_day_mom_without_expanding_page_or_score_range(
    sales_pg, tmp_path
):
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.storage import ProductBoardStore
    from app.modules.product_scoring.service import build_bundle
    from tests.test_product_board_storage import candidate

    end, data = candidate(duplicate_profile=False)
    records = [
        dict(
            z_id=i + 1,
            shop_id="663599807",
            spu_id="123",
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount="100",
            product_uv=100,
            transaction_users=10,
            transaction_orders=10,
            transaction_products=10,
            favorite_product_users=3,
        )
        for i in range(120)
    ]
    data["product_daily"] = normalize("product_daily", records[:60], end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        before = build_bundle(repo)
        before_meta = repo.meta()
    data["product_daily"] = normalize("product_daily", records, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        assert repo.start == end - timedelta(days=59)
        metric = overview(repo, BoardQuery(end - timedelta(days=30), end))["metrics"]["revenue"]
        assert metric["mom"] == 0 and metric["previous_period"] == 3100
        assert metric["mom_comparison"]["matched_product_days"] == 31
        longest = overview(repo, BoardQuery(repo.start, end))["metrics"]["revenue"]
        assert longest["mom"] == 0 and longest["mom_comparison"]["matched_product_days"] == 60
        after = build_bundle(repo)
        assert after["start"] == end - timedelta(days=59)
        assert after["scores"] == before["scores"]
        assert after["calibration"] == before["calibration"]
        assert repo.meta()["observed_rows"] == before_meta["observed_rows"]
        assert repo.versions["product_daily"]["period_start"] == end - timedelta(days=119)
        assert repo.versions["product_promotion"]["period_start"] == end - timedelta(days=59)


@pytest.mark.parametrize(
    "start,end,year_start,year_end,past_start,past_end",
    [
        ("2026-10-16", "2026-10-31", "2025-10-16", "2025-10-31", "2026-09-30", "2026-10-15"),
        ("2027-01-01", "2027-01-02", "2026-01-01", "2026-01-02", "2026-12-30", "2026-12-31"),
        ("2028-02-29", "2028-03-01", "2027-03-01", "2027-03-01", "2028-02-27", "2028-02-28"),
        ("2028-02-29", "2028-02-29", None, None, "2028-02-28", "2028-02-28"),
    ],
)
def test_comparison_periods_explain_calendar_windows_without_claiming_records_exist(
    start, end, year_start, year_end, past_start, past_end
):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    repo = Repo([])
    repo.start, repo.end = start, end
    result = overview(repo, BoardQuery(start, end))
    periods = result["comparison_periods"]
    assert periods["current"] == dict(start=start, end=end)
    assert periods["yoy"] == (
        dict(start=date.fromisoformat(year_start), end=date.fromisoformat(year_end))
        if year_start
        else None
    )
    assert periods["mom"] == dict(
        start=date.fromisoformat(past_start), end=date.fromisoformat(past_end)
    )
    assert result["metrics"]["revenue"]["yoy"] is None
    assert result["metrics"]["revenue"]["mom"] is None
