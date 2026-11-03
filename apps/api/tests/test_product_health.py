"""健康度只使用同店同款逐日共同记录；不改变总览其他指标。"""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.product_board.query import BoardQuery, overview
from app.modules.product_board.source import previous_year
from tests.test_product_board_review import END, Repo, row

pytest_plugins = ["tests.sales_pg_support"]


def fact(day, lid="x", **patch):
    return dict(row(day, lid), **patch)


def health(rows, start=END, end=END, **filters):
    repo = Repo(rows)
    repo.start, repo.end = start - timedelta(days=59), end
    return overview(repo, BoardQuery(start, end, **filters))["health"]


def test_health_matches_both_sides_without_unmatched_new_or_retired_rows():
    rows = [
        fact(END, product_revenue=D(100), visitors=100, buyers=10),
        fact(previous_year(END), product_revenue=D(50), visitors=50, buyers=10),
        fact(END, "new", product_revenue=D(900)),
        fact(previous_year(END), "retired", product_revenue=D(800)),
        fact(END - timedelta(days=1), product_revenue=D(700)),
    ]
    result = overview(Repo(rows), BoardQuery(END - timedelta(days=1), END))
    h = result["health"]
    assert h["title"] == "规模增长、效率承压"
    assert h["matched_product_days"] == h["matched_products"] == 1
    assert h["metrics"]["revenue"] == dict(value=D(100), previous=D(50), yoy=D(1), reason=None)
    assert h["metrics"]["conversion"]["yoy"] == D("-.5")
    assert h["metrics"]["aov"]["yoy"] == D(1)
    # 主卡按线上期间口径，健康度仍单独使用两边共同记录。
    assert result["metrics"]["revenue"]["value"] == D(1700)
    assert result["metrics"]["revenue"]["yoy"] == 33


@pytest.mark.parametrize(
    "amount,title",
    [
        ("90", "交易额同比下降"),
        ("110", "交易额同比增长"),
        ("100", "交易额同比持平"),
        ("0", "交易额同比下降"),
    ],
)
def test_health_changes_with_observed_amount_and_keeps_true_zero(amount, title):
    h = health([fact(END, product_revenue=D(amount)), fact(previous_year(END))])
    assert h["title"] == title
    assert h["metrics"]["revenue"]["yoy"] == D(amount) / 100 - 1
    assert "增长待验证" not in h["detail"]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [fact(END)],
        [
            fact(END, "shop-a", source_product_id="123"),
            fact(previous_year(END), "shop-b", source_shop_id="other", source_product_id="123"),
        ],
    ],
)
def test_no_comparable_rows_never_joins_across_shops_or_fills_zero(rows):
    h = health(rows)
    assert h["title"] == "暂无同期可比数据"
    assert h["matched_product_days"] == 0
    assert all(m["yoy"] is None for m in h["metrics"].values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("product_revenue", None),
        ("product_revenue", D(-1)),
        ("buyers", None),
        ("visitors", None),
        ("visitors", -1),
    ],
)
def test_bad_required_field_only_blocks_dependent_metric(field, value):
    h = health([fact(END, **{field: value}), fact(previous_year(END))])
    affected = {
        "product_revenue": ("revenue", "aov"),
        "buyers": ("conversion", "aov"),
        "visitors": ("conversion",),
    }[field]
    for key in affected:
        assert h["metrics"][key]["yoy"] is None
        assert h["metrics"][key]["reason"]
    for key in set(h["metrics"]) - set(affected):
        assert h["metrics"][key]["yoy"] == 0


def test_missing_efficiency_is_not_zero_but_known_decline_can_support_watch():
    h = health(
        [fact(END, product_revenue=D(110), buyers=20, visitors=None), fact(previous_year(END))]
    )
    assert h["title"] == "规模增长、效率承压"
    assert h["metrics"]["conversion"]["yoy"] is None
    assert h["metrics"]["aov"]["yoy"] == D("-.45")
    h = health(
        [fact(END, product_revenue=D(110), buyers=10, visitors=None), fact(previous_year(END))]
    )
    assert h["title"] == "交易额同比增长"
    assert "增长健康" not in h["title"]


def test_zero_baseline_and_denominators_are_not_growth_or_zero_percent():
    h = health([fact(END), fact(previous_year(END), product_revenue=D(0), buyers=0, visitors=0)])
    assert h["title"] == "交易额暂无可比增幅"
    assert all(m["yoy"] is None for m in h["metrics"].values())
    assert "去年同期交易额为0" in h["detail"]


def test_ratios_use_sums_for_identical_pairs_not_daily_averages():
    rows = [fact(END, buyers=1, visitors=1), fact(END - timedelta(days=1), buyers=9, visitors=99)]
    rows += [fact(previous_year(r["business_date"]), buyers=5, visitors=50) for r in rows]
    h = health(rows, END - timedelta(days=1))
    assert h["metrics"]["conversion"]["value"] == D(".1")
    assert h["metrics"]["conversion"]["yoy"] == 0
    assert h["metrics"]["aov"]["yoy"] == 0
    assert h["matched_product_days"] == 2


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 10, 31), date(2026, 11, 1)),
        (date(2026, 12, 31), date(2027, 1, 1)),
        (date(2028, 2, 28), date(2028, 3, 1)),
    ],
)
def test_calendar_pairs_across_month_year_and_leap_day(start, end):
    rows = [fact(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    rows += [
        fact(previous_year(r["business_date"])) for r in rows if previous_year(r["business_date"])
    ]
    h = health(rows, start, end)
    assert h["matched_product_days"] == 2
    assert h["metrics"]["revenue"]["value"] == h["metrics"]["revenue"]["previous"] == D(200)
    assert h["title"] == "交易额同比持平"


def test_unrounded_direction_and_selected_listing_are_preserved():
    rows = [
        fact(END, product_revenue=D("100.00001")),
        fact(previous_year(END)),
        fact(END, "other", product_revenue=D(0)),
        fact(previous_year(END), "other"),
    ]
    h = health(rows, products=("x",))
    assert h["title"] == "交易额同比增长"
    assert h["metrics"]["revenue"]["yoy"] == D(".0000001")
    assert "<0.01%" in h["detail"]


def test_real_pg_health_pairs_and_api_use_published_rows_without_changing_main_metrics(
    sales_pg, tmp_path
):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app
    from app.modules.product_board.source import normalize
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    end, data = candidate()
    raw = []
    for day, shop, product, amount, visitors, buyers in [
        ("2026-10-25", "663599807", "123", "100", 100, 10),
        ("2026-10-26", "663599807", "123", "300", 100, 10),
        ("2025-10-25", "663599807", "123", "50", 50, 10),
        ("2025-10-26", "663599807", "123", "150", 50, 10),
        ("2026-10-26", "663599807", "456", "9000", 100, 10),
        ("2025-10-25", "663599807", "789", "7000", 100, 10),
        ("2026-10-26", "799193231", "123", "500", 100, 10),
        ("2025-10-25", "799193231", "123", "6000", 100, 10),
    ]:
        raw.append(
            dict(
                z_id=len(raw) + 1,
                shop_id=shop,
                spu_id=product,
                dt=day,
                time_granularity="day",
                transaction_amount=amount,
                product_uv=visitors,
                transaction_users=buyers,
            )
        )
    data["product_daily"] = normalize("product_daily", raw, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        meta = client.get("/api/v1/products/meta").json()["data"]
        assert meta["complete_through"] is None
        params = dict(
            date_from="2026-10-25", date_to="2026-10-26", data_version=meta["data_version"]
        )
        response = client.get("/api/v1/products/overview", params=params)
        assert response.status_code == 200
        result = response.json()["data"]
        h = result["health"]
        assert h["title"] == "规模增长、效率承压"
        assert h["matched_product_days"] == 2 and h["matched_products"] == 1
        assert D(h["metrics"]["revenue"]["value"]) == 400
        assert D(h["metrics"]["revenue"]["previous"]) == 200
        assert D(h["metrics"]["conversion"]["yoy"]) == D("-.5")
        assert D(result["metrics"]["revenue"]["value"]) == 9900
        assert result["metrics"]["revenue"]["yoy"] == "48.5"
        # 跨店同ID不匹配，店铺筛选独立生效。
        filtered = client.get(
            "/api/v1/products/overview", params=dict(params, shop_id="799193231")
        ).json()["data"]
        assert filtered["health"]["title"] == "暂无同期可比数据"
        assert D(filtered["metrics"]["revenue"]["value"]) == 500
