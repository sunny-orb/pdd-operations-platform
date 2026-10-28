"""用户区间目标：不取中值、不混金额/件数、固定报告输入及真实PG权限。"""

from decimal import Decimal

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]


def config():
    return {
        "application_policy_code": "rolling_30d_and_natural_month",
        "total_refund_rate": {"lower": "0.40", "upper": "0.43"},
        "aftersale_refund_rate": {"lower": "0.32", "upper": "0.36"},
    }


def test_exact_range_and_no_midpoint_or_quantity_rate():
    from app.modules.aftersales.targets import describe_range, normalize_config

    rows = normalize_config(config())
    assert len(rows) == 2
    assert rows[0]["lower_rate"] == Decimal("0.40")
    assert rows[0]["upper_rate"] == Decimal("0.43")
    assert rows[1]["metric_code"] == "aftersale_refund_rate"
    result = describe_range(rows[0], Decimal("0.45"))
    assert result["state"] == "above_range"
    assert result["lower_gap_pp"] == Decimal("5")
    assert result["upper_gap_pp"] == Decimal("2")
    assert describe_range(rows[0], None)["state"] == "metric_unavailable"
    assert describe_range(rows[0], Decimal("0.39"))["state"] == "below_range"
    assert describe_range(rows[0], Decimal("0.43"))["state"] == "within_range"
    invalid = config()
    invalid["aftersale_refund_rate"]["lower"] = "0.50"
    with pytest.raises(ValueError):
        normalize_config(invalid)
    invalid = config()
    invalid["quantity_return_rate"] = {"lower": "0.32", "upper": "0.36"}
    with pytest.raises(ValueError):
        normalize_config(invalid)


def test_range_policy_needs_confirmed_bound_target_input():
    from app.modules.aftersales.classification import branch_enabled, rule_parameters

    assert not branch_enabled(rule_parameters(), "target_application")
    assert branch_enabled(rule_parameters(targets_available=True), "target_application")


def test_pg_target_publish_repeat_and_report_binding(sales_pg, tmp_path):
    from datetime import date

    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source
    from app.modules.aftersales.targets import publish_targets
    from tests.test_aftersales_api import client_for
    from tests.test_aftersales_ingestion import sample

    target = publish_targets(sales_pg, config(), tmp_path)
    assert publish_targets(sales_pg, config(), tmp_path)["version_id"] == target["version_id"]
    receive = sample(date(2026, 10, 27))
    for code in FACTS:
        publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)
    publish_reports(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        first = client.get("/api/v1/aftersales/overview").json()
        report_id = first["data"]["report"]["id"]
        ranges = first["data"]["target_ranges"]
        assert [x["metric_code"] for x in ranges] == ["total_refund_rate", "aftersale_refund_rate"]
        assert ranges[0]["lower_rate"] == "0.40" and ranges[0]["upper_rate"] == "0.43"
        assert ranges[1]["lower_rate"] == "0.32" and ranges[1]["upper_rate"] == "0.36"
        assert all(x["current"] is None and x["state"] == "metric_unavailable" for x in ranges)
        changed = config()
        changed["total_refund_rate"]["upper"] = "0.44"
        publish_targets(sales_pg, changed, tmp_path)
        old = client.get(f"/api/v1/aftersales/overview?report_id={report_id}").json()
        assert old["data"]["target_ranges"][0]["upper_rate"] == "0.43"
        publish_reports(sales_pg, tmp_path)
        new = client.get("/api/v1/aftersales/overview").json()
        assert new["data"]["report"]["id"] != report_id
        assert new["data"]["target_ranges"][0]["upper_rate"] == "0.44"
        filtered = client.get("/api/v1/aftersales/overview?focus=quality").json()["data"]
        assert all(x["state"] == "scope_not_applicable" for x in filtered["target_ranges"])
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM refund_targets")) == 4
        assert (
            c.scalar(sa.text("SELECT count(*) FROM version_inputs WHERE input_role='targets'")) >= 3
        )
