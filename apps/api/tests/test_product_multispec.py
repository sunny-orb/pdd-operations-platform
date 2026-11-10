"""多规格是正常N:M；按完整链接计额一次，组合归属及风险资格独立于显示名。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.product_board.query import BoardQuery, options, overview
from app.modules.product_board.repository import snapshot
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore

pytest_plugins = ["tests.sales_pg_support"]
END = date(2026, 10, 28)


def publish(engine, path, levels=("A级", "B级"), growth=False):
    products = [
        dict(
            z_id=i + 1,
            shop_id="663599807",
            spu_id="123",
            dt=str(END - timedelta(days=i)),
            time_granularity="day",
            spu_name="平台组合标题",
            transaction_amount="10" if growth and i >= 7 else "23.17",
            product_uv=101,
            transaction_users=2,
            transaction_orders=0 if i == 0 else 10,
        )
        for i in range(14)
    ]
    # 总览较早日不能渗入近7日榜单。
    products.append(
        dict(products[1], z_id=15, dt=str(END - timedelta(days=20)), transaction_amount="1000")
    )
    profiles = [
        dict(__id=1, spigf="123", spmc="帽子-绒款", pl="帽子", cpdj=levels[0]),
        dict(__id=2, spigf="123", spmc="手套【大号】+赠品", pl="手套", cpdj=levels[1]),
        dict(__id=3, spigf="123", spmc="帽子-绒款", pl="帽子", cpdj=levels[0]),
    ]
    ads = [
        dict(
            id=1,
            shop_id="663599807",
            spu_id="123",
            dt=str(END),
            start_dt=str(END),
            time_granularity="day",
            spu_name="推广平台标题",
            total_cost="5.01",
            transaction_amount="25.03",
        )
    ]
    raw = dict(product_daily=products, profile_records=profiles, product_promotion=ads)
    rows = {code: normalize(code, values, END) for code, values in raw.items()}
    ProductBoardStore(engine, path).run(lambda hb: (END, rows))


def test_multispec_is_preserved_and_totals_filters_and_risk_use_one_listing(sales_pg, tmp_path):
    publish(sales_pg, tmp_path)
    with snapshot(sales_pg) as repo:
        p = next(iter(repo.profiles().values()))
        assert p["field_status"]["product_name"] == "multiple"
        assert p["category"] == "多品类组合"
        assert p["candidates"]["category"] == ["帽子", "手套"]
        assert p["level"] == "A级/B级混合"
        assert p["risk_eligible"] is True
        q = BoardQuery(END - timedelta(days=30), END)
        data = overview(repo, q)
        assert data["observed_rows"] == "15"
        assert data["metrics"]["revenue"]["value"] == Decimal("1324.38")
        card = data["brief"]["products"][0]
        assert card["product_name"] == "平台组合标题"
        assert card["revenue"] == Decimal("162.19")
        assert card["previous_revenue"] == Decimal("162.19")
        assert len(data["risks"]) == 1 and data["risks"][0]["alert"]["priority"] == "P1"
        assert overview(repo, BoardQuery(q.start, END, keyword="手套"))["observed_rows"] == "15"
        choices = options(repo, BoardQuery(q.start, END, keyword="手套"))
        assert choices["total"] == 1
        assert choices["products"][0]["label"].startswith("平台组合标题")
        assert overview(repo, BoardQuery(q.start, END, category="帽子"))["observed_rows"] == "0"
        assert (
            overview(repo, BoardQuery(q.start, END, category="多品类组合"))["observed_rows"] == "15"
        )
        category = data["brief"]["categories"][0]
        assert category["category"] == "多品类组合" and category["revenue"] == Decimal("162.19")


@pytest.mark.parametrize(
    "levels,eligible,new",
    [
        (("清仓", "清仓"), False, True),
        (("B级", "清仓"), False, False),
        (("新品", "B级"), True, False),
        (("新品", "新品"), True, True),
    ],
)
def test_mixed_levels_do_not_invent_newness_or_clearance(sales_pg, tmp_path, levels, eligible, new):
    publish(sales_pg, tmp_path, levels, growth=True)
    with snapshot(sales_pg) as repo:
        p = next(iter(repo.profiles().values()))
        assert p["risk_eligible"] is eligible
        assert (p["level"] == levels[0]) is new
        data = overview(repo, BoardQuery(END - timedelta(days=30), END))
        assert bool(data["risks"]) is eligible
        assert bool(data["brief"]["rising"]) is (levels == ("新品", "新品"))
        if data["brief"]["rising"]:
            item = data["brief"]["rising"][0]
            assert item["revenue"] == Decimal("162.19")
            assert item["previous_revenue"] == Decimal("70")


def test_shared_promotion_alias_and_combo_filter_do_not_multiply_spend(sales_pg, tmp_path):
    from app.modules.promotion.query import PromotionQuery
    from app.modules.promotion.query import overview as promotion_overview
    from app.modules.promotion.repository import snapshot as promotion_snapshot

    publish(sales_pg, tmp_path)
    with promotion_snapshot(sales_pg, "product") as repo:
        q = PromotionQuery(END, END, mode="product", keyword="手套", category="多品类组合")
        data = promotion_overview(repo, q)
        assert data["metrics"]["spend"]["value"] == Decimal("5.01")
        assert data["metrics"]["revenue"]["value"] == Decimal("25.03")
