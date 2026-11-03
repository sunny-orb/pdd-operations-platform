"""建议动作复用四卡各自前三，按线上两段文字展示；真实PG/API不启动任务。"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board.actions import build_actions
from app.modules.product_board.query import BoardQuery, overview
from app.modules.product_board.repository import snapshot
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore

pytest_plugins = ["tests.sales_pg_support"]
END = date(2026, 10, 30)


def inputs(count=5, *, level="新品"):
    info, groups, candidates = {}, {}, []
    for i in range(count):
        lid = f"listing-{i}"
        item = dict(
            listing_id=lid,
            shop_id=f"shop-{i}",
            product_id="123",
            shop_name=f"店{i}",
            product_name=f"商品{i}",
            level=level,
            risk_eligible=True,
        )
        info[lid] = dict(item)
        candidates.append(dict(item))
        groups[lid] = [
            dict(
                business_date=END - timedelta(days=j),
                visitors=100,
                orders=10,
                buyers=8,
                product_revenue=D("200") if j < 7 else D("100"),
            )
            for j in range(14)
        ]
    return info, groups, candidates


def test_two_paragraphs_keep_both_top_threes_without_starving_new_products():
    info, groups, candidates = inputs(8)
    result = build_actions(info, groups, candidates[:4], candidates[4:], END)
    assert [r["kind"] for r in result["items"]] == ["risk", "opportunity"]
    risk, new = result["items"]
    assert risk["listing_ids"] == ["listing-0", "listing-1", "listing-2"]
    assert new["listing_ids"] == ["listing-4", "listing-5", "listing-6"]
    assert "商品3" not in risk["text"] and "商品7" not in new["text"]
    assert "优先处理" in risk["text"] and "商品可售状态" in risk["text"]
    assert "补充素材与关联推荐，分阶段放大有效流量并观察转化承接" in new["text"]
    assert result["generation_method"] == "rules" and result["ai_status"] == "not_connected"


def test_same_listing_can_appear_in_both_online_paragraphs():
    info, groups, candidates = inputs(1)
    result = build_actions(info, groups, candidates, candidates, END)
    assert [r["listing_ids"] for r in result["items"]] == [["listing-0"], ["listing-0"]]
    assert all("商品0" in r["text"] for r in result["items"])


def test_same_name_in_different_shops_is_disambiguated_without_merging():
    info, groups, candidates = inputs(2)
    for item in candidates:
        item["product_name"] = "同名商品"
    result = build_actions(info, groups, [], candidates, END)
    new = result["items"][1]
    assert new["listing_ids"] == ["listing-0", "listing-1"]
    assert "店0" in new["text"] and "店1" in new["text"]


def test_no_matches_missing_data_and_empty_records_remain_distinct():
    info, groups, _ = inputs(1, level="A级")
    result = build_actions(info, groups, [], [], END)
    assert result["status"] == "no_match"
    assert "经营正常" not in " ".join(r["text"] for r in result["items"])
    groups["listing-0"][0]["orders"] = None
    missing = build_actions(info, groups, [], [], END)
    assert missing["status"] == "insufficient_data"
    assert "数据不足" in missing["items"][0]["text"]
    assert "2026-10-30缺少支付订单" in missing["notices"][0]
    empty = build_actions({}, {}, [], [], END)
    assert empty["status"] == "empty" and empty["items"] == []


def test_same_shop_duplicate_names_still_get_ids_when_other_shops_are_present():
    info, groups, candidates = inputs(3)
    for i, item in enumerate(candidates):
        item.update(
            product_name="同名款",
            product_id=str(111 + i),
            shop_id="a" if i < 2 else "b",
            shop_name="店A" if i < 2 else "店B",
        )
    result = build_actions(info, groups, [], candidates, END)
    text = result["items"][1]["text"]
    assert "店A·111" in text and "店A·112" in text and "同名款（店B）" in text


def test_missing_new_product_amount_does_not_claim_risk_inputs_are_missing():
    info, groups, _ = inputs(1)
    groups["listing-0"][13]["product_revenue"] = None
    result = build_actions(info, groups, [], [], END)
    assert "数据不足" not in result["items"][0]["text"]
    assert "数据不足" in result["items"][1]["text"]


def test_missing_amount_does_not_hide_existing_flow_risk():
    info, groups, candidates = inputs(1, level="A级")
    groups["listing-0"][0]["product_revenue"] = None
    result = build_actions(info, groups, candidates, [], END)
    assert result["items"][0]["listing_ids"] == ["listing-0"]
    assert "优先处理" in result["items"][0]["text"]


def test_summary_does_not_add_conversion_or_inventory_calculations():
    info, groups, candidates = inputs(1)
    groups["listing-0"][0].update(buyers=None, visitors=0)
    result = build_actions(info, groups, [], candidates, END)
    assert result["items"][1]["listing_ids"] == ["listing-0"]
    assert "库存" not in result["items"][1]["text"] and "%" not in result["items"][1]["text"]


def test_missing_notices_stay_bounded():
    info, groups, _ = inputs(5, level="A级")
    for rows in groups.values():
        rows[0]["orders"] = None
    result = build_actions(info, groups, [], [], END)
    assert len(result["notices"]) == 4
    assert "另有2款" in result["notices"][-1]


def test_absent_daily_rows_are_counted_as_days_not_missing_visitor_fields():
    info, groups, _ = inputs(1, level="A级")
    groups["listing-0"] = groups["listing-0"][3:]
    result = build_actions(info, groups, [], [], END)
    notice = result["notices"][0]
    assert "2026-10-30暂无该商品的日经营记录" in notice
    assert "共缺3天记录" in notice
    assert "缺少商品访客" not in notice


def test_absent_rows_and_null_fields_are_explained_separately():
    info, groups, _ = inputs(1, level="A级")
    groups["listing-0"] = groups["listing-0"][1:]
    groups["listing-0"][0]["orders"] = None
    result = build_actions(info, groups, [], [], END)
    notice = result["notices"][0]
    assert "共缺1天记录" in notice
    assert "另有1处字段缺失或无效" in notice


def test_real_pg_groups_match_cards_and_filter_and_get_does_not_create_tasks(sales_pg, tmp_path):
    rows = []
    for shop in ("663599807", "799193231"):
        for j in range(14):
            rows.append(
                dict(
                    z_id=len(rows) + 1,
                    shop_id=shop,
                    spu_id="123",
                    dt=str(END - timedelta(days=j)),
                    time_granularity="day",
                    transaction_amount="200" if j < 7 else "100",
                    product_uv=100,
                    transaction_users=8,
                    transaction_orders=10,
                )
            )
    data = dict(
        product_daily=normalize("product_daily", rows, END),
        profile_records=normalize(
            "profile_records",
            [dict(__id=1, spigf="123", spihw="123", spmc="同名新品", cpdj="新品", pl="帽子")],
            END,
        ),
        product_promotion=[],
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, deepcopy(data)))
    with snapshot(sales_pg) as repo:
        result = overview(repo, BoardQuery(END, END))
        assert result["brief"]["actions"]["items"][1]["listing_ids"] == [
            r["listing_id"] for r in result["brief"]["rising"]
        ]
        assert all(r["revenue"] == D("1400") for r in result["brief"]["rising"])
        filtered = overview(repo, BoardQuery(END, END, shop="799193231"))
        assert len(filtered["brief"]["actions"]["items"][1]["listing_ids"]) == 1
    with sales_pg.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM public.task_runs")).scalar_one()
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        client.app.state.engine = sales_pg
        meta = client.get("/api/v1/products/meta").json()
        params = dict(
            date_from=str(END), date_to=str(END), data_version=meta["meta"]["data_version"]
        )
        response = client.get("/api/v1/products/overview", params=params)
        assert response.status_code == 200, response.text
        actions = response.json()["data"]["brief"]["actions"]
        assert actions["as_of_date"] == "2026-10-30"
        assert actions["generation_method"] == "rules" and len(actions["items"]) == 2
        assert "同名新品" in actions["items"][1]["text"]
        empty = client.get(
            "/api/v1/products/overview", params={**params, "category": "不存在的品类"}
        )
        assert empty.status_code == 200
        assert empty.json()["data"]["brief"]["actions"]["status"] == "empty"
    with sales_pg.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM public.task_runs")).scalar_one() == before
        )
