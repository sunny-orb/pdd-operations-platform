"""规则解释共享领域来源，固定报告规则不被最新配置覆盖。"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.modules.product_board import metrics
from app.modules.product_board.repository import BoardError
from tests.test_aftersales_api import publish

pytest_plugins = ["tests.sales_pg_support"]


def read(engine=None, **params):
    from app.modules.assistant import rule_definitions

    return rule_definitions.read(engine, params)


def rows(result):
    return result["tables"][0]["rows"]


def test_products_definitions_are_bounded_scalar_and_share_risk_thresholds():
    from app.modules.product_board import risk_rules

    result = read(view="products", keyword="products.risk", limit=25)
    assert result["kind"] == "dashboard" and result["domain"] == "definitions"
    assert result["capability"] == "rule_definitions"
    values = {r["id"]: r for r in rows(result)}
    assert len(values) == 5
    assert str(risk_rules.TRAFFIC_MIN) in values["products.risk.zero_orders"]["sample"]
    assert "首" in values["products.risk.conversion_severe"]["exceptions"]
    assert all(r["rule_version"] and r["source"] for r in values.values())
    assert all(not isinstance(v, (dict, list, tuple)) for r in values.values() for v in r.values())
    assert rows(read(view="products", keyword="products.risk", offset=3, limit=1)) == [
        rows(result)[3]
    ]


def test_risk_threshold_source_changes_both_explanation_and_computation(monkeypatch):
    from app.modules.product_board import risk_rules

    end = date(2026, 11, 23)
    data = [
        dict(
            business_date=end - timedelta(days=i),
            visitors=150,
            orders=10,
            product_revenue=Decimal("200"),
        )
        for i in range(8)
    ]
    data[0]["orders"] = 0
    assert metrics.risk(data, end, False)["label"] == "有流量无成交"
    monkeypatch.setattr(risk_rules, "TRAFFIC_MIN", 200)
    assert metrics.risk(data, end, False) is None
    assert "200" in rows(read(view="products", keyword="zero_orders"))[0]["sample"]


@pytest.mark.parametrize(
    "params",
    [
        dict(view="bad"),
        dict(limit=26),
        dict(offset=-1),
        dict(limit=True),
        dict(report_id=123),
        dict(view="products", report_id="history"),
        dict(keyword=["风险"]),
        dict(unrecognized="value"),
    ],
)
def test_invalid_scope_is_rejected_before_database(params):
    with pytest.raises(BoardError) as error:
        read(**params)
    assert error.value.status == 422


def test_promotion_store_has_no_product_sop_and_sales_rating_is_source_score():
    store = rows(read(view="promotion_store", keyword="diagnosis"))
    assert len(store) == 1 and store[0]["state"] == "not_defined"
    product = rows(read(view="promotion_product", keyword="roi_drop", limit=25))
    assert len(product) == 2
    assert all(
        "300" in r["sample"] and "-15" in r["condition"] and "%" in r["condition"] for r in product
    )
    rating = rows(read(view="sales", keyword="rating"))[0]
    assert "100" in rating["condition"] and "未定义" in rating["exceptions"]


def test_aftersales_uses_selected_report_frozen_thresholds_without_fact_reads(sales_pg):
    from app.modules.aftersales.classification import rule_parameters

    old = rule_parameters(risk_available=True)
    old["branches"][0]["thresholds"]["attention_change_gte"] = "0.07"
    original = deepcopy(old)
    publish(sales_pg, suffix="old", rules=old)
    publish(
        sales_pg, suffix="new", end=date(2026, 11, 1), rules=rule_parameters(risk_available=True)
    )
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    sa.event.listen(sales_pg, "before_cursor_execute", capture)
    try:
        result = read(sales_pg, view="aftersales", report_id="reportold", limit=25)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", capture)
    item = next(r for r in rows(result) if r["id"] == "aftersales.refund_rate_change")
    assert "0.07" in item["condition"] and "0.03" not in item["condition"]
    assert item["rule_version"] == "rules-old"
    assert result["scope"]["report_id"] == "reportold"
    assert not any(
        "from public.aftersales_return_records" in s or "from public.aftersales_shipped_sales" in s
        for s in statements
    )
    assert old == original


def test_aftersales_missing_package_does_not_fall_back(sales_pg):
    publish(sales_pg)
    result = read(sales_pg, view="aftersales", limit=25)
    assert any(
        r["id"] == "aftersales.rule_package" and r["state"] == "not_defined" for r in rows(result)
    )
    assert not any(r["id"] == "aftersales.refund_rate_change" for r in rows(result))
    assert rows(read(sales_pg, view="aftersales", keyword="aftersales.total_rate"))


def test_default_all_uses_domains_without_inventing_overview_thresholds():
    result = read(limit=25)
    assert result["scope"]["view"] == "all"
    assert any("运营中心" in note and "复用" in note for note in result["notes"])
    assert result["tables"][0]["total"] > 25


def test_promotion_formulas_reuse_domain_ratio_specs():
    result = read(view="promotion_store", keyword="formula", limit=25)
    assert len(rows(result)) == 5
    roi = next(r for r in rows(result) if r["id"] == "promotion_store.formula.roi")
    assert "交易额" in roi["condition"] and "总花费" in roi["condition"]
    assert "严格" in roi["exceptions"]


def test_aftersales_targets_read_only_report_bound_range(sales_pg, tmp_path):
    from app.modules.aftersales.rpa_publisher import sync_rpa
    from app.modules.aftersales.targets import publish_targets
    from tests.test_aftersales_rpa import delivery
    from tests.test_aftersales_targets import config

    publish_targets(sales_pg, config(), tmp_path)
    sync_rpa(sales_pg, lambda: delivery(), tmp_path)
    selected = read(sales_pg, view="aftersales", keyword="aftersales.target.", limit=25)
    assert len(rows(selected)) == 2
    assert all("上界=" in r["condition"] and "整体" in r["sample"] for r in rows(selected))
    before = deepcopy(rows(selected))
    changed = config()
    changed["total_refund_rate"]["upper"] = "0.48"
    publish_targets(sales_pg, changed, tmp_path)
    again = read(
        sales_pg,
        view="aftersales",
        report_id=selected["scope"]["report_id"],
        keyword="aftersales.target.",
        limit=25,
    )
    assert rows(again) == before


def test_all_without_report_keeps_other_definitions_and_explicit_missing_package(sales_pg):
    result = read(sales_pg, limit=25)
    assert result["tables"][0]["total"] > 25
    missing = read(sales_pg, view="aftersales", keyword="rule_package")
    assert rows(missing)[0]["state"] == "not_defined"


def test_product_rule_canonical_version_is_shared_with_risk_result():
    from app.modules.product_board import risk_rules

    result = read(view="products", keyword="products.risk", limit=25)
    assert {item["rule_version"] for item in rows(result)} == {risk_rules.RULE_VERSION}
    assert result["versions"]["rules_products"] != risk_rules.RULE_VERSION
    scope = rows(read(view="products", keyword="products.scope"))[0]
    assert scope["rule_version"] == risk_rules.RULE_VERSION
    assert "所选经营期金额" in scope["condition"]
    assert "近7日金额" not in scope["condition"]
    score = rows(read(view="products", keyword="products.score.policy"))[0]
    assert "不是" in score["exceptions"] and "冻结" in score["exceptions"]


def test_risk_definition_window_text_uses_shared_baseline_days(monkeypatch):
    from app.modules.product_board import risk_rules

    monkeypatch.setattr(risk_rules, "BASELINE_DAYS", 9)
    severe = rows(read(view="products", keyword="products.risk.traffic_severe"))[0]
    assert "前9日订单" in severe["condition"]
    scope = rows(read(view="products", keyword="products.scope"))[0]
    assert "同9日金额" in scope["exceptions"]


def test_risk_result_metadata_uses_shared_version_and_window(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import query, risk_rules
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    end, data = candidate(duplicate_profile=False)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    monkeypatch.setattr(risk_rules, "RULE_VERSION", "synthetic-risk-version", raising=False)
    monkeypatch.setattr(risk_rules, "BASELINE_DAYS", 9)
    with snapshot(sales_pg) as repo:
        result = query.risk_feed(repo, query.BoardQuery(end - timedelta(days=6), end))
    assert result["rule_version"] == "synthetic-risk-version"
    assert result["baseline_period"] == dict(
        start=end - timedelta(days=9), end=end - timedelta(days=1)
    )
    assert result["trend_period"] == dict(start=end - timedelta(days=8), end=end)
