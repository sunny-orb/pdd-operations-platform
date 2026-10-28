from datetime import date
from decimal import Decimal

import pytest


def test_source_projection_keeps_actual_quantities_and_blocks_unverified_money():
    from app.modules.aftersales.ingestion import normalize

    rows = normalize(
        "jst_returns",
        [
            dict(
                __id="12",
                shop_name="kk树-kocotree官方旗舰店-配饰",
                shop_code="17",
                product_code="0007",
                product_name="帽子",
                spec_name="蓝",
                confirm_date="2026-10-27 10:20:00",
                aftersale_num="1.5",
                actual_receive_amount="38.4",
                platform_subsidy_amount="0",
                reason_type="质量",
                aftersale_status="已确认",
                product_aftersale_type="退货",
            )
        ],
        date(2026, 10, 27),
    )
    assert rows[0]["actual_returned_qty"] == Decimal("1.5")
    assert rows[0]["actual_inbound_amount"] is None
    assert rows[0]["source_style_code"] == "0007"
    assert rows[0]["source_product_code"] is None
    assert rows[0]["period_start"] == date(2026, 10, 27)
    assert rows[0]["source_product_category"] is None


def test_shipped_never_falls_back_to_sale_amount_and_duplicate_id_rejected():
    from app.modules.aftersales.ingestion import normalize

    row = dict(
        __id="1",
        shop_name="kk树-kocotree官方旗舰店-配饰",
        shop_code="17",
        product_code="07",
        product_name="帽子",
        spec_name="蓝",
        pay_time="2026-10-27 11:00:00",
        sfje=None,
        sale_amount="100",
        sale_num="2",
        outstock_num="1",
    )
    assert normalize("jst_shipped_sales", [row], date(2026, 10, 27))[0]["sales_amount"] is None
    assert normalize("jst_cancelled_sales", [row], date(2026, 10, 27))[0][
        "sales_amount"
    ] == Decimal("100")
    with pytest.raises(ValueError, match="DUPLICATE"):
        normalize("jst_shipped_sales", [row, row], date(2026, 10, 27))


pytest_plugins = ["tests.sales_pg_support"]


def sample(end):
    from app.modules.aftersales.ingestion import normalize

    def receive(code):
        row = dict(
            __id="1",
            shop_name="kk树-kocotree官方旗舰店-配饰",
            shop_code="17",
            product_code="07",
            product_name="帽子",
            spec_name="蓝",
            confirm_date=f"{end} 10:00:00",
            pay_time=f"{end} 10:00:00",
            aftersale_num="2",
            actual_receive_amount="40",
            platform_subsidy_amount="0",
            reason_type="尺码",
            aftersale_status="已确认",
            product_aftersale_type="退货",
            sfje=None,
            sale_amount="100",
            sale_num="2",
            outstock_num="1",
        )
        return end, [row], normalize(code, [row], end)

    return receive


def test_real_pg_source_report_idempotence_and_failure_preserves_pointer(sales_pg, tmp_path):
    import sqlalchemy as sa

    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source

    end = date(2026, 10, 27)
    receive = sample(end)
    heads = {}
    for code in FACTS:
        first = publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)
        assert (
            publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)[
                "version_id"
            ]
            == first["version_id"]
        )
        heads[code] = first["version_id"]
    report = publish_reports(sales_pg, tmp_path)
    assert publish_reports(sales_pg, tmp_path)["version_id"] == report["version_id"]

    def fail(hb):
        raise ConnectionError("not logged")

    with pytest.raises(ConnectionError):
        publish_source(sales_pg, "jst_returns", fail, tmp_path)
    with sales_pg.connect() as c:
        assert dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        ) == dict(heads, aftersales_report=report["version_id"])
        assert c.execute(sa.text("SELECT COUNT(*) FROM aftersales_reports")).scalar_one() == 3
        assert (
            c.execute(
                sa.text(
                    "SELECT status FROM task_runs WHERE job_code='sync_jst_returns' "
                    "ORDER BY queued_at DESC LIMIT 1"
                )
            ).scalar_one()
            == "failed"
        )


def test_report_rejects_missing_roles_and_writer_permissions(sales_pg, tmp_path):
    from app.modules.aftersales.publisher import publish_reports

    with pytest.raises(ValueError, match="M5_INPUTS_MISSING"):
        publish_reports(sales_pg, tmp_path)
    import sqlalchemy as sa

    with sales_pg.connect() as c:
        for t in ("return_records", "product_sales_records", "aftersales_reports"):
            assert c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest',:t,'INSERT')"), dict(t=t)
            ).scalar_one()
            assert not c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest',:t,'UPDATE')"), dict(t=t)
            ).scalar_one()


def test_real_writer_can_publish_sources_reports_and_cannot_update_input(sales_pg, tmp_path):
    import sqlalchemy as sa

    from app.jobs.sync_shop_daily import runtime_engine
    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source

    configured = runtime_engine()
    writer = sa.create_engine(
        configured.url.set(database=sales_pg.url.database), hide_parameters=True
    )
    configured.dispose()
    receive = sample(date(2026, 10, 27))
    try:
        for code in FACTS:
            publish_source(writer, code, lambda hb, code=code: receive(code), tmp_path)
        assert publish_reports(writer, tmp_path)["status"] == "published"
        with writer.begin() as c, pytest.raises(sa.exc.DBAPIError):
            c.execute(sa.text("UPDATE return_records SET actual_returned_qty=0"))
    finally:
        writer.dispose()


def test_calendar_month_is_not_labeled_rolling_thirty_days():
    from app.modules.aftersales.repository import public_report

    row = dict(
        id="m",
        report_type="monthly",
        source_label=None,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        sealed_at=None,
        document_url=None,
        sheet_url=None,
    )
    assert public_report(row)["label"] == "2026年09月售后月报"


def identity_fact(**changes):
    """缺额、申请两件实退一件及拒收原类型均为有意的反例。"""
    return (
        dict(
            __id="1",
            shop_name="kk树-kocotree官方旗舰店-配饰",
            shop_code="17",
            product_code="KQ24001",
            spec_code="06941716542009",
            product_name="帽子",
            spec_name="蓝",
            confirm_date="2026-10-27 11:00:00",
            pay_time="2026-10-27 11:00:00",
            aftersale_num="1",
            apply_aftersale_num="2",
            actual_receive_amount="38.4",
            platform_subsidy_amount="0",
            reason_type=None,
            aftersale_status="已确认",
            aftersale_type="拒收退货",
            product_aftersale_type="退货",
            sfje=None,
            sale_amount="100",
            sale_num="2",
            outstock_num="1",
        )
        | changes
    )


def current_master(**changes):
    return (
        dict(
            __id="91",
            data_source="聚水潭标准API",
            product_code="KQ24001",
            spec_code="06941716542009",
            product_type="普通商品",
            suite_code=None,
            first_category="童帽秋冬",
            second_category="护耳帽",
            dt="2026-10-27",
            start_date=None,
            end_date=None,
        )
        | changes
    )


@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_current_jst_sku_identity_is_shared_without_changing_quantities_or_money(code):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    fact = identity_fact()
    raw = enrich_current_master([fact], [current_master()], "2026-10-27")
    rows = normalize(code, raw, date(2026, 10, 27))
    assert len(rows) == 1
    assert rows[0]["source_style_code"] == "KQ24001"
    assert rows[0]["source_product_code"] == "06941716542009"
    assert rows[0]["source_product_category"] == "童帽秋冬"
    assert rows[0]["source_subcategory"] == "护耳帽"
    assert rows[0]["source_virtual_category"] is None
    assert "jst_current_master_record" not in fact
    assert raw[0]["jst_current_master_record"]["__id"] == "91"
    assert raw[0]["jst_current_snapshot_date"] == "2026-10-27"
    if code == "jst_returns":
        assert rows[0]["source_aftersale_type"] == "拒收退货"
        assert rows[0]["actual_returned_qty"] == Decimal("1")
        assert rows[0]["actual_inbound_amount"] is None
        assert raw[0]["apply_aftersale_num"] == "2"
        assert raw[0]["product_aftersale_type"] == "退货"
    else:
        assert rows[0]["sales_amount"] == (None if code == "jst_shipped_sales" else Decimal("100"))


@pytest.mark.parametrize(
    "fact,master,want_status",
    [
        (identity_fact(spec_code=None), current_master(), "source_key_missing"),
        (identity_fact(), current_master(product_type="组合商品", suite_code="组合1"), "not_found"),
        (identity_fact(), current_master(spec_code="另一个SKU"), "not_found"),
        (identity_fact(), current_master(product_code="另一个款"), "identity_mismatch"),
    ],
)
def test_missing_combination_and_wrong_style_keep_fact_without_guessed_categories(
    fact, master, want_status
):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master([fact], [master], "2026-10-27")
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["actual_returned_qty"] == Decimal("1")
    assert row["source_style_code"] == "KQ24001"
    assert row["source_product_code"] == fact["spec_code"]
    assert row["source_product_category"] is None
    assert row["source_subcategory"] is None
    assert raw[0]["jst_current_mapping_status"] == want_status


@pytest.mark.parametrize(
    "master,error",
    [
        ([current_master(), current_master(__id="92")], "MASTER_DUPLICATE"),
        ([current_master(), current_master(__id="92", product_code="别款")], "MASTER_SKU_CONFLICT"),
        ([current_master(dt="2026-10-26")], "MASTER_SNAPSHOT"),
    ],
)
def test_current_master_ambiguity_and_stale_snapshot_cannot_publish(master, error):
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match=error):
        enrich_current_master([identity_fact()], master, "2026-10-27")


@pytest.mark.parametrize("changed", ["fact", "master", "snapshot"])
def test_reader_checks_both_fact_and_current_master_for_stability(changed):
    from app.connectors.aftersales import AftersalesReader

    reader = AftersalesReader.__new__(AftersalesReader)
    calls = dict(fact=0, master=0)

    def read(code, end):
        calls["fact"] += 1
        return [
            identity_fact(aftersale_num="2" if changed == "fact" and calls["fact"] > 1 else "1")
        ]

    def read_master(raw):
        calls["master"] += 1
        snapshot = "2026-10-28" if changed == "snapshot" and calls["master"] > 1 else "2026-10-27"
        return snapshot, [
            current_master(
                dt=snapshot,
                second_category="围脖" if changed == "master" and calls["master"] > 1 else "护耳帽",
            )
        ]

    reader.read = read
    reader.read_current_master = read_master
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_READ"):
        reader.read_stable("jst_returns", date(2026, 10, 27))


def test_reader_stable_result_retains_master_evidence_in_raw():
    from app.connectors.aftersales import AftersalesReader

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.read = lambda code, end: [identity_fact()]
    reader.read_current_master = lambda raw: ("2026-10-27", [current_master()])
    raw, rows = reader.read_stable("jst_returns", date(2026, 10, 27))
    assert raw[0]["jst_current_master_source"] == "cubeappdata.std_api_jstbz_product_spec_info_f"
    assert raw[0]["jst_current_mapping_status"] == "matched"
    assert rows[0]["source_subcategory"] == "护耳帽"


def test_real_pg_current_master_identity_and_refusal_evidence_are_immutable(sales_pg, tmp_path):
    import json
    from pathlib import Path

    import sqlalchemy as sa

    from app.modules.aftersales.ingestion import enrich_current_master, normalize
    from app.modules.aftersales.publisher import publish_source

    end = date(2026, 10, 27)
    raw = enrich_current_master([identity_fact()], [current_master()], "2026-10-27")
    normalized = normalize("jst_returns", raw, end)
    result = publish_source(sales_pg, "jst_returns", lambda hb: (end, raw, normalized), tmp_path)
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM return_records")).mappings().one()
        assert row["source_style_code"] == "KQ24001"
        assert row["source_product_code"] == "06941716542009"
        assert row["source_product_category"] == "童帽秋冬"
        assert row["source_subcategory"] == "护耳帽"
        assert row["source_virtual_category"] is None
        assert row["source_aftersale_type"] == "拒收退货"
        assert row["actual_inbound_amount"] is None
        archive = c.execute(
            sa.text("SELECT retained_input_ref FROM dataset_versions WHERE id=:id"),
            dict(id=result["version_id"]),
        ).scalar_one()
    evidence = json.loads(Path(archive).read_text().strip())
    assert evidence["apply_aftersale_num"] == "2"
    assert evidence["jst_current_master_record"]["first_category"] == "童帽秋冬"
    assert evidence["jst_current_snapshot_date"] == "2026-10-27"
    with sales_pg.begin() as c, pytest.raises(sa.exc.DBAPIError):
        c.execute(sa.text("UPDATE return_records SET source_style_code='改写'"))
