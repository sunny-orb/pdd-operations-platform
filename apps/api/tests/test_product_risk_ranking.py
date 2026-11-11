"""风险爆品候选在单款/搜索之前固定；临界同额公平纳入，不改变其他比较。"""

from datetime import timedelta
from decimal import Decimal as D

from app.modules.product_board.query import BoardQuery, overview
from tests.test_product_board_review import END, Repo
from tests.test_product_metric_pairs import fact

pytest_plugins = ["tests.sales_pg_support"]


def sample():
    amounts = {"a": 100, "b": 60, "c": 60, "d": 20, "e": 10, "f": 5}
    rows = [
        fact(
            END - timedelta(days=i),
            lid,
            product_revenue=D(amount),
            visitors=100 if i == 0 else 200,
            orders=4 if i == 0 else 10,
        )
        for lid, amount in amounts.items()
        for i in range(8)
    ]
    profiles = {lid: dict(level="A级", category="帽子", operator="甲") for lid in amounts}
    return Repo(rows, profiles)


def alerts(repo, **filters):
    board = overview(repo, BoardQuery(END - timedelta(days=7), END, **filters))
    return {r["listing_id"]: r["alert"] for r in board["risks"]}


def test_single_selection_and_search_do_not_promote_low_revenue_product():
    repo = sample()
    baseline = alerts(repo)["d"]
    assert baseline["priority"] == "P1"
    assert alerts(repo, products=("d",))["d"] == baseline
    assert alerts(repo, keyword="d")["d"] == baseline


def test_equal_revenue_at_twenty_percent_boundary_is_included():
    result = alerts(sample())
    assert [result[k]["priority"] for k in ("a", "b", "c")] == ["P0"] * 3
    assert result["d"]["priority"] == "P1"


def test_rank_pool_keeps_business_filters_and_empty_results():
    repo = sample()
    repo.info["d"]["category"] = "书包"
    assert alerts(repo, category="书包", products=("d",))["d"]["priority"] == "P0"
    assert alerts(repo, operator="无人") == {}


def test_brief_is_exact_prefix_and_same_rule_ties_use_stable_identity():
    repo = sample()
    first = overview(repo, BoardQuery(END - timedelta(days=7), END))
    repo.data.reverse()
    second = overview(repo, BoardQuery(END - timedelta(days=7), END))
    assert first["risks"] == second["risks"]
    assert first["brief"]["risks"] == first["risks"][:3]


def test_postgres_rank_pool_preserved_after_selection(sales_pg, tmp_path):
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.source import normalize
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    end, data = candidate(duplicate_profile=False)
    amounts = {"123": 100, "234": 60, "345": 60, "456": 20, "567": 10, "678": 5}
    rows = [
        dict(
            z_id=n,
            shop_id="663599807",
            spu_id=pid,
            dt=str(end - timedelta(days=i)),
            time_granularity="day",
            transaction_amount=str(amount),
            product_uv=100 if i == 0 else 200,
            transaction_users=4 if i == 0 else 10,
            transaction_orders=4 if i == 0 else 10,
            transaction_products=10,
        )
        for n, (pid, amount, i) in enumerate(
            ((p, a, i) for p, a in amounts.items() for i in range(8)), 1
        )
    ]
    data["product_daily"] = normalize("product_daily", rows, end)
    data["profile_records"] = normalize(
        "profile_records",
        [
            dict(__id=i, spigf=pid, pl="帽子", cpdj="A级", spmc=pid)
            for i, pid in enumerate(amounts, 1)
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        full = overview(repo, BoardQuery(end - timedelta(days=7), end))
        items = {r["product_id"]: r for r in full["risks"]}
        assert items["234"]["alert"]["priority"] == items["345"]["alert"]["priority"] == "P0"
        low = items["456"]
        single = overview(
            repo, BoardQuery(end - timedelta(days=7), end, products=(low["listing_id"],))
        )
        assert single["risks"][0]["alert"] == low["alert"]
        assert single["metrics"]["revenue"]["value"] == D(160)
