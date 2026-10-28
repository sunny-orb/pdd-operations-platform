"""金额观察与本地适用状态；不以已知小计填补正式金额或推定来源故障。"""

from decimal import Decimal

import pytest

pytest_plugins = ["tests.sales_pg_support"]


def test_observation_distinguishes_absent_empty_null_and_real_zero():
    from app.modules.aftersales.amounts import assess_amount

    assert assess_amount(None, "amount").state == "not_received"
    assert assess_amount([], "amount").state == "no_records"
    blank = assess_amount([{"amount": None}], "amount")
    assert blank.state == "field_null" and blank.known_subtotal is None
    zero = assess_amount([{"amount": Decimal(0)}], "amount")
    assert zero.state == "observed" and zero.complete_value == Decimal(0)
    assert zero.zero_rows == 1 and zero.null_rows == 0


def test_partial_observation_keeps_known_subtotal_separate_from_complete_value():
    from app.modules.aftersales.amounts import assess_amount

    result = assess_amount(
        [{"amount": Decimal("100.001")}, {"amount": None}, {"amount": Decimal("-5.001")}],
        "amount",
    )
    assert result.rows == 3 and result.known_rows == 2 and result.null_rows == 1
    assert result.known_subtotal == Decimal("95.000")
    assert result.complete_value is None and result.state == "field_null"


@pytest.mark.parametrize("value", [1.5, True, "3", Decimal("NaN"), Decimal("Infinity")])
def test_observation_rejects_non_decimal_or_nonfinite_inputs(value):
    from app.modules.aftersales.amounts import assess_amount

    with pytest.raises(ValueError, match="AMOUNT_DECIMAL_REQUIRED"):
        assess_amount([{"amount": value}], "amount")


def test_details_do_not_describe_local_mapping_null_as_upstream_data_loss():
    from app.modules.aftersales.amounts import amount_details

    details = amount_details(
        [{"actual_inbound_amount": None}],
        [{"sales_amount": Decimal("90")}, {"sales_amount": None}],
        [{"sales_amount": Decimal("10")}],
    )
    assert "映射待核" in details["inbound_amount"]
    assert "来源收货金额为空" not in details["inbound_amount"]
    assert "90.00" in details["gmv"] and "1条" in details["gmv"]
    assert "观察小计" in details["gmv"] and "适用待核" in details["gmv"]
    assert "A" in details["total_rate"] and "S" in details["total_rate"]


def test_unmapped_inbound_text_states_received_records_without_claiming_source_blanks():
    from app.modules.aftersales.amounts import amount_details

    detail = amount_details([{"actual_inbound_amount": None}], [], [])["inbound_amount"]
    assert "已接入1条退货记录" in detail
    assert "本地金额为空" not in detail
    assert "映射待核" in detail


def test_details_respect_empty_filter_and_preserve_zero_amount():
    from app.modules.aftersales.amounts import amount_details

    assert "无退货记录" in amount_details([], [], [])["inbound_amount"]
    complete = amount_details(
        [{"actual_inbound_amount": Decimal(0)}],
        [{"sales_amount": Decimal(0)}],
        [{"sales_amount": Decimal(0)}],
    )
    assert "映射待核" not in complete["inbound_amount"]
    assert "0.00" in complete["gmv"]


def test_presale_rate_detail_does_not_depend_on_inbound_mapping():
    from app.modules.aftersales.amounts import amount_details

    details = amount_details(
        [{"actual_inbound_amount": None}],
        [{"sales_amount": Decimal("90")}],
        [{"sales_amount": Decimal("10")}],
    )
    assert "暂不可算" not in details["presale_rate"]
    assert "A" not in details["presale_rate"]
    assert "A" in details["aftersale_rate"]


def test_fixed_pg_report_exposes_mapping_detail_without_releasing_amount_gate(sales_pg):
    from tests.test_aftersales_api import client_for, publish

    publish(sales_pg, missing=True)
    with client_for(sales_pg) as client:
        response = client.get("/api/v1/aftersales/overview")
        assert response.status_code == 200
        metrics = {item["key"]: item for item in response.json()["data"]["metrics"]}
        assert metrics["inbound_amount"]["value"] is None
        assert metrics["total_rate"]["value"] is None
        assert "映射待核" in metrics["inbound_amount"]["detail"]
        assert "110.00" in metrics["gmv"]["detail"]
        empty = client.get("/api/v1/aftersales/overview?keyword=不存在的合成款")
        values = {item["key"]: item for item in empty.json()["data"]["metrics"]}
        assert "无退货记录" in values["inbound_amount"]["detail"]
        assert "110.00" not in values["gmv"]["detail"]
