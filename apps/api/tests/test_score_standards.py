"""显示标准入口校验；不把显示四线误当历史生成程序验收。"""

from copy import deepcopy

import pytest

from app.modules.products.ingestion import IngestionError
from app.modules.products.score_standards import display_parameters, validate_parameters
from app.modules.products.scoring import METRICS


def document():
    return dict(
        families={
            "f": dict(
                reference="合成参照",
                counts=[24] * 10,
                lines=[
                    ["1%", "2%", "3%", "4%"] if m[2] == "ratio" else ["1", "2", "3", "4"]
                    for m in METRICS.values()
                ],
            )
        },
        products=[["123", "帽子", "spigf", "帽子", "平季", "放量期", "10", "f"]],
    )


def test_display_percent_converted_to_ratio_and_precision_preserved():
    d = document()
    d["families"]["f"]["lines"][1] = ["1.123456789", "2", "3", "4"]
    p = display_parameters(d)
    validate_parameters(p)
    signals = p["products"]["pdd:663599807:123"]["metrics"]
    assert signals["ad_click_through_rate_7d"]["thresholds"]["basic"] == "0.01"
    assert signals["product_revenue_7d"]["thresholds"]["basic"] == "1.123456789"


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "float_identity",
        "wrong_unit",
        "decreasing",
        "missing_metric",
        "nan",
        "bad_count",
    ],
)
def test_invalid_standard_is_not_importable(kind):
    d = deepcopy(document())
    if kind == "duplicate":
        d["products"] *= 2
    if kind == "float_identity":
        d["products"][0][0] = 123.0
    if kind == "wrong_unit":
        d["families"]["f"]["lines"][4] = ["1", "2", "3", "4"]
    if kind == "decreasing":
        d["families"]["f"]["lines"][0] = ["2", "1", "3", "4"]
    if kind == "missing_metric":
        d["families"]["f"]["lines"].pop()
    if kind == "nan":
        d["families"]["f"]["lines"][0] = ["NaN", "2", "3", "4"]
    if kind == "bad_count":
        d["families"]["f"]["counts"][0] = 1.2
    with pytest.raises(IngestionError):
        validate_parameters(display_parameters(d))
