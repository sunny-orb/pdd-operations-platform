"""推广精确累计复用：计算预算及缺项、strict、极高精度行为不回退。"""

from decimal import Decimal, localcontext

import pytest

from app.modules.promotion import metrics


def row(spend, revenue, *, clicks="10", impressions="100", deals="2"):
    return {
        field: None if value is None else Decimal(value)
        for field, value in dict(
            spend=spend,
            attributed_revenue=revenue,
            net_attributed_revenue=revenue,
            attributed_deals=deals,
            clicks=clicks,
            impressions=impressions,
        ).items()
    }


def test_product_aggregation_sums_each_input_field_once(monkeypatch):
    """比率复用精确合计；新增比率不得再次扫描累计相同金额或计数。"""
    fields = []
    original = metrics.total

    def counted_total(rows, field):
        fields.append(field)
        return original(rows, field)

    monkeypatch.setattr(metrics, "total", counted_total)
    result = metrics.product_metrics([row("10", "20"), row(None, "30")])
    assert result["roi"]["value"] == Decimal("5")
    assert sorted(fields) == sorted(metrics.FIELDS.values())


@pytest.mark.parametrize("strict", [False, True])
def test_independent_partial_ratios_keep_raw_totals_before_strict_gate(strict):
    result = metrics.aggregate([row("10", None), row(None, "30")],
                               independent=True, strict=strict)
    assert result["spend"]["value"] == (None if strict else Decimal("10"))
    assert result["revenue"]["value"] == (None if strict else Decimal("30"))
    roi = result["roi"]
    assert roi["numerator"] == Decimal("30")
    assert roi["denominator"] == Decimal("10")
    assert roi["value"] == (None if strict else Decimal("3"))
    assert (roi["status"], roi["valid_rows"], roi["observed_rows"]) == ("partial", "0", "2")


@pytest.mark.parametrize(
    "rows, value, status, numerator, denominator",
    [
        ([], None, "no_records", None, None),
        ([row(None, None)], None, "missing", None, None),
        ([row("10", "0")], Decimal("0"), "observed", Decimal("0"), Decimal("10")),
        ([row("0", "10")], None, "zero_denominator", Decimal("10"), Decimal("0")),
        ([row("-1", "10")], None, "zero_denominator", Decimal("10"), Decimal("-1")),
        ([row("1", "-10")], None, "observed", Decimal("-10"), Decimal("1")),
    ],
)
def test_independent_ratios_keep_missing_zero_and_negative_inputs(
    rows, value, status, numerator, denominator
):
    roi = metrics.product_metrics(rows)["roi"]
    assert (roi["value"], roi["status"], roi["numerator"], roi["denominator"]) == (
        value, status, numerator, denominator
    )


def test_independent_totals_preserve_precision_above_fifty_digits():
    large = "1" + "0" * 60
    rows = [row(large, large), row("0.0000000001", "0.0000000002")]
    with localcontext() as ctx:
        ctx.prec = 90
        spend = Decimal(large) + Decimal("0.0000000001")
        revenue = Decimal(large) + Decimal("0.0000000002")
    result = metrics.product_metrics(rows)
    assert result["spend"]["value"] == spend
    assert result["revenue"]["value"] == revenue
    assert result["roi"]["denominator"] == spend
    assert result["roi"]["numerator"] == revenue


@pytest.mark.parametrize("strict", [False, True])
def test_paired_ratios_still_exclude_unpaired_fields(strict):
    result = metrics.aggregate(
        [row("10", "20"), row(None, "30")], independent=False, strict=strict
    )
    roi = result["roi"]
    assert (roi["numerator"], roi["denominator"]) == (Decimal("20"), Decimal("10"))
    assert roi["value"] == (None if strict else Decimal("2"))
    assert (roi["status"], roi["valid_rows"]) == ("partial", "1")
