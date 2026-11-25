"""共用助手整页工具：真实隔离PG、合成输入，不访问上游。"""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.product_board.repository import BoardError
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore

pytest_plugins = ["tests.sales_pg_support"]
END = date(2026, 10, 26)
SHOP = "663599807"
OTHER = "799193231"


@pytest.fixture
def board(sales_pg, tmp_path):
    daily, ads, profiles = [], [], []
    for number in range(32):
        pid = str(number + 100)
        profiles.append(
            dict(__id=number + 1, spigf=pid, spmc="同名商品", pl="雨具", cpdj="新品", fzyy="小王")
        )
        for sid in (SHOP, OTHER) if number == 0 else (SHOP,):
            for offset in range(14):
                day = str(END - timedelta(days=offset))
                daily.append(
                    dict(
                        z_id=len(daily) + 1,
                        shop_id=sid,
                        spu_id=pid,
                        spu_name="同名商品",
                        dt=day,
                        time_granularity="day",
                        product_uv=200,
                        transaction_users=0 if offset == 0 else 5,
                        transaction_orders=0 if offset == 0 else 5,
                        transaction_amount="0" if offset == 0 else "120",
                    )
                )
                if number < 2 and not (number == 0 and sid == SHOP and offset == 3):
                    ads.append(
                        dict(
                            id=str(len(ads) + 1),
                            shop_id=sid,
                            spu_id=pid,
                            spu_name="同名商品",
                            dt=day,
                            start_dt=day,
                            time_granularity="day",
                            total_cost="10",
                            transaction_amount="20",
                            impression_count="100",
                            click_count="10",
                            transaction_count="2",
                        )
                    )
    rows = {
        code: normalize(code, values, END)
        for code, values in (
            ("product_daily", daily),
            ("product_promotion", ads),
            ("profile_records", profiles),
        )
    }
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, rows))
    return sales_pg


def params(engine, domain="products", **extra):
    from app.modules.assistant.board_tools import describe

    return {"data_version": describe(engine, domain)["data_version"], **extra}


def values(report):
    return {fact["key"]: fact["value"] for fact in report["facts"]}


def assert_bounded(report):
    assert report["kind"] == "dashboard" and report["data_version"]
    for table in report["tables"]:
        assert len(table["rows"]) <= 25
        assert all(
            not isinstance(cell, (dict, list)) for row in table["rows"] for cell in row.values()
        )


def test_describe_reports_actual_domain_dates_and_bounded_identity_catalog(board):
    from app.modules.assistant.board_tools import describe

    data = describe(board)
    assert data["domain"] == "products" and data["date_to"] == str(END)
    assert {shop["id"] for shop in data["shops"]} == {SHOP, OTHER}
    assert len(data["options"]["products"]) == 25
    assert data["options"]["total"] == 33
    assert all(len(v) <= 25 for v in data["options"].values() if isinstance(v, list))
    ads = describe(board, "promotion", "product")
    assert ads["domain"] == "promotion_product" and ads["date_to"] == str(END)
    assert ads["data_version"] != data["data_version"]


@pytest.mark.parametrize(
    "capability",
    ["products_overview", "products_risks", "products_scores", "products_days", "products_catalog"],
)
def test_product_capabilities_pin_versions_and_preserve_full_totals(board, capability):
    from app.modules.assistant.board_tools import read

    report = read(board, capability, params(board, limit=2, shop_id=SHOP))
    assert_bounded(report)
    assert report["domain"] == "products" and report["scope"]["shop_id"] == SHOP
    assert report["versions"]["product_version"] == report["data_version"]
    assert report["period"]["date_to"] == str(END)
    if capability == "products_overview":
        assert Decimal(values(report)["revenue"]) == 32 * 6 * 120
        assert values(report)["product_count"] == "32"
        assert values(report)["health"] == "暂无同期可比数据"
        assert {t["title"] for t in report["tables"]} >= {"经营周期表现", "浏览到成交"}
    else:
        table = report["tables"][0]
        assert len(table["rows"]) == 2 and table["total"] > 2
        assert all(row.get("shop_id", SHOP) == SHOP for row in table["rows"])


def test_catalog_identity_filtering_and_paging_across_fixed_domain_page(board):
    from app.modules.assistant.board_tools import read

    version = params(board)
    page = read(board, "products_catalog", {**version, "shop_id": SHOP, "limit": 25})
    table = page["tables"][0]
    assert table["total"] == 32 and len(table["rows"]) == 25
    ids = [row["listing_id"] for row in table["rows"]]
    filtered = read(board, "products_overview", {**version, "listing_ids": ids[:1]})
    assert Decimal(values(filtered)["revenue"]) == 720
    for capability in ("products_catalog", "products_days", "products_scores"):
        first = read(board, capability, {**version, "shop_id": SHOP, "limit": 25})
        next_page = read(board, capability, {**version, "shop_id": SHOP, "limit": 10, "offset": 23})
        assert next_page["tables"][0]["rows"][:2] == first["tables"][0]["rows"][23:]
        assert next_page["tables"][0]["total"] == first["tables"][0]["total"]
    same = read(board, "products_catalog", {**version, "keyword": "同名商品", "limit": 25})
    assert same["tables"][0]["total"] == 33
    empty = read(board, "products_overview", {**version, "keyword": "不存在"})
    assert values(empty)["revenue"] is None


@pytest.fixture
def risk_board(sales_pg, tmp_path):
    products = (
        (SHOP, "100", "雪原帽", "24100", 120, 6),
        (SHOP, "101", "露营鞋", None, 200, 0),
        (OTHER, "101", "露营鞋", "0", 200, 0),
        (SHOP, "1772", "雨林伞", "10", 200, 4),
    )
    rows, profiles = [], []
    for shop, pid, name, day_revenue, visitors, orders in products:
        profiles.append(
            dict(
                __id=len(profiles) + 1,
                **{"spigf" if shop == SHOP else "spihw": pid},
                spmc=name,
                cpdj="A级",
            )
        )
        for lag in range(8):
            rows.append(
                dict(
                    z_id=len(rows) + 1,
                    shop_id=shop,
                    shop_name="雪原官方店" if shop == SHOP else "雪原户外店",
                    spu_id=pid,
                    spu_name=name,
                    dt=str(END - timedelta(days=lag)),
                    time_granularity="day",
                    product_uv=240 if lag else visitors,
                    transaction_orders=12 if lag else orders,
                    transaction_amount=("36650" if pid == "100" else "100") if lag else day_revenue,
                )
            )
    sources = {
        "product_daily": normalize("product_daily", rows, END),
        "profile_records": normalize("profile_records", profiles, END),
        "product_promotion": [],
    }
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, sources))
    return sales_pg


def test_risk_evidence_separates_daily_money_period_total_and_rule_units(risk_board):
    from app.modules.assistant.board_tools import read

    report = read(risk_board, "products_risks", params(risk_board))
    table = report["tables"][0]
    rows = {(r["shop_id"], r["product_id"]): r for r in table["rows"]}
    row = rows[(SHOP, "100")]
    assert Decimal(row["period_revenue"]) == 244000
    assert Decimal(row["evaluation_day_revenue"]) == 24100
    assert rows[(SHOP, "101")]["evaluation_day_revenue"] is None
    assert Decimal(rows[(OTHER, "101")]["evaluation_day_revenue"]) == 0
    assert row["metric"] == "商品访客数" and row["unit"] == "人"
    assert (Decimal(row["current"]), Decimal(row["baseline"])) == (120, 240)
    assert "日均" in row["baseline_method"]
    assert rows[(SHOP, "101")]["metric"] == "支付订单数" and rows[(SHOP, "101")]["unit"] == "单"
    conversion = rows[(SHOP, "1772")]
    assert conversion["metric"] == "订单转化率" and "小数" in conversion["unit"]
    assert Decimal(conversion["current"]) == Decimal("0.02")
    assert "合计" in conversion["baseline_method"]
    columns = {column["key"]: column for column in table["columns"]}
    for key in ("period_revenue", "evaluation_day_revenue", "estimated_impact"):
        assert columns[key]["unit"] == "currency" and "元" in columns[key]["label"]
        assert "2026-10-26" in columns[key]["label"]
    assert (
        "2026-10-20" in columns["period_revenue"]["label"]
        and "累计" in columns["period_revenue"]["label"]
    )
    assert "当天" in columns["evaluation_day_revenue"]["label"]
    assert "估算" in columns["estimated_impact"]["label"]
    assert "2026-10-26" in columns["current"]["label"]
    assert all(day in columns["baseline"]["label"] for day in ("2026-10-19", "2026-10-25"))
    counts = {f["key"]: f for f in report["facts"] if f["key"].endswith("_product_count")}
    assert counts["selected_product_count"]["value"] == 4
    assert len(counts) == 6 and all("款" in f["label"] for f in counts.values())


def test_risk_reference_requires_one_named_observed_listing_across_pages(risk_board):
    from app.modules.assistant.board_tools import read, risk_product_reference
    from app.modules.assistant.references import reference_from_answer
    from app.modules.product_board.source import identity

    p = params(risk_board, limit=1)
    pages = [read(risk_board, "products_risks", {**p, "offset": i}) for i in range(4)]
    first, shoe, other_shoe, umbrella = [r["tables"][0]["rows"][0] for r in pages]
    assert [r["product_name"] for r in (first, shoe, other_shoe, umbrella)] == [
        "雪原帽",
        "露营鞋",
        "露营鞋",
        "雨林伞",
    ]
    expected = dict(
        listing_id=identity(SHOP, "1772"),
        shop_id=SHOP,
        product_id="1772",
        name="雨林伞",
        shop_name="雪原官方店",
    )
    all_rows = (first, shoe, other_shoe, umbrella)
    estimates = [
        Decimal(r["estimated_impact"]) for r in all_rows if r["estimated_impact"] is not None
    ]
    for row in all_rows:
        amount = row["estimated_impact"]
        expected_rank = (
            1 + sum(a > Decimal(amount) for a in estimates) if amount is not None else None
        )
        assert row["estimated_impact_rank"] == expected_rank
    assert risk_product_reference(pages + pages[-1:], "建议先看**雨林伞**。") == expected
    assert risk_product_reference(pages[-1:], "先看商品 ID：1772。") == expected
    recorded_page = json.loads(json.dumps(pages[-1], ensure_ascii=False))
    assert "雪原帽" not in json.dumps(recorded_page, ensure_ascii=False)
    assert risk_product_reference([recorded_page], "雨林伞。") is None
    assert risk_product_reference([recorded_page], f"商品ID 1772，店铺ID {SHOP}。") == expected
    for answer in (
        "本轮共有 1772 款。",
        "建议先查列表第一款。",
        "雪原帽和雨林伞都要查。",
        "雨林伞（商品ID 999）。",
    ):
        assert risk_product_reference(pages, answer) is None
    assert risk_product_reference(pages[:1], "先查雨林伞。") is None
    assert risk_product_reference(pages[:1], "雪原帽、雨林伞一起核查。") is None
    catalog = {
        "capability": "products_catalog",
        "tables": [
            {
                "rows": [
                    dict(listing_id="other", shop_id=SHOP, product_id="300", product_name="山茶衫")
                ]
            }
        ],
    }
    assert risk_product_reference([pages[0], catalog], "雪原帽和山茶衫都要核查。") is None
    assert reference_from_answer([pages[0], catalog], "雪原帽和山茶衫都要核查。") is None
    assert risk_product_reference(pages[-1:], "商品ID：1772、101。") is None
    assert risk_product_reference(pages[1:2], "先查露营鞋。") is None
    assert risk_product_reference(pages[1:2], "商品ID 101。") is None
    assert risk_product_reference(pages[1:2], "雪原户外店的露营鞋。") is None
    outdoor = risk_product_reference(pages[1:3], "先核查雪原户外店的露营鞋。")
    assert outdoor and outdoor["listing_id"] == identity(OTHER, "101")


def test_risk_reference_distinguishes_complete_names_from_parts_of_another_name():
    from app.modules.assistant.board_tools import risk_product_reference
    from app.modules.product_board.source import identity

    rows = [
        dict(
            listing_id=identity(SHOP, pid),
            product_id=pid,
            shop_id=SHOP,
            product_name=name,
            shop_name="雪原店",
        )
        for pid, name in (("100", "雪原帽"), ("101", "加绒雪原帽"), ("102", "雨靴A1"))
    ]
    reports = [
        {"capability": "products_risks", "tables": [{"rows": rows, "total": 3, "offset": 0}]}
    ]
    ref = risk_product_reference(reports, "优先复核加绒雪原帽。")
    assert ref and ref["product_id"] == "101"
    assert risk_product_reference(reports, "加绒雪原帽和雪原帽的情况不同。") is None
    assert risk_product_reference(reports, "雨靴A10值得复核。") is None


@pytest.mark.parametrize(
    "capability",
    [
        "promotion_overview",
        "promotion_days",
        "promotion_diagnosis",
        "promotion_objects",
        "promotion_catalog",
    ],
)
def test_promotion_capabilities_preserve_store_identity_missing_days_and_sop_dates(
    board, capability
):
    from app.modules.assistant.board_tools import read

    report = read(
        board,
        capability,
        params(
            board,
            "promotion",
            shop_id=SHOP,
            limit=2,
            view="products",
            date_from=str(END),
            date_to=str(END),
        ),
    )
    assert_bounded(report)
    assert report["domain"] == "promotion_product"
    assert report["versions"]["promotion_version"] == report["data_version"]
    if capability == "promotion_overview":
        assert Decimal(values(report)["spend"]) == 20
        assert Decimal(values(report)["roi"]) == 2
        assert {t["title"] for t in report["tables"]} >= {"投产比最低的5天", "推广漏斗"}
    if capability in ("promotion_diagnosis", "promotion_objects"):
        assert report["scope"]["rule_period"]["date_from"] == "2026-10-20"
        assert report["scope"]["requested_period"]["date_from"] == str(END)
    if capability == "promotion_diagnosis":
        assert {t["title"] for t in report["tables"]} >= {"经营比较", "规则可判断情况"}
    if capability == "promotion_objects":
        assert report["tables"][0]["total"] == 2


def test_store_mode_keeps_channel_scope_and_rejects_product_filters(sales_pg, tmp_path):
    from app.modules.assistant.board_tools import describe, read
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_extended_sources import sample, window

    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        ShopDailyStore(
            sales_pg, archive_root=tmp_path / code, approved=window(), spec=SPECS[code]
        ).publish_candidate(normalize_source(code, [sample(code)], window()))
    description = describe(sales_pg, "promotion", "store")
    assert description["domain"] == "promotion_store" and description["date_to"] == str(END)
    p = dict(
        data_version=description["data_version"],
        mode="store",
        shop_id=SHOP,
        channel="product_promotion",
    )
    overview = read(sales_pg, "promotion_overview", p)
    assert Decimal(values(overview)["spend"]) == Decimal("10.20")
    assert overview["domain"] == "promotion_store"
    daily = read(sales_pg, "promotion_days", p)
    assert daily["tables"][0]["total"] == 1
    assert daily["tables"][0]["rows"][0]["channel"] == "product_promotion"
    for capability in ("promotion_diagnosis", "promotion_objects", "promotion_catalog"):
        assert_bounded(read(sales_pg, capability, p))
    with pytest.raises(BoardError, match="STORE_PRODUCT_FILTER_NOT_ALLOWED"):
        read(sales_pg, "promotion_overview", {**p, "category": "雨具"})


def test_invalid_versions_ranges_limits_and_unknown_capabilities_are_controlled(board):
    from app.modules.assistant.board_tools import read

    for capability in ("products_overview", "promotion_overview"):
        p = params(board, "products" if capability.startswith("products") else "promotion")
        with pytest.raises(BoardError, match="DATA_VERSION_CHANGED"):
            read(board, capability, {**p, "data_version": "stale"})
        for change in (
            {"limit": 26},
            {"offset": -1},
            {"recent_days": True},
            {"date_from": "bad", "date_to": str(END)},
            {"data_version": None},
            {"date_from": str(END)},
            {"listing_ids": ["100"]},
        ):
            with pytest.raises(BoardError):
                read(board, capability, {**p, **change})
    with pytest.raises(BoardError):
        read(board, "invented", params(board))


def test_detail_filters_scoring_window_and_missing_promotion_day_are_preserved(board):
    from app.modules.assistant.board_tools import read

    p = params(board)
    catalog = read(board, "products_catalog", {**p, "shop_id": SHOP, "keyword": "100"})
    [identity] = catalog["tables"][0]["rows"]
    scoped = {
        **p,
        "shop_id": SHOP,
        "operator": "小王",
        "category": "雨具",
        "level": "新品",
        "listing_ids": [identity["listing_id"]],
    }
    daily = read(board, "products_days", scoped)
    assert daily["tables"][0]["total"] == 7
    assert all(
        row["product_id"] == "100" and row["shop_id"] == SHOP for row in daily["tables"][0]["rows"]
    )
    score = read(board, "products_scores", {**scoped, "date_from": str(END), "date_to": str(END)})
    assert score["period"]["date_from"] == "2026-10-20"
    assert score["scope"]["requested_period"]["date_from"] == str(END)
    assert len(score["tables"][1]["rows"]) == 10
    assert score["tables"][0]["rows"][0]["score"] is None
    ad_params = params(
        board,
        "promotion",
        shop_id=SHOP,
        listing_ids=[identity["listing_id"]],
        date_from="2026-10-23",
        date_to="2026-10-23",
    )
    empty = read(board, "promotion_overview", ad_params)
    assert values(empty)["current_record_count"] == "0"
    assert values(empty)["missing_count"] == 1
    assert values(empty)["spend"] is None and values(empty)["roi"] is None
    objects = read(
        board, "promotion_objects", params(board, "promotion", view="category", shop_id=SHOP)
    )
    assert objects["scope"]["view"] == "categories" and objects["tables"][0]["total"] == 1
    for capability, domain in (
        ("products_overview", "products"),
        ("promotion_overview", "promotion"),
    ):
        result = read(board, capability, params(board, domain, shop_id=SHOP))
        assert 1 <= len(result["series"]) <= 4
        assert all(series["unit"] and len(series["points"]) == 7 for series in result["series"])
        assert all(
            set(point) >= {"date", "value", "state"}
            for series in result["series"]
            for point in series["points"]
        )


def test_promotion_absence_is_unavailable_not_zero(sales_pg, tmp_path):
    from app.modules.assistant.board_tools import describe
    from tests.test_product_board_storage import candidate

    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: candidate())
    assert describe(sales_pg)["domain"] == "products"
    with pytest.raises(BoardError, match="ASSISTANT_DATA_UNAVAILABLE"):
        describe(sales_pg, "promotion")


@pytest.fixture
def multidimensional_board(sales_pg, tmp_path):
    """三选二的各维交集；第三项留作反例，跨店同商品保持独立身份。"""
    from itertools import product

    daily, profiles = [], []
    for index, (category, operator, level) in enumerate(
        product(("雨具", "服饰", "鞋靴"), ("小王", "小李", "小陈"), ("新品", "A级", "B级"))
    ):
        pid = str(200 + index)
        profiles.append(
            dict(
                __id=index + 1,
                spigf=pid,
                spihw=pid,
                spifs=pid,
                spmc="同名多维商品",
                pl=category,
                fzyy=operator,
                cpdj=level,
            )
        )
        for shop in (SHOP, OTHER, "191467219"):
            for offset in range(14):
                daily.append(
                    dict(
                        z_id=len(daily) + 1,
                        shop_id=shop,
                        spu_id=pid,
                        spu_name="同名多维商品",
                        dt=str(END - timedelta(days=offset)),
                        time_granularity="day",
                        product_uv=200,
                        transaction_users=0 if offset == 0 else 5,
                        transaction_orders=0 if offset == 0 else 5,
                        transaction_amount=str(index + 1),
                    )
                )
    rows = {
        "product_daily": normalize("product_daily", daily, END),
        "profile_records": normalize("profile_records", profiles, END),
        "product_promotion": [],
    }
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (END, rows))
    return sales_pg


@pytest.mark.parametrize("sequence", [list, tuple])
def test_products_multi_dimensions_match_board_union_within_intersection_across(
    multidimensional_board, sequence
):
    from app.modules.assistant.board_tools import read
    from app.modules.product_board.query import BoardQuery, filtered_overview
    from app.modules.product_board.repository import snapshot

    engine = multidimensional_board
    dimensions = {
        "shop_id": sequence((SHOP, OTHER)),
        "category": sequence(("雨具", "服饰")),
        "operator": sequence(("小王", "小李")),
        "level": sequence(("新品", "A级")),
    }
    p = params(engine, **dimensions)
    result = read(engine, "products_overview", p)
    with snapshot(engine, p["data_version"]) as repo:
        original = filtered_overview(
            repo,
            BoardQuery(
                END - timedelta(days=6),
                END,
                shop=(SHOP, OTHER),
                category=("雨具", "服饰"),
                operator=("小王", "小李"),
                level=("新品", "A级"),
            ),
        )
    assert Decimal(values(result)["revenue"]) == 840
    assert values(result)["product_count"] == "16"
    assert Decimal(values(result)["revenue"]) == original["metrics"]["revenue"]["value"]
    assert values(result)["product_count"] == original["product_count"]
    for key, selected in dimensions.items():
        assert result["scope"][key] == list(selected)
    for capability in ("products_days", "products_catalog", "products_risks", "products_scores"):
        data = read(engine, capability, {**p, "limit": 25})
        assert (
            data["tables"][0]["total"]
            == {
                "products_days": 112,
                "products_catalog": 16,
                "products_risks": 16,
                "products_scores": 8,
            }[capability]
        )
        assert all(
            row["shop_id"] in (SHOP, OTHER)
            and row["category"] in ("雨具", "服饰")
            and row["operator"] in ("小王", "小李")
            and row["level"] in ("新品", "A级")
            for row in data["tables"][0]["rows"]
        )


def test_dashboard_dispatches_product_multi_selection_to_real_adapter(multidimensional_board):
    import asyncio

    from app.modules.assistant.chat import ChatScope, QueryRun
    from app.modules.assistant.dashboard import DashboardRequest
    from app.modules.assistant.dashboard import read as dashboard_read

    run = QueryRun(
        multidimensional_board,
        ChatScope(
            domain="products",
            shop_ids=[SHOP, OTHER],
            category_values=["雨具", "服饰"],
            operator_values=["小王", "小李"],
            level_values=["新品", "A级"],
        ),
        "最近7天商品表现",
    )
    result = asyncio.run(dashboard_read(run, DashboardRequest(capability="products_overview")))
    assert Decimal(values(result)["revenue"]) == 840
    assert values(result)["product_count"] == "16"
    assert result["scope"]["shop_id"] == [SHOP, OTHER]
    assert result["scope"]["category"] == ["雨具", "服饰"]
    assert result["scope"]["operator"] == ["小王", "小李"]
    assert result["scope"]["level"] == ["新品", "A级"]


def test_dimension_arrays_reject_bad_members_and_promotion_remains_single_value(board):
    from app.modules.assistant.board_tools import read

    product = params(board)
    ads = params(board, "promotion")
    for key in ("shop_id", "category", "operator", "level"):
        for invalid in (
            [],
            (),
            [None],
            [1],
            [True],
            [""],
            ["  "],
            ["x" * 121],
            ["x"] * 21,
            {"name": "雨具"},
            ["雨具", ["服饰"]],
        ):
            with pytest.raises(BoardError, match="INVALID_SCOPE"):
                read(board, "products_overview", {**product, key: invalid})
        for invalid in ([SHOP, OTHER], (SHOP,), [], ()):
            with pytest.raises(BoardError, match="INVALID_SCOPE"):
                read(board, "promotion_overview", {**ads, key: invalid})
