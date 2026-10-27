"""最终复核反例：完整日历、逐指标门禁、健康度和级联选择。"""

from datetime import date, timedelta
from decimal import Decimal as D

from app.modules.product_board.query import BoardQuery, options, overview
from app.modules.product_board.source import previous_year

END = date(2026, 10, 26)


def row(day, lid="x", shop="s", **patch):
    return dict(
        listing_id=lid,
        source_shop_id=shop,
        source_product_id=lid,
        source_shop_name=shop,
        source_product_name=lid,
        business_date=day,
        product_revenue=D("100"),
        visitors=100,
        buyers=10,
        orders=15,
        units=15,
        **patch,
    )


class Repo:
    start, end = END - timedelta(days=59), END

    def __init__(self, rows, profiles=None):
        self.data, self.info = rows, profiles or {}

    def rows(self, start, end):
        return [r for r in self.data if start <= r["business_date"] <= end]

    def profiles(self):
        return self.info


def test_missing_calendar_days_block_comparison_for_explicit_listing():
    repo = Repo([row(END), row(END - timedelta(days=7)), row(previous_year(END))])
    result = overview(repo, BoardQuery(END - timedelta(days=6), END, products=("x",)))
    revenue = result["metrics"]["revenue"]
    assert revenue["value"] == D("100")
    assert revenue["status"] == "partial"
    assert revenue["yoy"] is None and revenue["mom"] is None
    assert result["windows"][0]["metrics"]["revenue"]["change"] is None
    # 显式选择中完全缺记录的另一个K也不能被悄悄缩出期望范围。
    single = overview(repo, BoardQuery(END, END, products=("x", "absent")))
    assert single["metrics"]["revenue"]["status"] == "partial"
    assert single["series"][0]["previous"]["revenue"] is None


def test_fixed_shop_calendar_does_not_shrink_when_one_shop_missing_today():
    repo = Repo([row(END), row(END - timedelta(days=1), "y", "other")])
    result = overview(repo, BoardQuery(END, END))
    assert result["metrics"]["revenue"]["status"] == "partial"


def test_each_period_and_daily_comparison_requires_its_own_inputs():
    rows = [row(END - timedelta(days=i)) for i in range(14)]
    rows[7]["visitors"] = None
    older = [dict(r, business_date=previous_year(r["business_date"])) for r in rows[:7]]
    older[0]["buyers"] = None
    repo = Repo(rows + older)
    result = overview(repo, BoardQuery(END - timedelta(days=6), END, products=("x",)))
    period = result["windows"][0]["metrics"]
    assert period["revenue"]["change"] == 0
    assert period["visitors"]["change"] is None
    assert period["conversion"]["change"] is None
    assert period["visitors"]["comparison_status"] == "missing_comparable_rows"
    day = result["series"][-1]
    assert day["previous"]["revenue"] == D("100")
    assert day["previous"]["buyers"] is None
    assert day["previous"]["aov"] is None
    assert day["previous"]["conversion"] is None


def test_unknown_completeness_never_publishes_health_classification():
    for amount in ("90", "110"):
        a = row(END)
        a["product_revenue"] = D(amount)
        repo = Repo([a, row(previous_year(END))])
        result = overview(repo, BoardQuery(END, END, products=("x",)))
        assert result["health"]["title"] == "增长待验证"
        assert result["health"]["tone"] == "neutral"
        assert "完整" in result["health"]["detail"]


def test_cascade_preserves_valid_selection_and_only_clears_invalid_descendants():
    repo = Repo(
        [row(END), row(END, "y", "other")],
        dict(
            x=dict(operator="A", category="衣", level="新品"),
            y=dict(operator="B", category="鞋", level="常规"),
        ),
    )
    q = BoardQuery(END, END, shop="s", operator="A", category="衣", level="新品")
    good = options(repo, q, selected_ids=("x",), new_only=False)
    assert good["selection"] == dict(operator="A", category="衣", level="新品", products=["x"])
    bad = options(
        repo,
        BoardQuery(END, END, shop="other", operator="A", category="衣", level="新品"),
        selected_ids=("x", "y"),
    )
    assert bad["selection"] == dict(operator="", category="", level="", products=["y"])
    # 候选搜索、分页不能把仍在范围内的已选商品移除。
    found = options(repo, BoardQuery(END, END, shop="s", keyword="no match", page=2), ("x",))
    assert found["selection"]["products"] == ["x"]
    assert found["shops"] == good["shops"]
