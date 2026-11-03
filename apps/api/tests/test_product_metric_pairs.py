"""七主卡沿线上期间比较，健康度保持交集；缺值与真实零不混淆。"""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.product_board.query import BoardQuery, overview
from app.modules.product_board.source import normalize
from tests.test_product_board_review import END, Repo, row

pytest_plugins = ["tests.sales_pg_support"]


def fact(day, lid="x", **patch):
    return dict(row(day, lid), favorites=3, **patch)


def test_seven_cards_use_displayed_current_total_for_both_comparisons():
    current = fact(END, product_revenue=D(100), buyers=10, visitors=100)
    prior = fact(END - timedelta(days=2), product_revenue=D(50), buyers=5, visitors=100)
    year = fact(date(2025, 10, 26), product_revenue=D(25), buyers=5, visitors=50)
    result = overview(
        Repo([current, prior, year, fact(END, "new")]), BoardQuery(END - timedelta(days=1), END)
    )
    m = result["metrics"]
    assert m["revenue"]["value"] == 200
    assert m["revenue"]["yoy"] == 7 and m["revenue"]["mom"] == 3
    assert m["revenue"]["yoy_comparison"]["value"] == 200
    assert m["revenue"]["mom_comparison"]["previous"] == 50
    assert m["revenue"]["observation_note"] == (
        "本期有些日期没有店铺或商品经营记录，按已有记录统计。"
    )
    assert m["aov"]["yoy"] == 1 and m["aov"]["mom"] == 0
    assert m["conversion"]["yoy"] == 0 and m["conversion"]["mom"] == 1
    assert all(m[k]["yoy"] == 1 for k in ("units", "favorites"))
    assert m["visitors"]["yoy"] == 3 and m["buyers"]["yoy"] == 3
    assert result["health"]["metrics"]["revenue"]["yoy"] == 3
    assert result["windows"][0]["metrics"]["revenue"]["change"] is None


def test_ratios_use_period_sums_and_never_average_daily_rates():
    rows = [
        fact(END, product_revenue=D(100), buyers=10, visitors=100),
        fact(END - timedelta(days=1), product_revenue=D(300), buyers=10, visitors=300),
        fact(date(2025, 10, 26), product_revenue=D(100), buyers=10, visitors=100),
        fact(date(2025, 10, 25), product_revenue=D(100), buyers=10, visitors=100),
    ]
    m = overview(Repo(rows), BoardQuery(END - timedelta(days=1), END))["metrics"]
    assert m["conversion"]["yoy"] == D("-.5")
    assert m["aov"]["yoy"] == 1


def test_mom_keeps_previous_only_products_and_filters_each_period():
    rows = [
        fact(END, "new"),
        fact(END - timedelta(days=1), "old", product_revenue=D(50)),
        fact(END - timedelta(days=1), "other", product_revenue=D(900)),
    ]
    profiles = {
        "new": dict(category="衣", operator="甲", level="A"),
        "old": dict(category="衣", operator="甲", level="A"),
        "other": dict(category="衣", operator="乙", level="A"),
    }
    q = BoardQuery(END, END, shop="s", category="衣", operator="甲", level="A")
    m = overview(Repo(rows, profiles), q)["metrics"]["revenue"]
    assert m["previous_period"] == 50 and m["mom"] == 1
    single = overview(Repo(rows, profiles), BoardQuery(END, END, products=("new",)))
    assert single["metrics"]["revenue"]["mom"] is None


def test_missing_calendar_date_does_not_extend_mom_window():
    rows = [fact(END), fact(END - timedelta(days=3), product_revenue=D(500))]
    result = overview(Repo(rows), BoardQuery(END, END))
    m = result["metrics"]["revenue"]
    assert m["previous_period"] is None and m["mom"] is None
    assert m["mom_comparison"]["message"] == "前一期暂无经营记录"


def test_unreceived_history_is_not_reported_as_a_complete_prior_period():
    repo = Repo([fact(END), fact(END - timedelta(days=3))])
    repo.versions = {"product_daily": {"period_start": END - timedelta(days=3)}}
    m = overview(repo, BoardQuery(END - timedelta(days=2), END))["metrics"]["revenue"]
    assert m["mom"] is None
    assert m["mom_comparison"]["reason"] == "history_not_received"


def test_yoy_only_uses_exact_current_listing_dates_not_other_days_of_same_listing():
    rows = [
        fact(END, "x"), fact(END - timedelta(days=1), "new"),
        fact(date(2025, 10, 26), "x", product_revenue=D(50)),
        fact(date(2025, 10, 25), "x", product_revenue=D(900)),
    ]
    m = overview(Repo(rows), BoardQuery(END - timedelta(days=1), END))["metrics"]["revenue"]
    assert m["value"] == 200 and m["previous"] == 50 and m["yoy"] == 3


def test_missing_field_only_blocks_its_metric_without_silently_dropping_pairs():
    rows = [fact(END), fact(date(2025, 10, 26), visitors=None)]
    m = overview(Repo(rows), BoardQuery(END, END))["metrics"]
    assert m["revenue"]["yoy"] == 0
    assert m["visitors"]["yoy"] is None
    assert m["visitors"]["yoy_comparison"]["reason"] == "invalid_values"
    assert m["conversion"]["yoy_comparison"]["reason"] == "invalid_values"


def test_partial_current_values_have_a_specific_explanation():
    m = overview(Repo([fact(END), fact(END, "new", visitors=None)]), BoardQuery(END, END))[
        "metrics"
    ]
    assert m["revenue"]["observation_note"] is None
    assert m["visitors"]["observation_note"] == "商品访客数：部分记录没有可用数值，仅合计有效部分。"
    assert m["visitors"]["yoy"] is None


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
    assert metric["yoy_comparison"]["reason"] == "no_records"


def test_mom_pairs_calendar_days_across_year_boundary():
    repo = Repo([fact(date(2027, 1, 1)), fact(date(2026, 12, 30), product_revenue=D(50))])
    repo.start, repo.end = date(2026, 12, 1), date(2027, 1, 1)
    m = overview(repo, BoardQuery(date(2026, 12, 31), repo.end))["metrics"]["revenue"]
    assert m["mom"] == 1


def test_leap_day_stays_in_current_total_but_never_maps_to_feb_28():
    repo = Repo(
        [
            fact(date(2028, 2, 29)),
            fact(date(2028, 2, 28)),
            fact(date(2027, 2, 28), product_revenue=D(50)),
        ]
    )
    repo.start, repo.end = date(2028, 2, 1), date(2028, 2, 29)
    m = overview(repo, BoardQuery(date(2028, 2, 28), repo.end))["metrics"]["revenue"]
    assert m["value"] == 200 and m["yoy"] == 3
    assert m["yoy_comparison"]["current_rows"] == 2
    assert m["yoy_comparison"]["previous_rows"] == 1


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
        incomplete = overview(repo, BoardQuery(repo.start, end))["metrics"]["revenue"]
        assert incomplete["mom"] is None
        assert incomplete["mom_comparison"]["reason"] == "no_records"
    data["product_daily"] = normalize("product_daily", records, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        assert repo.start == end - timedelta(days=59)
        metric = overview(repo, BoardQuery(end - timedelta(days=30), end))["metrics"]["revenue"]
        assert metric["mom"] == 0 and metric["previous_period"] == 3100
        assert metric["mom_comparison"]["previous_rows"] == 31
        longest = overview(repo, BoardQuery(repo.start, end))["metrics"]["revenue"]
        assert longest["mom"] == 0 and longest["mom_comparison"]["previous_rows"] == 60
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
