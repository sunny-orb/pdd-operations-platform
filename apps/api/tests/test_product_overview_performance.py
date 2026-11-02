"""商品总览批量比较与按需汇总保留原精度、字段门禁和日历身份。"""

from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest

from app.modules.product_board import metrics, query
from app.modules.product_board.metrics import aggregate


def row(day, lid="a", **updates):
    return (
        dict(
            listing_id=lid,
            business_date=day,
            product_revenue=Decimal("0.03"),
            visitors=10,
            buyers=2,
            favorites=0,
            units=3,
            views=20,
            orders=2,
        )
        | updates
    )


def test_comparisons_map_each_calendar_day_once_without_weakening_metric_gates():
    """同一配对批次不能再按七指标重复映射10个商品的同一日期。"""
    today = date(2026, 10, 29)
    current = [row(today, str(i)) for i in range(10)]
    past = [row(today - timedelta(days=7), str(i)) for i in range(10)]
    current[0]["buyers"] = None
    current[1]["favorites"] = -1
    mapped = []

    def map_day(day):
        mapped.append(day)
        return day - timedelta(days=7)

    batch = getattr(query, "comparisons", None)
    assert batch is not None, "逐指标配对仍重复构造同一商品/日键集合"
    result = batch(current, past, map_day)
    assert result == {
        "revenue": True,
        "visitors": True,
        "favorites": False,
        "buyers": False,
        "units": True,
        "aov": False,
        "conversion": False,
    }
    assert mapped == [today]


@pytest.mark.parametrize("reason", ["empty", "missing", "other_listing", "leap_day"])
def test_comparisons_reject_nonmatching_identity_dates(reason):
    today = date(2024, 2, 29)
    current = [row(today)]
    past = [row(date(2023, 2, 28))]

    def mapper(day):
        return date(2023, 2, 28)

    if reason == "empty":
        current = past = []
    elif reason == "missing":
        past = []
    elif reason == "other_listing":
        past[0]["listing_id"] = "other"
    else:
        mapper = query.previous_year
    batch = getattr(query, "comparisons", None)
    assert batch is not None
    assert set(batch(current, past, mapper).values()) == {False}


def test_selected_aggregates_keep_exact_sum_and_paired_denominators():
    """仅取热榜两项时仍保留小数、极大数抵消、缺失与负数成对过滤。"""
    today = date(2026, 10, 29)
    rows = [
        row(today, product_revenue=Decimal("1E+70"), buyers=None),
        row(today, product_revenue=Decimal("0.0000000001"), buyers=3, visitors=4),
        row(today, product_revenue=Decimal("-1E+70"), buyers=9, visitors=-1),
    ]
    with localcontext() as ctx:
        ctx.prec = 6
        result = aggregate(rows, only=("revenue", "conversion"))
    assert result == {
        "revenue": dict(
            value=Decimal("0.0000000001"),
            status="observed",
            reason=None,
            valid_rows="3",
            observed_rows="3",
        ),
        "conversion": dict(
            value=Decimal("0.75"),
            status="partial",
            reason="只采用分子分母同时有效的行",
            numerator=Decimal(3),
            denominator=Decimal(4),
            valid_rows="1",
            observed_rows="3",
        ),
    }


@pytest.mark.parametrize(
    "rows",
    [[], [row(date(2026, 10, 29), visitors=0, buyers=0)], [row(date(2026, 10, 29), buyers=None)]],
)
def test_selected_aggregates_preserve_zero_missing_and_empty(rows):
    full = aggregate(rows)
    assert aggregate(rows, only=("revenue", "conversion")) == {
        key: full[key] for key in ("revenue", "conversion")
    }


def test_daily_aggregate_merge_preserves_exact_sums_counts_and_paired_rows():
    today = date(2026, 10, 29)
    parts = [
        aggregate([row(today, product_revenue=Decimal("1E+70"), buyers=None)]),
        aggregate([row(today, product_revenue=Decimal("0.0000000001"), buyers=3, visitors=4)]),
        aggregate([row(today, product_revenue=Decimal("-1E+70"), buyers=9, visitors=-1)]),
        aggregate([]),
    ]
    merge = getattr(metrics, "merge_aggregates", None)
    assert merge is not None, "重叠窗口仍逐次遍历全部商品日"
    with localcontext() as ctx:
        ctx.prec = 6
        result = merge(parts)
    assert result["revenue"] == dict(
        value=Decimal("0.0000000001"),
        status="observed",
        reason=None,
        valid_rows="3",
        observed_rows="3",
    )
    assert result["buyers"] == dict(
        value=Decimal(12),
        status="partial",
        reason="字段缺失或无记录",
        valid_rows="2",
        observed_rows="3",
    )
    assert result["conversion"] == dict(
        value=Decimal("0.75"),
        status="partial",
        reason="只采用分子分母同时有效的行",
        numerator=Decimal(3),
        denominator=Decimal(4),
        valid_rows="1",
        observed_rows="3",
    )


def test_daily_aggregate_merge_does_not_average_ratios_or_drop_zero_denominators():
    today = date(2026, 10, 29)
    parts = [
        aggregate([row(today, buyers=1, visitors=0)]),
        aggregate([row(today, buyers=1, visitors=10)]),
    ]
    merge = getattr(metrics, "merge_aggregates", None)
    assert merge is not None
    result = merge(parts)
    assert result["conversion"] == dict(
        value=Decimal("0.2"),
        status="observed",
        reason=None,
        numerator=Decimal(2),
        denominator=Decimal(10),
        valid_rows="2",
        observed_rows="2",
    )
    assert merge([]) == aggregate([])


def test_overview_extreme_cancellation_preserves_original_decimal_string():
    """危险金额先原窗回退，不能在日聚合抵消后把0E+21改成0。"""
    today = date(2026, 10, 29)
    facts = [
        dict(
            row(today, str(i), product_revenue=Decimal(amount)),
            source_shop_id="s",
            source_product_id=str(i),
            source_shop_name="店",
            source_product_name="商品",
        )
        for i, amount in enumerate(("1E+70", "-0.00", "-1E+70"))
    ]

    class Repo:
        start, end = today - timedelta(days=59), today

        def rows(self, start, end):
            return [r for r in facts if start <= r["business_date"] <= end]

        def profiles(self):
            return {}

    result = query.overview(Repo(), query.BoardQuery(today, today))
    assert str(result["metrics"]["revenue"]["value"]) == "0E+21"
