"""周期趋势按固定日历两期独立统计，不沿用同款全集合门禁。"""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.product_board.query import BoardQuery, overview
from tests.test_product_board_review import END, Repo
from tests.test_product_metric_pairs import fact

pytest_plugins = ["tests.sales_pg_support"]


def window(repo, start=None, **filters):
    return overview(repo, BoardQuery(start or repo.end, repo.end, **filters))["windows"]


def test_windows_keep_previous_only_products_and_ignore_selected_start():
    repo = Repo(
        [
            fact(END, "new", product_revenue=D(200)),
            fact(END - timedelta(days=2), "recent", product_revenue=D(300)),
            fact(END - timedelta(days=8), "old", product_revenue=D(100)),
        ]
    )
    recent = window(repo)[0]
    assert recent["metrics"]["revenue"]["value"] == 500
    assert recent["metrics"]["revenue"]["previous"] == 100
    assert recent["metrics"]["revenue"]["change"] == 4
    assert window(repo, END - timedelta(days=30)) == window(repo)


def test_windows_apply_all_filters_to_both_periods():
    repo = Repo(
        [
            fact(END, "new", product_revenue=D(200)),
            fact(END - timedelta(days=8), "old"),
            fact(END - timedelta(days=8), "excluded", product_revenue=D(900)),
        ],
        {
            lid: dict(operator=op, category="衣", level="A")
            for lid, op in (("new", "甲"), ("old", "甲"), ("excluded", "乙"))
        },
    )
    metrics = window(repo, shop="s", operator="甲", category="衣", level="A")[0]["metrics"]
    assert metrics["revenue"]["change"] == 1
    assert window(repo, products=("new",))[0]["metrics"]["revenue"]["reason"] == "no_records"
    assert window(repo, keyword="old")[0]["metrics"]["revenue"]["value"] is None
    assert window(repo, operator="无人")[0]["metrics"]["revenue"]["previous"] is None


def test_missing_days_do_not_shift_calendar_or_supply_zero():
    repo = Repo([fact(END), fact(END - timedelta(days=14), product_revenue=D(900))])
    result = window(repo)[0]
    assert result["start"] == END - timedelta(days=6)
    assert result["previous_start"] == END - timedelta(days=13)
    m = result["metrics"]["revenue"]
    assert m["value"] == 100 and m["previous"] is None and m["change"] is None
    assert m["message"] == "前7日暂无经营记录"


@pytest.mark.parametrize(
    "amount,reason,want",
    [(0, "zero_baseline", None), (None, "invalid_values", None), (D(100), None, D(-1))],
)
def test_real_zero_and_invalid_values_are_distinct(amount, reason, want):
    repo = Repo(
        [fact(END, product_revenue=D(0)), fact(END - timedelta(days=7), product_revenue=amount)]
    )
    m = window(repo)[0]["metrics"]["revenue"]
    assert m["value"] == 0 and m["reason"] == reason and m["change"] == want


def test_ratio_uses_sums_not_daily_average_and_invalid_field_only_blocks_its_metrics():
    repo = Repo(
        [
            fact(END, product_revenue=D(100), buyers=10, visitors=100),
            fact(END - timedelta(days=1), product_revenue=D(300), buyers=10, visitors=300),
            fact(END - timedelta(days=7), product_revenue=D(100), buyers=10, visitors=100),
        ]
    )
    m = window(repo)[0]["metrics"]
    assert m["aov"]["value"] == 20 and m["aov"]["change"] == 1
    assert m["conversion"]["value"] == D(".05") and m["conversion"]["change"] == D("-.5")
    repo.data[0]["visitors"] = None
    m = window(repo)[0]["metrics"]
    assert m["revenue"]["change"] == 3 and m["conversion"]["value"] is None
    assert m["visitors"]["reason"] == "invalid_values"


@pytest.mark.parametrize("end", [date(2027, 1, 2), date(2028, 3, 1)])
def test_calendar_boundaries_are_exact_even_with_leap_day(end):
    repo = Repo([fact(end), fact(end - timedelta(days=30))])
    repo.start, repo.end = end - timedelta(days=59), end
    for w in window(repo):
        n = w["days"]
        assert w["start"] == end - timedelta(days=n - 1)
        assert w["previous_end"] == w["start"] - timedelta(days=1)
        assert w["previous_start"] == end - timedelta(days=2 * n - 1)


def test_unreceived_history_does_not_report_partial_period_growth():
    repo = Repo([fact(END), fact(END - timedelta(days=8))])
    repo.versions = {"product_daily": {"period_start": END - timedelta(days=10)}}
    m = window(repo)[0]["metrics"]["revenue"]
    assert m["value"] == 100 and m["previous"] is None and m["change"] is None
    assert m["reason"] == "history_not_received"
    m = window(repo)[2]["metrics"]["revenue"]
    assert m["value"] is None


def test_real_postgres_reads_full_period_before_page_start(sales_pg, tmp_path):
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.source import normalize
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    end, data = candidate(duplicate_profile=False)
    rows = [
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
    data["product_daily"] = normalize("product_daily", rows, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        qend = end - timedelta(days=30)
        w = overview(repo, BoardQuery(qend, qend))["windows"][2]
        assert w["previous_start"] < repo.start
        assert w["metrics"]["revenue"]["value"] == 3000
        assert w["metrics"]["revenue"]["previous"] == 3000
        assert w["metrics"]["revenue"]["change"] == 0


def test_windows_reuse_loaded_facts_and_only_read_extra_history_when_needed():
    class CountRepo(Repo):
        def __init__(self):
            super().__init__([fact(END), fact(END - timedelta(days=30))])
            self.calls = []

        def rows(self, start, end):
            self.calls.append((start, end))
            return super().rows(start, end)

    repo = CountRepo()
    window(repo)
    assert repo.calls.count((repo.start, repo.end)) == 1
