"""改名不改变身份、事实和历史归属；合成输入与隔离PostgreSQL验证。"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_normalization import normalize_source
from tests.test_sales_extended_sources import sample
from tests.test_sales_source import raw_row, window

pytest_plugins = ["tests.sales_pg_support"]
OLD = "kocotree童装童鞋旗舰店"
NEW = "kocotree青少年服饰旗舰店"
SID = "271470254"
JST_OLD = f"kk树-{OLD}-配饰"
JST_NEW = f"kk树-{NEW}-配饰"


@pytest.mark.parametrize("name", [OLD, NEW])
def test_shop_daily_accepts_alias_preserves_identity_and_original_name(name):
    row = normalize_shop_daily([raw_row(shop_id=SID, shop_name=name)], window()).rows[0]
    assert (row["shop_id"], row["source_shop_id"], row["source_shop_name"]) == (
        "spitztx",
        SID,
        name,
    )
    assert row["shop_revenue"] == Decimal("10.20")


@pytest.mark.parametrize(
    "code", ["shop_ratings", "shop_channel_product_promotion", "shop_regions", "product_regions"]
)
@pytest.mark.parametrize("name", [OLD, NEW])
def test_extended_sources_keep_same_shop_for_both_names(code, name):
    row = normalize_source(code, [sample(code, shop_id=SID, shop_name=name)], window()).rows[0]
    assert row["shop_id"] == "spitztx"
    assert row["source_shop_name"] == name


@pytest.mark.parametrize("name", [JST_OLD, JST_NEW])
def test_feishu_alias_keeps_refund_amount(name):
    row = normalize_source("shop_refunds", [sample("shop_refunds", 店铺=name)], window()).rows[0]
    assert row["shop_id"] == "spitztx"
    assert row["reported_refund_amount"] == Decimal("10.20")
    assert row["source_shop_name"] == name


@pytest.mark.parametrize("name", [JST_OLD, JST_NEW])
@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_jst_alias_keeps_identity_and_raw_name(name, code):
    from app.modules.aftersales.ingestion import normalize

    row = dict(
        __id="1",
        shop_code="11273433",
        shop_name=name,
        product_code="07",
        product_name="帽子",
        spec_name="蓝",
        confirm_date="2026-10-26 10:00:00",
        pay_time="2026-10-26 10:00:00",
        aftersale_num="2",
        actual_receive_amount="40",
        platform_subsidy_amount="0",
        sfje="20",
        sale_amount="30",
        sale_num="2",
        outstock_num="1",
    )
    result = normalize(code, [row], date(2026, 10, 27))[0]
    assert (result["shop_id"], result["source_shop_id"], result["source_shop_name"]) == (
        "spitztx",
        "11273433",
        name,
    )


def test_aliases_never_duplicate_same_shop_day_or_accept_unknown_names():
    with pytest.raises(SalesError, match="DUPLICATE"):
        normalize_source(
            "shop_refunds",
            [
                sample("shop_refunds", 店铺=JST_OLD),
                sample("shop_refunds", record_id="rec2", 店铺=JST_NEW),
            ],
            window(),
        )
    with pytest.raises(SalesError, match="SHOP_NAME_CONFLICT"):
        normalize_shop_daily([raw_row(shop_id=SID, shop_name="未知店铺")], window())
    with pytest.raises(SalesError, match="REFUND_SCOPE"):
        normalize_source(
            "shop_refunds", [sample("shop_refunds", 店铺="kk树-kocotree欢趣-配饰")], window()
        )


def test_both_jst_probe_queries_include_both_names_with_matching_parameters():
    from app.connectors.source_probes import warehouse_query

    for code in ("jst_sales", "jst_returns"):
        sql, params = warehouse_query(code, date(2026, 10, 27))
        assert JST_OLD in params and JST_NEW in params
        assert sql.count("%s") == len(params)
        assert "kk树-kocotree欢趣-配饰" not in params


def test_product_and_promotion_labels_use_current_name_without_mutating_facts():
    from app.modules.product_board.query import labels
    from app.modules.promotion.query import labels as promotion_labels

    rows = [
        dict(
            listing_id="listing",
            source_shop_id=SID,
            source_shop_name=OLD,
            source_product_id="123",
            source_product_name="帽子",
            business_date=date(2026, 10, 26),
            date=date(2026, 10, 26),
        )
    ]
    assert labels(rows, {})[0]["listing"]["shop_name"] == NEW
    assert promotion_labels(SimpleNamespace(profiles={}), rows)["listing"]["shop_name"] == NEW
    assert rows[0]["source_shop_name"] == OLD


def test_real_pg_sales_publish_keeps_identity_and_facts_while_query_shows_current_name(
    sales_pg, tmp_path
):
    from app.modules.sales.storage import ShopDailyStore

    with sales_pg.begin() as c:
        c.execute(
            sa.text("INSERT INTO shops(id,shop_name,status) VALUES('spitztx',:name,'active')"),
            {"name": OLD},
        )
        created = c.execute(sa.text("SELECT created_at FROM shops WHERE id='spitztx'")).scalar_one()
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window())
    candidate = normalize_shop_daily([raw_row(shop_id=SID, shop_name=OLD)], window())
    result = store.publish_candidate(candidate)
    assert store.publish_candidate(candidate).version_id == result.version_id
    with sales_pg.connect() as c:
        assert c.execute(
            sa.text("SELECT shop_name,created_at FROM shops WHERE id='spitztx'")
        ).one() == (OLD, created)
        assert c.execute(sa.text("SELECT count(*) FROM shops")).scalar_one() == 6
        assert c.execute(
            sa.text("SELECT source_shop_name,shop_revenue FROM shop_daily_facts")
        ).one() == (OLD, Decimal("10.20"))

    from app.modules.sales.query import SalesService

    data = SalesService(sales_pg).filters()
    shop = next(s for s in data["data"]["shops"] if s["shop_id"] == "spitztx")
    assert shop["shop_name"] == NEW
    assert shop["display_name"] == "青少年服饰店"


def test_real_pg_product_and_promotion_show_current_name_for_old_rows_and_profile_only(
    sales_pg, tmp_path
):
    from app.modules.product_board.query import BoardQuery, options, overview, score_cards
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.source import identity, normalize
    from app.modules.product_board.storage import ProductBoardStore
    from app.modules.promotion.query import PromotionQuery
    from app.modules.promotion.query import options as promotion_options
    from app.modules.promotion.repository import snapshot as promotion_snapshot

    end = date(2026, 10, 26)
    with sales_pg.begin() as c:
        c.execute(
            sa.text("INSERT INTO shops(id,shop_name,status) VALUES(:id,:name,'active')"),
            dict(id=SID, name=OLD),
        )
    raw = dict(
        shop_id=SID,
        shop_name=OLD,
        spu_id="123",
        spu_name="帽子",
        dt=str(end),
        time_granularity="day",
    )
    rows = {
        "product_daily": normalize(
            "product_daily", [dict(raw, z_id=1, transaction_amount="23.17")], end
        ),
        "product_promotion": normalize(
            "product_promotion",
            [dict(raw, id=1, start_dt=str(end), total_cost="2", transaction_amount="4")],
            end,
        ),
        "profile_records": normalize(
            "profile_records",
            [
                dict(__id=1, spitztx="123", spmc="帽子", cpdj="新品"),
                dict(__id=2, spitztx="456", spmc="无经营新品", cpdj="新品"),
            ],
            end,
        ),
    }
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        q = BoardQuery(repo.start, repo.end)
        for scope in (False, True):
            directory = options(repo, q, new_only=scope)
            assert directory["shops"] == [dict(value=SID, label=NEW)]
        cards = score_cards(repo, q)["items"]
        assert len(cards) == 2
        assert {r["shop_name"] for r in cards} == {NEW}
        assert {r["listing_id"] for r in cards} == {identity(SID, "123"), identity(SID, "456")}
        assert overview(repo, q)["metrics"]["revenue"]["value"] == Decimal("23.17")
    with promotion_snapshot(sales_pg, "product") as repo:
        q = PromotionQuery(end, end, mode="product")
        assert (
            next(s for s in promotion_options(repo, q)["shops"] if s["value"] == SID)["label"]
            == NEW
        )
        assert all(NEW in r["label"] for r in promotion_options(repo, q)["products"])
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT shop_name FROM shops WHERE id=:id"), dict(id=SID)
            ).scalar_one()
            == OLD
        )
        assert (
            c.execute(sa.text("SELECT source_shop_name FROM product_daily_facts")).scalar_one()
            == OLD
        )


def test_real_pg_store_promotion_days_use_current_name_for_old_fact(sales_pg, tmp_path):
    from app.modules.promotion.query import PromotionQuery, days
    from app.modules.promotion.repository import snapshot
    from app.modules.sales.storage import ShopDailyStore

    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        changes = (
            dict(shop_id=SID, shop_name=OLD) if code == "shop_channel_product_promotion" else {}
        )
        c = normalize_source(code, [sample(code, **changes)], window())
        ShopDailyStore(
            sales_pg, archive_root=tmp_path, approved=window(), spec=SPECS[code]
        ).publish_candidate(c)
    with snapshot(sales_pg, "store") as repo:
        result = days(repo, PromotionQuery(date(2026, 10, 26), date(2026, 10, 26), shop=SID))
        assert len(result["items"]) == 1
        assert result["items"][0]["shop_name"] == NEW


def test_jst_latest_and_paged_read_bind_all_aliases_and_preserve_new_rows():
    from datetime import datetime
    from unittest.mock import MagicMock

    from app.connectors.aftersales import AftersalesReader
    from app.connectors.retry import RetryBudget

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.connection = MagicMock()
    reader.heartbeat = lambda: None
    reader.retry_budget = RetryBudget()
    cursor = reader.connection.cursor.return_value.__enter__.return_value
    scope = dict(row_count=1, min_id=1, max_id=1)
    cursor.fetchone.side_effect = [dict(latest=datetime(2026, 10, 26)), scope, scope]
    raw = dict(__id=1, shop_name=JST_NEW)
    cursor.fetchall.return_value = [raw]
    assert reader.latest("jst_returns") == date(2026, 10, 26)
    assert reader.read("jst_returns", date(2026, 10, 26)) == [raw]
    for call in cursor.execute.call_args_list:
        sql, params = call.args
        assert sql.count("%s") == len(params)
        assert JST_OLD in params and JST_NEW in params


def test_historical_supplement_display_uses_current_name_preserving_evidence():
    from app.modules.sales.promotion_history import supplement_audit

    row = dict(
        shop_id=SID,
        source_shop_name=OLD,
        business_date=date(2026, 2, 21),
        channel_code="product_promotion",
        source_row_key="legacy-inactive/123",
        source_channel_label="原版停投确认记录",
        source_record_id="123",
        spend=Decimal("0"),
        attributed_revenue=Decimal("0"),
    )
    result = supplement_audit([], [row])
    assert result[0]["shop_name"] == NEW
    assert result[0]["status"] == "supplement"
    assert row["source_shop_name"] == OLD


@pytest.mark.parametrize("source_name", [JST_OLD, JST_NEW])
def test_real_pg_fixed_aftersales_report_displays_new_name_without_rewriting_input(
    sales_pg, source_name
):
    from tests.test_aftersales_api import client_for, publish

    with sales_pg.begin() as c:
        c.execute(
            sa.text("INSERT INTO shops(id,shop_name,status) VALUES('spitztx',:name,'active')"),
            dict(name=OLD),
        )
    publish(
        sales_pg,
        returns=[
            dict(
                id="r1",
                source_row_key="r",
                shop_id="spitztx",
                shop_match_status="matched",
                source_shop_id="11273433",
                source_shop_name=source_name,
                actual_returned_qty=Decimal("1.5"),
                actual_inbound_amount=None,
            )
        ],
    )
    with client_for(sales_pg) as client:
        response = client.get("/api/v1/aftersales/analysis?view=style")
        assert response.status_code == 200
        detail = response.json()["data"]["rows"][0]["cells"]["product"]["detail"]
        assert JST_NEW in detail and OLD not in detail
    with sales_pg.connect() as c:
        assert (
            c.execute(sa.text("SELECT source_shop_name FROM return_records")).scalar_one()
            == source_name
        )
