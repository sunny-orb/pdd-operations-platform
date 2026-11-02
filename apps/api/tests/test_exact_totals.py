"""商品/推广共用汇总优化不能丢小数、极大数或真实零。"""

from decimal import Decimal, localcontext

import pytest

from app.modules.product_board.metrics import total


@pytest.mark.parametrize(
    "values,expected",
    [
        (["0.01", "0.02", None], "0.03"),
        (
            ["99999999999999999999999999999999999999999999999999", "0.01"],
            "99999999999999999999999999999999999999999999999999.01",
        ),
        (["1E+70", "0.0000000001", "-1E+70"], "0.0000000001"),
        (["0.00", "0.00"], "0.00"),
        ([None], None),
    ],
)
def test_exact_sum_preserves_all_digits_and_missing(values, expected):
    with localcontext() as ctx:
        ctx.prec = 6
        result = total([{"value": Decimal(x) if x is not None else None} for x in values], "value")
    assert result == (Decimal(expected) if expected is not None else None)
