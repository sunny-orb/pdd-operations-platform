from datetime import date
from decimal import Decimal

import pytest

from app.modules.product_board.source import normalize, parse_ids, windows


def row(**changes):
    value = dict(
        z_id=1,
        shop_id="663599807",
        spu_id="9007199254740993",
        dt="2026-10-26 00:00:00",
        time_granularity="day",
        transaction_amount="12.34000000000001",
        product_uv=0,
    )
    value.update(changes)
    return value


def test_strict_ids_no_digit_guess():
    assert parse_ids("123 / 456\n789；012") == ("123", "456", "789", "012")
    for value in ["下架123", "123,456", "1e12", "1.0"]:
        assert parse_ids(value) == ()


@pytest.mark.parametrize(
    "note",
    [
        "下架",
        "替换",
        "新链接",
        "旧链接",
        "拼链接",
        "拼老链接",
        "成人",
        "儿童",
        "背心链接",
        "替换链接",
        "复购",
    ],
)
def test_id_notes_preserve_other_numeric_rows_without_guessing(note):
    assert parse_ids(f"123\n\n456-{note}") == ("123", "456")
    assert parse_ids(f"123\n\n456-\n\n{note}") == ("123", "456")
    assert parse_ids(f"123\n\n456\n\n-{note}") == ("123", "456")
    assert parse_ids(f"456{note}") == ("456",)


def test_id_parser_keeps_clear_rows_but_rejects_urls_decimals_and_arbitrary_text():
    assert parse_ids("123\n无法识别789\n456") == ("123", "456")
    assert parse_ids("123\nhttps://example.com/789\n456") == ("123", "456")
    assert parse_ids("123 / 已下架") == ("123",)
    assert parse_ids("限量购/456复购") == ("456",)
    for value in [
        "https://example.com/123",
        "订单号123",
        "123-2025",
        "123-未知789",
        "123.456",
        "123e456",
        "123/未知456",
    ]:
        assert parse_ids(value) == ()


def test_name_consensus_merges_only_complete_trailing_variant_suffixes():
    from app.modules.product_board.profile_rules import resolve_product_name

    assert resolve_product_name(["商品甲（拉链款）", "商品甲（按扣款）"]) == "商品甲"
    assert resolve_product_name(["商品甲", "商品甲【帽子+手套】"]) == "商品甲"
    assert resolve_product_name(["商品甲【蓝色】（大号）", "商品甲【红色】（小号）"]) == "商品甲"
    assert resolve_product_name([" 【新品】商品甲 "]) == "【新品】商品甲"
    for names in [
        ["商品甲（大号）", "商品乙（小号）"],
        ["商品甲（大号", "商品甲"],
        ["商品甲（冬）加绒", "商品甲（夏）薄款"],
        ["甲（大）", "甲（小）"],
        [],
    ]:
        assert resolve_product_name(names) is None


def test_projection_preserves_null_exact_money_and_identity():
    result = normalize("product_daily", [row()], date(2026, 10, 26))
    assert result[0]["product_revenue"] == Decimal("12.34000000000001")
    assert result[0]["visitors"] == 0 and result[0]["buyers"] is None
    assert result[0]["source_product_id"] == "9007199254740993"
    assert result[0]["business_date"] == date(2026, 10, 26)


def test_reject_duplicates_invalid_scope_or_fractional_counter():
    for values in [
        [row(), row()],
        [row(product_pv="1.5")],
        [row(shop_name="ok", product_uv="NaN")],
    ]:
        with pytest.raises(ValueError):
            normalize("product_daily", values, date(2026, 10, 26))
    assert windows(date(2026, 10, 26)) == (
        (date(2026, 8, 28), date(2026, 10, 26)),
        (date(2025, 8, 28), date(2025, 10, 26)),
    )
