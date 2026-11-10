"""M5真实PG固定报告与缺源/精确分母反例；仅隔离合成数据。"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal, localcontext

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.config import Settings
from migrations_v2.baseline_20261028.schema import build_metadata

pytest_plugins = ["tests.sales_pg_support"]


def div(a, b):
    with localcontext() as ctx:
        ctx.prec = 50
        return Decimal(a) / Decimal(b)


@contextmanager
def client_for(engine):
    from app.main import create_app

    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app) as client:
        app.state.engine = engine
        yield client


def publish(
    engine,
    suffix="1",
    end=date(2026, 10, 27),
    missing=False,
    coarse=False,
    rules=None,
    returns=None,
    source_revision="m5-test",
    revision_no=1,
    start=date(2026, 9, 28),
    previous_report_id=None,
    yoy_report_id=None,
):
    tables = build_metadata().tables

    def tab(n):
        return tables["public." + n]

    with engine.begin() as c:
        rule_id = None
        if rules is not None:
            from app.modules.aftersales.publisher import fingerprint

            rule_id = "rules-" + suffix
            c.execute(
                tab("rule_sets")
                .insert()
                .values(
                    id=rule_id,
                    set_code="m5-test-" + suffix,
                    revision="1",
                    domain="aftersales",
                    schema_revision="v1",
                    parameters=rules,
                    confirmation_status="partially_confirmed",
                    source_locator="synthetic:fixed-reason-dictionary",
                    content_sha256=fingerprint(rules),
                )
            )

        def version(id, code, kind="source"):
            c.execute(
                tab("dataset_versions")
                .insert()
                .values(
                    id=id,
                    dataset_code=code,
                    origin_kind=kind,
                    state="draft",
                    source_system="synthetic" if kind == "source" else None,
                    source_locator="synthetic://m5" if kind == "source" else None,
                    row_schema_revision=source_revision if kind == "source" else "m5-test",
                    interpretation_ref="synthetic:fixed-groups",
                    producer_revision="synthetic:test",
                    period_start=start,
                    period_end=end,
                    rule_set_id=rule_id if kind == "derived" else None,
                )
            )

        def seal(id):
            c.execute(
                tab("dataset_versions")
                .update()
                .where(tab("dataset_versions").c.id == id)
                .values(
                    state="validated",
                    content_fingerprint="synthetic:" + id,
                    validation_evidence_ref="synthetic:verified",
                    received_at=sa.func.clock_timestamp() if not id.startswith("report-") else None,
                )
            )
            c.execute(
                tab("dataset_versions")
                .update()
                .where(tab("dataset_versions").c.id == id)
                .values(state="published")
            )

        for code, _role, amount in [
            ("jst_returns", "j1_returns", "10.01"),
            ("jst_shipped_sales", "j2_shipped_sales", "100"),
            ("jst_cancelled_sales", "j3_cancelled_sales", "25"),
        ]:
            vid = code + suffix
            version(vid, code)
            base = dict(
                dataset_version_id=vid,
                row_kind="detail",
                shop_match_status="not_provided",
                date_status="verified_period",
                period_start=end,
                period_end=end,
                source_product_category="帽子",
                source_style_code="0001",
                source_product_code="SKU1",
                source_product_name="合成商品",
                source_subcategory="细类甲",
            )
            if code == "jst_returns":
                c.execute(
                    tab("return_records").insert(),
                    [{**base, "source_aftersale_type": None, **r} for r in returns]
                    if returns is not None
                    else [
                        dict(
                            base,
                            id="r" + suffix,
                            source_row_key="r",
                            actual_returned_qty=Decimal("1.5"),
                            actual_inbound_amount=Decimal(amount),
                            source_return_reason="尺码偏大",
                        ),
                        dict(
                            base,
                            id="r2" + suffix,
                            source_row_key="r2",
                            source_subcategory="细类乙",
                            actual_returned_qty=Decimal("2.5"),
                            actual_inbound_amount=None if missing else Decimal("4.99"),
                            source_return_reason="质量问题",
                        ),
                    ],
                )
            else:
                if coarse:
                    base["source_subcategory"] = None
                c.execute(
                    tab("product_sales_records")
                    .insert()
                    .values(
                        **base,
                        id=code + suffix,
                        source_row_key="s",
                        sales_amount=Decimal(amount),
                        source_sales_qty=Decimal("5"),
                    )
                )
                # 无退货的销售组必须参与总体分母。
                c.execute(
                    tab("product_sales_records")
                    .insert()
                    .values(
                        **dict(base, source_style_code="0002", source_product_code="SKU2"),
                        id=code + "extra" + suffix,
                        source_row_key="extra",
                        sales_amount=Decimal("10"),
                        source_sales_qty=Decimal("1"),
                    )
                )
            seal(vid)
        out = "report-" + suffix
        version(out, "aftersales_report", "derived")
        for code, role in [
            ("jst_returns", "j1_returns"),
            ("jst_shipped_sales", "j2_shipped_sales"),
            ("jst_cancelled_sales", "j3_cancelled_sales"),
        ]:
            c.execute(
                tab("version_inputs")
                .insert()
                .values(
                    output_version_id=out,
                    input_role=role,
                    input_version_id=code + suffix,
                    window_start=start,
                    window_end=end,
                )
            )
        c.execute(
            tab("aftersales_reports")
            .insert()
            .values(
                id="report" + suffix,
                dataset_version_id=out,
                report_type="custom",
                period_start=start,
                period_end=end,
                revision_no=revision_no,
                source_generated_time_status="unknown",
                previous_report_id=previous_report_id,
                previous_comparison_status="available" if previous_report_id else "missing",
                previous_comparison_reason=None if previous_report_id else "合成无上期",
                yoy_report_id=yoy_report_id,
                yoy_comparison_status="available" if yoy_report_id else "missing",
                yoy_comparison_reason=None if yoy_report_id else "合成无去年",
            )
        )
        seal(out)


def test_empty_board_preserves_fields_and_never_claims_zero(sales_pg):
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/aftersales/meta")
        assert meta.status_code == 200, meta.text
        assert meta.json()["data"]["status"] == "source_unavailable"
        board = client.get("/api/v1/aftersales/overview").json()["data"]
        assert len(board["metrics"]) == 9 and all(m["value"] is None for m in board["metrics"])
        for view, count in [
            ("category", 12),
            ("style", 13),
            ("mom", 7),
            ("yoy", 7),
            ("quality", 3),
            ("history", 10),
        ]:
            data = client.get("/api/v1/aftersales/analysis", params={"view": view}).json()["data"]
            assert len(data["columns"]) == count and data["rows"] == []
        assert client.get("/api/v1/aftersales/analysis?size=50").status_code == 200
        assert client.get("/api/v1/aftersales/analysis?size=100").status_code == 200
        assert client.get("/api/v1/aftersales/analysis?size=1000").status_code == 422
        assert client.get("/api/v1/aftersales/overview?data_version=stale").status_code == 409


def test_fixed_report_rates_include_sales_without_returns_and_decimal_strings(sales_pg):
    publish(sales_pg)
    with client_for(sales_pg) as client:
        board = client.get("/api/v1/aftersales/overview").json()["data"]
        vals = {m["key"]: m["value"] for m in board["metrics"]}
        assert vals["returned_qty"] == "4.0"
        assert Decimal(vals["inbound_amount"]) == Decimal("15")
        assert Decimal(vals["gmv"]) == Decimal("145")
        assert Decimal(vals["presale_rate"]) == div("35", "145")
        assert Decimal(vals["aftersale_rate"]) == div("15", "110")
        assert Decimal(vals["total_rate"]) == div("50", "145")
        assert vals["size_qty"] is None and vals["quality_qty"] is None
        data = client.get("/api/v1/aftersales/analysis?view=style").json()["data"]
        assert len(data["rows"]) == 3  # 同款两个细组不被Map覆盖，另保留零退货销售组
        fine_b = [r for r in data["rows"] if "细类乙" in r["cells"]["product"]["detail"]]
        assert len(fine_b) == 1 and fine_b[0]["cells"]["gmv"]["value"] is None
        with sales_pg.connect() as c:
            assert c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one() == 0


def test_null_amount_blocks_sum_and_coarse_sales_not_copied_to_styles(sales_pg):
    publish(sales_pg, missing=True, coarse=True)
    with client_for(sales_pg) as client:
        board = client.get("/api/v1/aftersales/overview").json()["data"]
        vals = {m["key"]: m["value"] for m in board["metrics"]}
        assert vals["inbound_amount"] is None and vals["total_rate"] is None
        assert Decimal(vals["gmv"]) == 145
        rows = client.get("/api/v1/aftersales/analysis?view=style").json()["data"]["rows"]
        assert all(
            r["cells"]["gmv"]["value"] is None
            for r in rows
            if r["cells"]["returned_qty"]["value"] is not None
        )


def test_selected_history_stays_fixed_and_catalog_change_returns_409(sales_pg):
    publish(sales_pg)
    with client_for(sales_pg) as client:
        token = client.get("/api/v1/aftersales/meta").json()["data"]["data_version"]
        publish(sales_pg, suffix="2", end=date(2026, 10, 28))
        assert (
            client.get("/api/v1/aftersales/overview", params={"data_version": token}).status_code
            == 409
        )
        data = client.get("/api/v1/aftersales/overview?report_id=report1").json()["data"]
        assert data["report"]["period_end"] == "2026-10-27"
        assert (
            client.get("/api/v1/aftersales/meta").json()["data"]["default_report_id"] == "report2"
        )
        assert client.get("/api/v1/aftersales/overview?report_id=does-not-exist").status_code == 422


def test_reason_keyword_selects_full_objects_across_roles_and_meta_is_lightweight(sales_pg):
    publish(sales_pg)
    seen = []

    def record(_connection, _cursor, statement, *_):
        seen.append(statement)

    sa.event.listen(sales_pg, "before_cursor_execute", record)
    try:
        with client_for(sales_pg) as client:
            client.get("/api/v1/aftersales/meta")
            assert not any("source_row_key" in sql for sql in seen)
            data = client.get("/api/v1/aftersales/overview?keyword=尺码偏大").json()["data"]
            vals = {m["key"]: m["value"] for m in data["metrics"]}
            assert Decimal(vals["returned_qty"]) == Decimal("1.5")
            assert Decimal(vals["gmv"]) == Decimal("125")
            assert Decimal(vals["aftersale_rate"]) == Decimal("10.01") / 100
            rows = client.get("/api/v1/aftersales/analysis?view=style&keyword=尺码偏大").json()[
                "data"
            ]["rows"]
            assert len(rows) == 1
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", record)


def test_real_writer_api_uses_actual_observation_and_fixed_version_success(sales_pg, tmp_path):
    from datetime import UTC, datetime, timedelta

    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source
    from tests.test_aftersales_ingestion import sample

    end = date(2026, 10, 27)
    receive = sample(end)
    heads = {
        code: publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)[
            "version_id"
        ]
        for code in FACTS
    }
    publish_reports(sales_pg, tmp_path)
    tables = build_metadata().tables
    with sales_pg.begin() as c:
        fixed = c.execute(
            sa.text("SELECT max(finished_at) FROM task_runs WHERE result_version_id=:v"),
            dict(v=heads["jst_returns"]),
        ).scalar_one()
        # 后来别的版本成功不应成为旧报告输入的成功时间。
        c.execute(
            tables["public.task_runs"]
            .insert()
            .values(
                id="unrelated-success",
                job_code="sync_jst_returns",
                request_ref="synthetic",
                lock_key="synthetic-other",
                status="succeeded",
                queued_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC) + timedelta(seconds=1),
                result_version_id=heads["jst_shipped_sales"],
            )
        )
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/aftersales/meta").json()["data"]
        src = {x["code"]: x for x in meta["sources"]}
        assert src["jst_returns"]["observed_from"] == "2026-10-27"
        assert src["jst_returns"]["observed_to"] == "2026-10-27"
        assert src["jst_returns"]["last_success_at"] == fixed.isoformat()
        assert "1119" in src["jst_returns"]["note"] and "补贴" in src["jst_returns"]["note"]
        overview = client.get("/api/v1/aftersales/overview").json()["data"]
        vals = {m["key"]: m["value"] for m in overview["metrics"]}
        assert vals["returned_qty"] == "2" and vals["inbound_amount"] is None
        monthly = next(
            r
            for r in client.get("/api/v1/aftersales/options").json()["data"]["reports"]
            if r["report_type"] == "monthly"
        )
        oldmeta = client.get("/api/v1/aftersales/meta", params={"report_id": monthly["id"]}).json()[
            "data"
        ]
        assert all(
            x["status"] == "no_records" and x["observed_to"] is None for x in oldmeta["sources"]
        )


def test_api_decimal_strings_never_use_exponents():
    from app.api.aftersales import decimal_wire

    assert decimal_wire({"value": Decimal("0E-10"), "ratio": Decimal("1E-30")}) == {
        "value": "0.0000000000",
        "ratio": "0.000000000000000000000000000001",
    }


def test_comparison_text_preserves_rate_units_and_zero_base():
    from app.modules.aftersales.analysis import change_text, comparison_text

    assert comparison_text(Decimal("0.1234"), "percent") == "12.34%"
    assert change_text(Decimal("0.2"), Decimal("0.1"), "percent") == "+10.00个百分点"
    assert change_text(Decimal("2"), Decimal("0"), "number") == "2；相对变化不可比"
    assert change_text(None, Decimal("1"), "currency") is None


def test_new_source_head_does_not_hide_report_pending_recompute(sales_pg, tmp_path):
    from app.modules.aftersales.ingestion import normalize
    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source
    from tests.test_aftersales_ingestion import sample

    end = date(2026, 10, 27)
    receive = sample(end)
    for code in FACTS:
        publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)
    publish_reports(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        before = client.get("/api/v1/aftersales/meta").json()["data"]

        def changed(hb):
            _, raw, _ = receive("jst_returns")
            raw[0]["aftersale_num"] = "3"
            return end, raw, normalize("jst_returns", raw, end)

        publish_source(sales_pg, "jst_returns", changed, tmp_path)
        pending = client.get("/api/v1/aftersales/meta").json()["data"]
        src = next(x for x in pending["sources"] if x["code"] == "jst_returns")
        assert pending["data_version"] == before["data_version"]
        assert src["status"] == "new_input_pending_report" and "待重算" in src["note"]
        assert src["last_success_at"] == before["sources"][0]["last_success_at"]
        publish_reports(sales_pg, tmp_path)
        after = client.get("/api/v1/aftersales/meta").json()["data"]
        assert after["data_version"] != before["data_version"]
        assert (
            next(x for x in after["sources"] if x["code"] == "jst_returns")["status"] == "observed"
        )


def test_unverified_focus_is_unavailable_not_zero_records(sales_pg):
    publish(sales_pg)
    with client_for(sales_pg) as client:
        body = client.get("/api/v1/aftersales/overview?focus=quality").json()["data"]
        assert next(x["value"] for x in body["metrics"] if x["key"] == "records") is None
        table = client.get("/api/v1/aftersales/analysis?focus=quality&view=category").json()["data"]
        assert table["status"] == "rule_pending"
        assert "不表示无问题" in table["scope_note"]


def classified_returns(suffix="1"):
    return [
        dict(
            id="quality-" + suffix,
            source_row_key="quality",
            actual_returned_qty=Decimal("2"),
            actual_inbound_amount=None,
            source_return_reason="质量问题，商品破损或污渍",
            source_aftersale_type="拒收退货",
        ),
        dict(
            id="other-" + suffix,
            source_row_key="other",
            actual_returned_qty=Decimal("3"),
            actual_inbound_amount=None,
            source_return_reason="不想要了",
            source_aftersale_type="普通退货",
        ),
        dict(
            id="size-" + suffix,
            source_row_key="size",
            source_style_code="0002",
            source_product_code="SKU2",
            actual_returned_qty=Decimal("4"),
            actual_inbound_amount=None,
            source_return_reason="尺码偏大也偏小",
            source_aftersale_type="普通退货",
        ),
        dict(
            id="missing-" + suffix,
            source_row_key="missing",
            source_style_code="0003",
            source_product_code="SKU3",
            actual_returned_qty=Decimal("5"),
            actual_inbound_amount=None,
            source_return_reason=None,
            source_aftersale_type="普通退货",
        ),
    ]


@pytest.mark.parametrize(
    "revision",
    [
        "m5-jst-current-sku-v2",
        "m5-jst-current-style-consensus-v3",
        "m5-jst-current-bundle-consensus-v4",
    ],
)
def test_fixed_dictionary_quality_focus_selects_full_objects_and_preserves_amount_gates(
    sales_pg, revision
):
    from app.modules.aftersales.classification import rule_parameters

    publish(
        sales_pg,
        rules=rule_parameters(),
        returns=classified_returns(),
        source_revision=revision,
    )
    with client_for(sales_pg) as client:
        board = client.get("/api/v1/aftersales/overview").json()["data"]
        values = {r["key"]: r["value"] for r in board["metrics"]}
        assert Decimal(values["returned_qty"]) == 14
        assert Decimal(values["quality_qty"]) == 2
        assert Decimal(values["size_qty"]) == 4
        assert values["inbound_amount"] is None and values["total_rate"] is None
        assert "未填写" in board["quality_note"] and "观察量" in board["quality_note"]
        focused = client.get("/api/v1/aftersales/overview?focus=quality").json()["data"]
        scoped = {r["key"]: r["value"] for r in focused["metrics"]}
        assert Decimal(scoped["returned_qty"]) == 5
        assert Decimal(scoped["records"]) == 2
        assert Decimal(scoped["gmv"]) == 125
        assert Decimal(scoped["quality_qty"]) == 2
        assert {r["name"] for r in focused["reasons"]} == {"质量问题，商品破损或污渍", "不想要了"}
        table = client.get("/api/v1/aftersales/analysis?view=quality").json()["data"]
        assert table["status"] == "observed_reference"
        assert {r["cells"]["qty"]["value"] for r in table["rows"]} == {"2"}
        assert len(table["rows"]) == 2  # 多标签各2件，质量总量仍2件
        stages = {r["name"]: r for r in board["stages"]}
        assert stages["已发货拒收"]["qty"] == "2"
        assert stages["已发货拒收"]["amount"] is None
        assert "来源标记" in stages["已发货拒收"]["note"]
        assert stages["签收后退货"]["qty"] is None
        assert all(r["risk_score"] is None for r in board["category_risks"] + board["style_risks"])
        assert board["category_risks"] and board["style_risks"]


def test_old_report_dictionary_is_frozen_when_new_report_enables_classification(sales_pg):
    from app.modules.aftersales.classification import rule_parameters

    old = rule_parameters()
    old["dictionaries"] = []
    old["branches"] = []
    publish(sales_pg, end=date(2026, 10, 26), rules=old, returns=classified_returns())
    with client_for(sales_pg) as client:
        before = client.get("/api/v1/aftersales/meta").json()["data"]["data_version"]
        publish(sales_pg, suffix="2", rules=rule_parameters(), returns=classified_returns("2"))
        assert client.get("/api/v1/aftersales/overview?data_version=" + before).status_code == 409
        old_board = client.get("/api/v1/aftersales/overview?report_id=report1").json()["data"]
        current = client.get("/api/v1/aftersales/overview?report_id=report2").json()["data"]
        assert next(r["value"] for r in old_board["metrics"] if r["key"] == "quality_qty") is None
        assert next(r["value"] for r in current["metrics"] if r["key"] == "quality_qty") == "2"
        old_focus = client.get(
            "/api/v1/aftersales/analysis?report_id=report1&view=quality&focus=quality"
        ).json()["data"]
        assert old_focus["status"] == "rule_pending"


@pytest.mark.parametrize(
    "revision,partial_missing",
    [
        ("m5-jst-partial-v1", False),
        ("m5-jst-current-sku-v2", True),
        ("m5-jst-current-style-consensus-v3", True),
        ("m5-jst-current-bundle-consensus-v4", True),
    ],
)
def test_refusal_requires_verified_source_revision_and_whole_window_field_coverage(
    sales_pg, revision, partial_missing
):
    from app.modules.aftersales.classification import rule_parameters

    rows = classified_returns()
    if partial_missing:
        rows[-1]["source_aftersale_type"] = None
    else:
        # 旧接收把商品售后类型“退货”放在此列，不能据非空推断本期拒收为零。
        for row in rows:
            row["source_aftersale_type"] = "退货"
    publish(sales_pg, rules=rule_parameters(), returns=rows, source_revision=revision)
    with client_for(sales_pg) as client:
        for focus in ("all", "quality"):
            board = client.get("/api/v1/aftersales/overview", params={"focus": focus}).json()[
                "data"
            ]
            stages = {r["name"]: r for r in board["stages"]}
            assert stages["已发货拒收"]["qty"] is None
            assert stages["已发货拒收"]["status"] == "unavailable"
            assert stages["签收后退货"]["qty"] is None
