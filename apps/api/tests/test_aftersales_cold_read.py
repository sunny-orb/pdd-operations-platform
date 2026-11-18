"""RPA冷查询直接读取期间原行；真实PG验证精度、原行计数与缓存等价。"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.api.aftersales import decimal_wire
from app.modules.aftersales.analysis import analysis
from app.modules.aftersales.query import AfterQuery, overview
from app.modules.aftersales.read_cache import ReportRowsCache
from app.modules.aftersales.repository import snapshot
from app.modules.aftersales.rpa_ingestion import normalize
from app.modules.aftersales.rpa_publisher import publish, sync_rpa
from tests.test_aftersales_rpa import delivery

pytest_plugins = ["tests.sales_pg_support"]


def test_cold_rpa_overview_avoids_regrouping_fixed_period_rows(sales_pg, tmp_path):
    """恢复宽GROUP BY会使本测试失败；相同快照冷/热完整响应必须相同。"""
    sync_rpa(
        sales_pg,
        lambda: delivery(date(2026, 11, 9), 100) + delivery(),
        tmp_path,
    )
    statements = []

    def capture(c, cursor, statement, parameters, context, many):
        statements.append(statement)

    cache = ReportRowsCache()
    sa.event.listen(sales_pg, "before_cursor_execute", capture)
    try:
        with snapshot(sales_pg, rows_cache=cache) as repo:
            cold = decimal_wire(dict(data=overview(repo, AfterQuery()), meta=repo.metadata()))
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", capture)
    values = {metric["key"]: metric["value"] for metric in cold["data"]["metrics"]}
    assert values["returned_qty"] == "3"
    assert values["inbound_amount"] == "10.1256"
    assert values["gmv"] == "125"
    facts = [sql for sql in statements if "FROM public.read_" in sql]
    assert len(facts) == 6
    assert not any("GROUP BY" in sql for sql in facts)
    with snapshot(sales_pg, rows_cache=cache) as repo:
        warm = decimal_wire(dict(data=overview(repo, AfterQuery()), meta=repo.metadata()))
    assert warm == cold


def publish_boundary_rows(engine, tmp_path, rows):
    """发布隔离库规范行边界；不改变线上接收器或已发布事实。"""
    from app.modules.aftersales.publisher import reflect

    groups = normalize(delivery())
    group = next(group for group in groups if group.role == "j1_returns")
    base = group.rows[0]
    group.rows = [dict(base, **row) for row in rows]
    with engine.begin() as c:
        publish(c, reflect(c), groups, tmp_path, "synthetic:cold-boundaries")


def test_missing_rpa_sku_and_style_preserve_group_identity_and_comparisons(sales_pg, tmp_path):
    """合法缺键重复行仍按旧完整维度合组；不能把最小原行键拆成两个对象。"""
    delivered = delivery(date(2026, 11, 9), 100) + delivery()
    for item in delivered:
        if item["class"] == "按退货原因":
            item["json_data"].update(
                商品编码="",
                款式编码="",
                商品名称="缺键合成",
                退货原因="尺码不合适",
                实退数量="2",
                实际入库金额="2.50",
            )
    sync_rpa(sales_pg, lambda: delivered, tmp_path)
    cache = ReportRowsCache()
    results = []
    for active_cache in (None, cache, cache):
        with snapshot(sales_pg, rows_cache=active_cache) as repo:
            q = AfterQuery(view="style", keyword="缺键合成")
            styles = analysis(repo, q)
            assert styles["total"] == 1
            assert styles["rows"][0]["cells"]["returned_qty"]["value"] == "4"
            assert styles["rows"][0]["cells"]["inbound_amount"]["value"] == "5.00"
            board = overview(repo, q)
            assert next(m["value"] for m in board["metrics"] if m["key"] == "records") == "2"
            comparison = analysis(repo, AfterQuery(view="mom", keyword="缺键合成"))
            by_style = [r for r in comparison["rows"] if r["cells"]["type"]["value"] == "单款"]
            assert len(by_style) == 2
            assert sorted(r["cells"]["current"]["value"] or "" for r in by_style) == ["", "¥5.00"]
            assert sorted(r["cells"]["previous"]["value"] or "" for r in by_style) == ["", "¥5.00"]
            results.append(decimal_wire(dict(styles=styles, comparison=comparison, board=board)))
    assert results[0] == results[1] == results[2]


def test_missing_rpa_key_groups_keep_null_empty_dimensions_and_sql_min_order(sales_pg, tmp_path):
    publish_boundary_rows(
        sales_pg,
        tmp_path,
        [
            dict(
                source_row_key=key,
                source_product_code=None,
                source_style_code=None,
                source_color_specification=color,
                actual_returned_qty=Decimal("2"),
            )
            for key, color in (("2", None), ("10", None), ("3", ""), ("20", ""))
        ],
    )
    with snapshot(sales_pg) as repo:
        rows = repo.rows("j1_returns")
        assert [(r["source_row_key"], r["source_color_specification"]) for r in rows] == [
            ("10", None),
            ("20", ""),
        ]
        assert [r["_record_count"] for r in rows] == [2, 2]
        assert [r["actual_returned_qty"] for r in rows] == [Decimal("4"), Decimal("4")]


def test_rpa_duplicate_identity_keeps_null_zero_negative_and_raw_counts(sales_pg, tmp_path):
    publish_boundary_rows(
        sales_pg,
        tmp_path,
        [
            dict(
                source_row_key="01",
                actual_returned_qty=Decimal("2.0001"),
                actual_inbound_amount=None,
            ),
            dict(
                source_row_key="02",
                actual_returned_qty=Decimal("-0.0001"),
                actual_inbound_amount=Decimal("-0.50"),
            ),
            dict(
                source_row_key="03",
                actual_returned_qty=Decimal("0"),
                actual_inbound_amount=Decimal("0"),
            ),
        ],
    )
    cache = ReportRowsCache()
    results = []
    for active_cache in (None, cache, cache):
        with snapshot(sales_pg, rows_cache=active_cache) as repo:
            body = overview(repo, AfterQuery(keyword="尺码"))
            values = {metric["key"]: metric["value"] for metric in body["metrics"]}
            assert values["records"] == "3"
            assert values["returned_qty"] == Decimal("2.0000")
            assert values["inbound_amount"] is None
            assert values["total_rate"] is None
            summary = repo.source_summary("j1_returns")
            assert summary["rows"] == 3
            assert summary["actual_returned_qty"] == Decimal("2.0000")
            assert summary["actual_inbound_amount"] is None
            results.append(decimal_wire(dict(data=body, meta=repo.metadata())))
    assert results[0] == results[1] == results[2]


def test_rpa_raw_scope_limit_counts_original_rows_in_real_pg(sales_pg):
    """重复身份也不能用返回分组数绕过30万原行上限，且LIMIT不能静默截断。"""
    from app.modules.aftersales.repository import BoardError
    from app.modules.aftersales.rpa_ingestion import REVISION
    from migrations_v2.baseline_20261028.schema import build_metadata

    tables = build_metadata().tables
    versions = tables["public.dataset_versions"]
    end = date(2026, 11, 16)
    with sales_pg.begin() as c:
        for vid, code, kind in (
            ("limit-input", "rpa_returns", "source"),
            ("limit-output", "aftersales_report", "derived"),
        ):
            c.execute(
                versions.insert().values(
                    id=vid,
                    dataset_code=code,
                    origin_kind=kind,
                    state="draft",
                    source_system="synthetic" if kind == "source" else None,
                    source_locator="synthetic://limit" if kind == "source" else None,
                    row_schema_revision=REVISION,
                    interpretation_ref="synthetic:row-limit",
                    producer_revision="synthetic:test",
                    period_start=end,
                    period_end=end,
                )
            )
        c.execute(
            sa.text("""
                INSERT INTO return_records (
                    id,dataset_version_id,source_row_key,row_kind,shop_match_status,
                    date_status,period_start,period_end,actual_returned_qty
                ) SELECT 'limit-' || n,'limit-input',n::text,'detail','not_provided',
                    'verified_period',:day,:day,1 FROM generate_series(1,300001) n
            """),
            {"day": end},
        )
        c.execute(
            versions.update()
            .where(versions.c.id == "limit-input")
            .values(
                state="validated",
                content_fingerprint="synthetic:limit-input",
                validation_evidence_ref="synthetic:row-limit",
                received_at=sa.func.clock_timestamp(),
            )
        )
        c.execute(versions.update().where(versions.c.id == "limit-input").values(state="published"))
        c.execute(
            tables["public.version_inputs"]
            .insert()
            .values(
                output_version_id="limit-output",
                input_role="j1_returns",
                input_version_id="limit-input",
                window_start=end,
                window_end=end,
            )
        )
        c.execute(
            tables["public.aftersales_reports"]
            .insert()
            .values(
                id="limit-report",
                dataset_version_id="limit-output",
                report_type="custom",
                period_start=end,
                period_end=end,
                revision_no=1,
                source_generated_time_status="unknown",
                previous_comparison_status="missing",
                previous_comparison_reason="合成无上期",
                yoy_comparison_status="missing",
                yoy_comparison_reason="合成无去年",
            )
        )
        c.execute(
            versions.update()
            .where(versions.c.id == "limit-output")
            .values(
                state="validated",
                content_fingerprint="synthetic:limit-output",
                validation_evidence_ref="synthetic:row-limit",
            )
        )
        c.execute(
            versions.update().where(versions.c.id == "limit-output").values(state="published")
        )
    with snapshot(sales_pg) as repo, pytest.raises(BoardError) as error:
        repo.rows("j1_returns")
    assert error.value.code == "REPORT_SCOPE_ROW_LIMIT"


def test_rpa_extreme_precision_summary_keeps_exact_sql_sum(sales_pg, tmp_path):
    publish_boundary_rows(
        sales_pg,
        tmp_path,
        [
            dict(
                source_row_key="01",
                actual_returned_qty=Decimal(
                    "999999999999999999999999999999999999999999999999999999999"
                ),
            ),
            dict(source_row_key="02", actual_returned_qty=Decimal("2")),
        ],
    )
    cache = ReportRowsCache()
    results = []
    for active_cache in (None, cache, cache):
        with snapshot(sales_pg, rows_cache=active_cache) as repo:
            body = overview(repo, AfterQuery())
            summary = repo.source_summary("j1_returns")
            assert summary["actual_returned_qty"] == Decimal(
                "1000000000000000000000000000000000000000000000000000000001"
            )
            results.append(decimal_wire(dict(data=body, meta=repo.metadata())))
    assert results[0] == results[1] == results[2]
