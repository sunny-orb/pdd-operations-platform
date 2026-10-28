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
    assert rows[0]["source_style_code"] is None
    assert rows[0]["source_product_code"] == "0007"
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
