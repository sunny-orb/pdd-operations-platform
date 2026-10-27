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
    for value in ["下架123", "123,456", "123 / 已下架", "1e12", "1.0"]:
        assert parse_ids(value) == ()


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
