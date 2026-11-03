"""原版GMV目标：合成源与真实隔离PG，不访问飞书或项目库。"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.connectors.sales_targets import (
    TARGET_BASE,
    TARGET_FIELDS,
    TARGET_TABLE,
    FeishuTargetReader,
)
from app.modules.sales.contracts import SalesError
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.storage import ShopDailyStore
from app.modules.sales.targets import SalesTargetStore, normalize_targets
from tests.test_sales_extended_api import params, setup
from tests.test_sales_extended_sources import window
from tests.test_sales_source import raw_row

pytest_plugins = ["tests.sales_pg_support"]


def rows():
    return [
        {
            "record_id": f"month-{year}-{month}",
            "平台": "拼多多",
            "年份": f"{year}年",
            "月份": f"{month}月",
            "gmv目标": "25000.25",
        }
        for year, month in [(2026, m) for m in range(1, 13)] + [(2027, 1)]
    ]


def manifest(count, **changes):
    return dict(
        base_token=TARGET_BASE,
        table_id=TARGET_TABLE,
        rev=1,
        timezone="Asia/Shanghai",
        records_count=count,
        has_more=False,
        columns={
            name: dict(field_id=fid, field_type=kind) for fid, (name, kind) in TARGET_FIELDS.items()
        },
        **changes,
    )


def candidate(source=None):
    values = rows() if source is None else source
    reader = FeishuTargetReader.for_test(lambda offset: (values, manifest(len(values))))
    return reader.read_stable(basis="original_gmv")


def test_exact_yuan_units_and_fiscal_exclusion():
    result = normalize_targets(rows(), basis="original_gmv")
    assert len(result.rows) == 12 and len(result.raw_rows) == 13
    assert result.rows[0]["period_start"] == date(2026, 2, 1)
    assert result.rows[-1]["period_end"] == date(2027, 1, 31)
    assert all(r["target_amount"] == Decimal("25000.25") for r in result.rows)
    historical = rows() + [dict(rows()[0], record_id="old-year", 年份="2025年", gmv目标=None)]
    assert normalize_targets(historical).fingerprint == result.fingerprint


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate_month",
        "duplicate_excluded_month",
        "duplicate_id",
        "multiple_years",
        "bad_amount",
        "bad_period",
    ],
)
def test_ambiguous_or_invalid_input_never_picks_first_row(defect):
    source = rows()
    if defect == "duplicate_month":
        source.append(dict(source[1], record_id="duplicate"))
    elif defect == "duplicate_excluded_month":
        source.append(dict(source[0], record_id="duplicate-excluded"))
    elif defect == "duplicate_id":
        source[2]["record_id"] = source[1]["record_id"]
    elif defect == "multiple_years":
        source[1]["年份"] = ["2026年", "2027年"]
    elif defect == "bad_amount":
        source[1]["gmv目标"] = "NaN"
    else:
        source[1]["月份"] = "13月"
    with pytest.raises(SalesError):
        normalize_targets(source, basis="original_gmv")


@pytest.mark.parametrize("defect", ["field_type", "field_id", "timezone", "pagination", "revision"])
def test_source_contract_or_pagination_change_blocks_read(defect):
    def page(offset):
        meta = manifest(1)
        if defect == "field_type":
            meta["columns"]["gmv目标"]["field_type"] = "text"
        elif defect == "field_id":
            meta["columns"]["gmv目标"]["field_id"] = "unapproved-field"
        elif defect == "timezone":
            meta["timezone"] = "UTC"
        elif defect == "pagination":
            meta.update(has_more=True, next_offset=offset)
        else:
            meta.update(has_more=offset == 0, next_offset=1, rev=offset + 1)
        return [rows()[1]], meta

    with pytest.raises(SalesError):
        FeishuTargetReader.for_test(page).read_stable(basis="original_gmv")


def test_pg_publication_replays_members_and_is_semantically_idempotent(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    old = params(client)["data_version"]
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="original_gmv")
    first = store.run(lambda hb: candidate())
    second = store.run(lambda hb: candidate())
    assert first.version_id == second.version_id and second.reused
    assert (
        client.get(
            "/api/v1/sales/targets", params={"month": "2026-10", "data_version": old}
        ).status_code
        == 409
    )
    response = client.get(
        "/api/v1/sales/targets",
        params={"month": "2026-10", "data_version": params(client)["data_version"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["month_target"] == "25000.25" and data["annual_target"] == "300003.00"
    assert data["month_actual"] == "100.00" and data["annual_actual"] == "100.00"
    assert data["month_ratio"] == "0.003999960000"
    assert data["gap"] == "24900.25"
    assert data["target_basis"] == "original_gmv" and data["state"] == "partial"
    assert data["target_coverage"]["missing_months"] == []
    assert data["month_missing_days"] > 0 and data["annual_missing_days"] > 0
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM sales_targets")).scalar_one() == 12
        assert c.execute(sa.text("SELECT count(*) FROM sales_target_shops")).scalar_one() == 72
        assert (
            c.execute(
                sa.text(
                    "SELECT count(*) FROM task_runs WHERE job_code='sync_sales_targets' "
                    "AND status='succeeded'"
                )
            ).scalar_one()
            == 2
        )


@pytest.mark.parametrize("month,expected_actual", [("2026-09", None), ("2026-10", "0.00")])
def test_pg_no_records_and_real_zero_remain_distinct(sales_pg, tmp_path, month, expected_actual):
    client = setup(sales_pg, tmp_path)
    ShopDailyStore(sales_pg, archive_root=tmp_path / "zero", approved=window()).publish_candidate(
        normalize_shop_daily(
            [
                raw_row(
                    metrics={
                        "成交金额": "0",
                        "店铺访客数": "0",
                        "成交买家数": "0",
                        "成交订单数": "0",
                    }
                )
            ],
            window(),
        )
    )
    SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="original_gmv").run(
        lambda hb: candidate()
    )
    response = client.get(
        "/api/v1/sales/targets",
        params={"month": month, "data_version": params(client)["data_version"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["month_actual"] == data["annual_actual"] == expected_actual
    assert data["month_target"] == "25000.25"
    if expected_actual is None:
        assert data["month_ratio"] is data["annual_ratio"] is data["gap"] is None
    else:
        assert Decimal(data["month_ratio"]) == Decimal(data["annual_ratio"]) == 0
        assert data["gap"] == "25000.25"
        assert data["month_missing_days"] > 0  # 真实零与其他日期缺记录可同时存在。


def test_pg_missing_month_and_real_zero_do_not_create_full_annual_or_zero_denominator(
    sales_pg, tmp_path
):
    client = setup(sales_pg, tmp_path)
    source = [r for r in rows() if r["月份"] != "3月"]
    next(r for r in source if r["月份"] == "10月")["gmv目标"] = 0
    SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="original_gmv").run(
        lambda hb: candidate(source)
    )
    data = client.get(
        "/api/v1/sales/targets",
        params={"month": "2026-10", "data_version": params(client)["data_version"]},
    ).json()["data"]
    assert data["month_target"] == "0.00" and data["month_ratio"] is None
    assert data["annual_target"] is None and data["annual_ratio"] is None
    assert data["target_coverage"]["missing_months"] == ["2026-03"]


def test_pg_source_failure_keeps_previous_version_and_failure_state(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="original_gmv")
    first = store.run(lambda hb: candidate())
    token = params(client)["data_version"]

    def fail(heartbeat):
        raise SalesError("FEISHU_READ_FAILED", 503)

    with pytest.raises(SalesError):
        store.run(fail)
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='sales_targets'"
                )
            ).scalar_one()
            == first.version_id
        )
    data = client.get(
        "/api/v1/sales/targets",
        params={"month": "2026-10", "data_version": params(client)["data_version"]},
    ).json()["data"]
    assert data["month_target"] == "25000.25"
    assert data["target_source"]["state"] == "failed_retained"
    filters = client.get("/api/v1/sales/filters").json()
    assert filters["meta"]["data_version"] == token
    assert filters["data"]["sources"]["sales_targets"]["last_attempt_status"] == "failed"
    store.run(lambda hb: candidate())
    recovered = client.get("/api/v1/sales/filters").json()
    assert recovered["meta"]["data_version"] == token
    assert recovered["data"]["sources"]["sales_targets"]["last_attempt_status"] == "succeeded"


def test_pg_actual_writer_requires_only_parent_id_lock_permission(sales_pg, tmp_path):
    setup(sales_pg, tmp_path)
    writer = sa.create_engine(sales_pg.url, hide_parameters=True)

    @sa.event.listens_for(writer, "connect")
    def as_ingest(dbapi, _record):
        with dbapi.cursor() as cursor:
            cursor.execute("SET ROLE pdd_ingest")
        dbapi.commit()

    try:
        with sales_pg.begin() as c:
            c.execute(sa.text("REVOKE UPDATE(id) ON public.sales_targets FROM pdd_ingest"))
        store = SalesTargetStore(writer, archive_root=tmp_path / "writer", basis="original_gmv")
        with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
            store.run(lambda hb: candidate())
        with sales_pg.begin() as c:
            assert c.execute(sa.text("SELECT count(*) FROM sales_targets")).scalar_one() == 0
            c.execute(sa.text("GRANT UPDATE(id) ON public.sales_targets TO pdd_ingest"))
        result = store.run(lambda hb: candidate())
        assert result.status == "succeeded"
        with writer.connect() as c:
            assert c.execute(sa.text("SELECT current_user")).scalar_one() == "pdd_ingest"
            with pytest.raises(sa.exc.ProgrammingError):
                c.execute(sa.text("UPDATE sales_targets SET target_amount=0"))
    finally:
        writer.dispose()


def test_pg_failed_database_replay_rolls_back_whole_candidate(sales_pg, tmp_path, monkeypatch):
    setup(sales_pg, tmp_path)
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="original_gmv")
    first = store.run(lambda hb: candidate())

    def fail(c, version, input):
        raise SalesError("INJECTED_REPLAY_FAILURE")

    monkeypatch.setattr(store, "_validate_stored", fail)
    changed = rows()
    changed[1]["gmv目标"] = "3"
    with pytest.raises(SalesError, match="INJECTED_REPLAY_FAILURE"):
        store.run(lambda hb: candidate(changed))
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text(
                    "SELECT version_id FROM current_datasets WHERE dataset_code='sales_targets'"
                )
            ).scalar_one()
            == first.version_id
        )
        assert c.execute(sa.text("SELECT count(*) FROM sales_targets")).scalar_one() == 12


def test_same_row_count_but_changed_value_never_passes_double_read():
    count = 0

    def page(offset):
        nonlocal count
        count += 1
        source = rows()
        source[1]["gmv目标"] = str(count)
        return source, manifest(len(source))

    with pytest.raises(SalesError, match="CHANGED_DURING_READ"):
        FeishuTargetReader.for_test(page).read_stable(basis="original_gmv")


@pytest.mark.parametrize("basis", ["base", "stretch"])
def test_other_target_definitions_are_rejected(basis):
    with pytest.raises(SalesError, match="INVALID_TARGET_BASIS"):
        normalize_targets(rows(), basis=basis)


def test_pg_legacy_revision_is_not_interpreted_as_original_target(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    # 旧数据版本只有遗留定义元数据，不伪造新源可用状态。
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO dataset_versions "
                "(id,dataset_code,origin_kind,state,source_system,source_locator,"
                "row_schema_revision,interpretation_ref,producer_revision,"
                "retained_input_ref,received_at) "
                "VALUES ('legacy-target','sales_targets','source','draft','feishu',"
                "'legacy-source','m3-channel-gmv-targets-v1:base','legacy','legacy','legacy',clock_timestamp())"
            )
        )
        c.execute(
            sa.text(
                "UPDATE dataset_versions SET state='validated',content_fingerprint='legacy',"
                "validation_evidence_ref='legacy' WHERE id='legacy-target'"
            )
        )
        c.execute(sa.text("UPDATE dataset_versions SET state='published' WHERE id='legacy-target'"))
        c.execute(
            sa.text(
                "INSERT INTO current_datasets(dataset_code,version_id) "
                "VALUES ('sales_targets','legacy-target')"
            )
        )
    response = client.get(
        "/api/v1/sales/targets",
        params={"month": "2026-10", "data_version": params(client)["data_version"]},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TARGET_DEFINITION_INCOMPATIBLE"


@pytest.mark.parametrize("basis", ["base", "stretch"])
def test_api_rejects_old_target_basis(sales_pg, tmp_path, basis):
    client = setup(sales_pg, tmp_path)
    response = client.get(
        "/api/v1/sales/targets",
        params={
            "month": "2026-10",
            "data_version": params(client)["data_version"],
            "target_basis": basis,
        },
    )
    assert response.status_code == 422
