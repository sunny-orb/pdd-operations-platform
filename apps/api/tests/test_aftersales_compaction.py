"""真实PG等价行压缩：原行数量、NULL、分类及完整对象不能被压缩改变。"""

from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from app.modules.aftersales.amounts import assess_amount
from app.modules.aftersales.classification import observations, rule_parameters
from app.modules.aftersales.metrics import summarize
from app.modules.aftersales.repository import (
    COMMON_FIELDS,
    IDENTITY_FIELDS,
    ROLES,
    Repository,
    snapshot,
)
from migrations_v2.baseline_20261028.schema import build_metadata
from tests.test_aftersales_api import client_for, publish

pytest_plugins = ["tests.sales_pg_support"]


def test_numeric_grouping_does_not_reclassify_each_object(monkeypatch):
    from app.modules.aftersales import metrics

    calls = []
    original = metrics.observations

    def observe(rows, parameters):
        calls.append(rows)
        return original(rows, parameters)

    monkeypatch.setattr(metrics, "observations", observe)
    row = dict(
        source_product_category="帽子",
        actual_returned_qty=Decimal("2"),
        actual_inbound_amount=None,
        source_return_reason="质量问题",
    )
    result = metrics.groups([[row], [], []], "category", rule_parameters())
    assert result[0]["metrics"]["returned_qty"] == Decimal("2")
    assert result[0]["metrics"]["inbound_amount"] is None
    assert calls == []  # 分组只消费金额/件数；分类在对应卡片或表格投影时计算。


def test_same_identity_reason_compacts_without_losing_counts_or_null_evidence(sales_pg):
    rows = [
        dict(
            id=f"r{i}",
            source_row_key=f"r{i}",
            actual_returned_qty=Decimal("1.5"),
            actual_inbound_amount=amount,
            source_return_reason="尺码偏大也偏小，质量问题破损",
            source_aftersale_type="拒收退货",
        )
        for i, amount in enumerate([Decimal("0"), Decimal("2.50"), None])
    ]
    rows += [
        dict(rows[0], id="missing1", source_row_key="missing1", source_return_reason=None),
        dict(rows[0], id="missing2", source_row_key="missing2", source_return_reason=None),
    ]
    publish(
        sales_pg, returns=rows, rules=rule_parameters(), source_revision="m5-jst-current-sku-v2"
    )
    with snapshot(sales_pg) as repo:
        block = repo.rows("j1_returns")
        assert len(block) == 2  # 同身份同原因同售后分类只传一次，原行数另存。
        values = summarize(
            block,
            repo.rows("j2_shipped_sales"),
            repo.rows("j3_cancelled_sales"),
            repo.classification_parameters(),
        )
        assert values["records"] == "5"
        assert values["returned_qty"] == Decimal("7.5")
        assert values["size_qty"] == Decimal("4.5")
        assert values["quality_qty"] == Decimal("4.5")
        assert values["inbound_amount"] is None and values["total_rate"] is None
        amount = assess_amount(block, "actual_inbound_amount")
        assert (amount.rows, amount.known_rows, amount.null_rows, amount.zero_rows) == (5, 4, 1, 3)
        assert amount.known_subtotal == Decimal("2.50") and amount.complete_value is None
        observed = observations(block, repo.classification_parameters())
        assert "未填写原因2/5行" in observed["note"]
        assert "尺码方向冲突3行" in observed["note"]
        assert [item["qty"] for item in observed["quality_rows"]] == [
            Decimal("4.5"),
            Decimal("4.5"),
        ]


def raw_rows(repo, role, report=None):
    """独立逐原行SQL对照；不复用聚合查询或内部辅助字段。"""
    report = report or repo.report
    if not repo.source_available(role, report):
        return None
    source = repo.inputs(report)[role]
    fields = (
        (
            *COMMON_FIELDS,
            "actual_returned_qty",
            "actual_inbound_amount",
            "source_return_reason",
            "source_aftersale_type",
        )
        if role == "j1_returns"
        else (*COMMON_FIELDS, "sales_amount", "source_sales_qty")
    )
    return [
        dict(row)
        for row in repo.c.execute(
            sa.text(
                f"SELECT {','.join(fields)} FROM public.{ROLES[role][1]} "
                "WHERE dataset_version_id=:vid AND row_kind='detail' "
                "AND period_start>=:start AND period_end<=:end "
                "AND period_start IS NOT NULL AND period_end IS NOT NULL ORDER BY source_row_key"
            ),
            dict(vid=source["id"], start=source["window_start"], end=source["window_end"]),
        ).mappings()
    ]


def test_full_api_projection_matches_raw_rows_for_all_dimensions_and_missing_evidence(
    sales_pg, monkeypatch
):
    base = dict(
        source_return_reason="尺码偏大，质量问题破损",
        actual_returned_qty=Decimal("1.234567890123456789"),
        actual_inbound_amount=Decimal("123456789012345678901234567890.123456789012"),
        source_aftersale_type="拒收退货",
    )
    rows = []
    variants = [
        {},
        {},
        {"actual_inbound_amount": None},
        {"actual_inbound_amount": Decimal("0")},
        {"source_return_reason": None},
        {"source_return_reason": ""},
        {"source_return_reason": "   "},
        {"source_return_reason": "不是质量问题"},
        {"source_return_reason": "尺码偏大偏小"},
        {"source_aftersale_type": None},
        {"actual_returned_qty": None},
        {"source_product_category": None},
    ]
    variants += [{field: "另一原生身份"} for field in IDENTITY_FIELDS]
    for i, variant in enumerate(variants):
        rows.append(dict(base, id=f"r{i:03}", source_row_key=f"r{i:03}", **variant))
    publish(
        sales_pg, returns=rows, rules=rule_parameters(), source_revision="m5-jst-current-sku-v2"
    )
    cases = [
        ("overview", q)
        for q in [
            {},
            {"category": "帽子"},
            {"keyword": "质量"},
            {"keyword": "未命中"},
            {"focus": "size"},
            {"focus": "quality"},
        ]
    ]
    cases += [
        ("analysis", {"view": view, "size": 100})
        for view in ["category", "style", "quality", "history", "mom", "yoy"]
    ]
    cases.append(("options", {}))
    with client_for(sales_pg) as client:
        compact = [
            client.get("/api/v1/aftersales/" + path, params=q).json()["data"] for path, q in cases
        ]
        monkeypatch.setattr(Repository, "rows", raw_rows)
        original = [
            client.get("/api/v1/aftersales/" + path, params=q).json()["data"] for path, q in cases
        ]
    assert compact == original


def test_unbounded_numeric_cancellation_keeps_original_row_arithmetic(sales_pg, monkeypatch):
    rows = [
        dict(
            id=f"r{i}",
            source_row_key=f"r{i}",
            actual_returned_qty=Decimal(value),
            actual_inbound_amount=None,
            source_return_reason="尺码偏大质量问题",
            source_aftersale_type="拒收退货",
        )
        for i, value in enumerate(["1E+70", "0.0000000001", "-1E+70"])
    ]
    publish(
        sales_pg, returns=rows, rules=rule_parameters(), source_revision="m5-jst-current-sku-v2"
    )
    with client_for(sales_pg) as client:
        paths = [
            "overview",
            "analysis?view=category",
            "analysis?view=style",
            "analysis?view=history",
        ]
        compact = [client.get("/api/v1/aftersales/" + path).json()["data"] for path in paths]
        monkeypatch.setattr(Repository, "rows", raw_rows)
        original = [client.get("/api/v1/aftersales/" + path).json()["data"] for path in paths]
    assert compact == original


def test_default_trend_reads_only_returns_and_keeps_history_without_sales(sales_pg, monkeypatch):
    publish(sales_pg, end=date(2026, 10, 26))
    publish(sales_pg, suffix="2")
    tables = build_metadata().tables
    versions = tables["public.dataset_versions"]
    with sales_pg.begin() as connection:
        connection.execute(
            versions.insert().values(
                id="return-only-version",
                dataset_code="aftersales_report",
                origin_kind="derived",
                state="draft",
                row_schema_revision="m5-test",
                interpretation_ref="synthetic:history",
                producer_revision="synthetic:test",
                period_start=date(2026, 9, 28),
                period_end=date(2026, 10, 26),
            )
        )
        connection.execute(
            tables["public.version_inputs"]
            .insert()
            .values(
                output_version_id="return-only-version",
                input_role="j1_returns",
                input_version_id="jst_returns1",
                window_start=date(2026, 9, 28),
                window_end=date(2026, 10, 26),
            )
        )
        connection.execute(
            tables["public.aftersales_reports"]
            .insert()
            .values(
                id="return-only",
                dataset_version_id="return-only-version",
                report_type="custom",
                period_start=date(2026, 9, 28),
                period_end=date(2026, 10, 26),
                revision_no=2,
                source_generated_time_status="unknown",
                previous_comparison_status="missing",
                previous_comparison_reason="合成无上期",
                yoy_comparison_status="missing",
                yoy_comparison_reason="合成无去年",
            )
        )
        connection.execute(
            versions.update()
            .where(versions.c.id == "return-only-version")
            .values(
                state="validated",
                content_fingerprint="synthetic:return-only",
                validation_evidence_ref="synthetic:verified",
            )
        )
        connection.execute(
            versions.update()
            .where(versions.c.id == "return-only-version")
            .values(
                state="published",
            )
        )

    calls = []
    original = Repository.source_summary

    def recorded(repo, role, report=None):
        calls.append(((report or repo.report)["id"], role))
        return original(repo, role, report)

    monkeypatch.setattr(Repository, "source_summary", recorded)
    with client_for(sales_pg) as client:
        response = client.get("/api/v1/aftersales/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(item["id"], item["qty"]) for item in body["data"]["trend"]] == [
        ("return-only", "4.0"),
        ("report2", "4.0"),
    ]
    assert [role for report, role in calls if report == "return-only"] == ["j1_returns"]
    assert {role for report, role in calls if report == "report2"} == set(ROLES)
    assert len(body["meta"]["sources"]) == 3
