"""整页来源反例：合成输入，不访问上游。"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.connectors import sales_sources
from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import SHOPS


def sample(code, **changes):
    row = dict(
        id="raw-001",
        dt="2026-10-26",
        time_granularity="day",
        shop_id="663599807",
        shop_name=SHOPS["663599807"][1],
        updated_at="2026-10-27 06:00:00",
    )
    if code == "shop_ratings":
        row.update(shop_rating_score=Decimal("0.9876"), create_time="2026-10-27 06:00:00")
    elif code == "shop_channel_product_promotion":
        row["metrics"] = {
            "总花费(元)": "10.20",
            "交易额(元)": "99.99",
            "净交易额(元)": "80.80",
            "成交笔数": "3",
            "曝光量": "1000",
            "点击量": "10",
        }
    elif code == "shop_channel_star_shop":
        row.update(
            cost=Decimal("0.00"),
            transaction_amount=Decimal("0"),
            transaction_count=Decimal("0"),
            impression_count=Decimal("0"),
            click_count=Decimal("0"),
            create_time="2026-10-27 06:00:00",
        )
    elif code == "shop_regions":
        row.update(
            province="浙江",
            transaction_amount=Decimal("100.01"),
            transaction_orders=Decimal("2"),
            transaction_users=Decimal("2"),
            price_per_buyer=Decimal("50.005"),
            price_per_order=Decimal("50.005"),
        )
    elif code == "product_regions":
        row.update(
            province="浙江",
            style_code="001A",
            product_code="00002",
            product_name="合成雨具",
            product_category="雨具",
            virtual_category="日用品",
            color_specification="蓝色",
            actual_shipped_amount=Decimal("100.01000000000001"),
            actual_shipped_qty=Decimal("2.5"),
            actual_returned_amount=None,
            actual_returned_qty=Decimal("0"),
            refund_rate=None,
        )
    elif code == "shop_refunds":
        row = {
            "record_id": "rec001",
            "日期": "2026-10-26T00:00:00+08:00",
            "平台": "拼多多",
            "部门": "市场部",
            "店铺": "kk树-kocotree官方旗舰店-配饰",
            "统计退款金额": Decimal("10.20"),
            "统计金额": Decimal("20.40"),
            "退款占比": "0.5",
        }
    row.update(changes)
    return row


def window():
    return ReadWindow(date(2026, 2, 1), date(2026, 10, 27), tuple(SHOPS))


def test_product_regions_uses_larger_limit_without_changing_other_sources(monkeypatch):
    monkeypatch.setattr(sales_sources, "SOURCE_MAX_ROWS", 2)
    monkeypatch.setattr(sales_sources, "PRODUCT_REGION_SOURCE_MAX_ROWS", 4)
    monkeypatch.setattr(sales_sources, "SOURCE_PAGE_SIZE", 2)
    monkeypatch.setattr(sales_sources, "execute_read", lambda *_: None)
    one_day = ReadWindow(date(2026, 10, 26), date(2026, 10, 26), tuple(SHOPS))

    def scan(code, count):
        reader = sales_sources.SalesSourceReader.for_test(None)
        reader.retry_budget = None
        reader.connection = MagicMock()
        cursor = reader.connection.cursor.return_value.__enter__.return_value
        rows = [{"id": str(i)} for i in range(count)]
        cursor.fetchall.side_effect = [rows[i : i + 2] for i in range(0, count + 1, 2)]
        return reader._scan(SPECS[code], one_day)

    assert len(scan("product_regions", 4)) == 4
    with pytest.raises(SalesError, match="SOURCE_ROW_LIMIT"):
        scan("product_regions", 5)
    with pytest.raises(SalesError, match="SOURCE_ROW_LIMIT"):
        scan("shop_regions", 3)


@pytest.mark.parametrize(
    "code",
    [
        "shop_ratings",
        "shop_channel_product_promotion",
        "shop_channel_star_shop",
        "shop_regions",
        "product_regions",
        "shop_refunds",
    ],
)
def test_each_source_preserves_identity_exact_numbers_and_raw_projection(code):
    candidate = normalize_source(code, [sample(code)], window())
    assert len(candidate.rows) == 1 and candidate.rows[0]["shop_id"] == "spigf"
    assert candidate.raw_rows[0]
    assert all(not isinstance(v, float) for v in candidate.rows[0].values())


def test_rating_old_basis_and_null_remain_raw_not_fictitious_scores():
    c = normalize_source(
        "shop_ratings", [sample("shop_ratings", shop_rating_score=Decimal("4.9"))], window()
    )
    assert c.rows[0]["source_score"] == Decimal("4.9")
    assert c.rows[0]["source_created_at"] is None
    assert c.rows[0]["rating_date"] == date(2026, 10, 26)


def test_two_promotion_channels_have_independent_nullable_metrics():
    p = normalize_source(
        "shop_channel_product_promotion", [sample("shop_channel_product_promotion")], window()
    ).rows[0]
    s = normalize_source(
        "shop_channel_star_shop", [sample("shop_channel_star_shop")], window()
    ).rows[0]
    assert p["spend"] == Decimal("10.20") and p["channel_code"] == "product_promotion"
    assert s["spend"] == 0 and s["net_attributed_revenue"] is None
    assert s["channel_code"] == "star_shop" and s["source_updated_at"] is None


@pytest.mark.parametrize(
    "changes",
    [{"time_granularity": "recent_7d"}, {"shop_id": "unknown"}, {"id": ""}, {"dt": "2026-10-28"}],
)
def test_unapproved_identity_period_or_date_blocks_source(changes):
    with pytest.raises(SalesError):
        normalize_source("shop_regions", [sample("shop_regions", **changes)], window())


def test_extra_star_shop_and_duplicate_rating_do_not_get_arbitrary_resolution():
    with pytest.raises(SalesError):
        normalize_source(
            "shop_channel_star_shop",
            [
                sample(
                    "shop_channel_star_shop", shop_id="799193231", shop_name=SHOPS["799193231"][1]
                )
            ],
            window(),
        )
    with pytest.raises(SalesError):
        normalize_source(
            "shop_ratings", [sample("shop_ratings"), sample("shop_ratings", id="raw-002")], window()
        )


def test_product_regions_keep_fractional_counts_nulls_and_source_labels():
    r = normalize_source("product_regions", [sample("product_regions")], window()).rows[0]
    assert r["actual_shipped_amount"] == Decimal("100.01000000000001")
    assert r["actual_shipped_qty"] == Decimal("2.5")
    assert r["actual_returned_amount"] is None and r["actual_returned_qty"] == 0
    assert r["source_product_code"] == "00002" and r["period_start"] == r["period_end"] == date(
        2026, 10, 26
    )


def test_refund_department_duplicate_and_midday_labels_block_candidate():
    for rows in [
        [sample("shop_refunds", 部门="其他部门")],
        [sample("shop_refunds"), sample("shop_refunds", record_id="rec002")],
        [sample("shop_refunds", 日期="2026-10-26T12:00:00+08:00")],
    ]:
        with pytest.raises(SalesError):
            normalize_source("shop_refunds", rows, window())


def test_same_count_changed_metric_changes_content_fingerprint():
    a = normalize_source("shop_regions", [sample("shop_regions")], window())
    b = normalize_source(
        "shop_regions", [sample("shop_regions", transaction_amount=Decimal("100.02"))], window()
    )
    assert a.fingerprint != b.fingerprint


def test_extended_reader_compares_full_content_with_finite_retry():
    from app.connectors.sales_sources import SalesSourceReader

    one = [sample("shop_regions")]
    other = [sample("shop_regions", transaction_amount=Decimal("200.01"))]
    reads = iter([one, other, one, other])
    reader = SalesSourceReader.for_test(lambda spec, window: next(reads))
    with pytest.raises(SalesError, match="SOURCE_CHANGED"):
        reader.read_stable("shop_regions", window())


def test_feishu_pagination_revision_and_timezone_are_verified():
    from app.connectors.sales_feishu import FeishuRefundReader

    def page(window, offset):
        return [sample("shop_refunds", record_id=f"rec{offset}")], dict(
            records_count=1,
            has_more=offset == 0,
            next_offset=1,
            rev=10 + offset,
            timezone="Asia/Shanghai",
            base_token="XGzTbd9xJaASV3soYnycMmf0nY3",
            table_id="tblISBHrFF4jFJqW",
        )

    reader = FeishuRefundReader.for_test(page)
    with pytest.raises(SalesError, match="FEISHU_REVISION_CHANGED"):
        reader.read_stable(window())


def test_feishu_field_type_changes_are_rejected_before_normalization():
    from app.connectors.sales_feishu import validate_column_types
    from app.modules.sales.datasets import REFUND_FIELDS

    kinds = ("datetime", "text", "text", "text", "number", "number", "formula")
    columns = {
        str(i): dict(field_id=key, field_type=kind)
        for i, (key, kind) in enumerate(zip(REFUND_FIELDS, kinds, strict=True))
    }
    validate_column_types(columns)
    columns["4"]["field_type"] = "text"
    with pytest.raises(SalesError, match="FEISHU_FIELD_SCHEMA_CHANGED"):
        validate_column_types(columns)
