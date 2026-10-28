"""新正式GMV目标：合成源与真实隔离PG，不访问飞书或项目库。"""

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
from app.modules.sales.targets import SalesTargetStore, normalize_targets
from tests.test_sales_extended_api import params, setup

pytest_plugins = ["tests.sales_pg_support"]


def rows():
    return [
        {
            "record_id": f"month-{year}-{month}",
            "渠道": ["拼多多"],
            "年份": [f"{year}年"],
            "月份": [f"{month}月"],
            "GMV基础": "2.5",
            "GMV冲刺": "8.5",
            "记录名称": "渠道月目标",
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
    return reader.read_stable(basis="base")


def test_exact_ten_thousand_units_and_fiscal_exclusion():
    result = normalize_targets(rows(), basis="base")
    assert len(result.rows) == 12 and len(result.raw_rows) == 13
    assert result.rows[0]["period_start"] == date(2026, 2, 1)
    assert result.rows[-1]["period_end"] == date(2027, 1, 31)
    assert all(r["target_amount"] == Decimal("25000") for r in result.rows)
    changed_stretch = rows()
    changed_stretch[1]["GMV冲刺"] = None
    assert normalize_targets(changed_stretch, basis="base").fingerprint == result.fingerprint


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate_month",
        "duplicate_excluded_month",
        "duplicate_id",
        "multiple_years",
        "bad_amount",
        "outside_period",
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
        source[1]["GMV基础"] = "NaN"
    else:
        source[1]["年份"] = ["2028年"]
    with pytest.raises(SalesError):
        normalize_targets(source, basis="base")


@pytest.mark.parametrize("defect", ["field_type", "timezone", "pagination", "revision"])
def test_source_contract_or_pagination_change_blocks_read(defect):
    def page(offset):
        meta = manifest(1)
        if defect == "field_type":
            meta["columns"]["GMV基础"]["field_type"] = "text"
        elif defect == "timezone":
            meta["timezone"] = "UTC"
        elif defect == "pagination":
            meta.update(has_more=True, next_offset=offset)
        else:
            meta.update(has_more=offset == 0, next_offset=1, rev=offset + 1)
        return [rows()[1]], meta

    with pytest.raises(SalesError):
        FeishuTargetReader.for_test(page).read_stable(basis="base")


def test_pg_publication_replays_members_and_is_semantically_idempotent(sales_pg, tmp_path):
    client = setup(sales_pg, tmp_path)
    old = params(client)["data_version"]
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="base")
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
    assert data["month_target"] == "25000.00" and data["annual_target"] == "300000.00"
    assert data["month_actual"] == "100.00" and data["annual_actual"] == "100.00"
    assert data["month_ratio"] == "0.004000000000"
    assert data["target_basis"] == "base" and data["state"] == "partial"
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


def test_pg_missing_month_and_real_zero_do_not_create_full_annual_or_zero_denominator(
    sales_pg, tmp_path
):
    client = setup(sales_pg, tmp_path)
    source = [r for r in rows() if r["月份"] != ["3月"]]
    next(r for r in source if r["月份"] == ["10月"])["GMV基础"] = 0
    SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="base").run(
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
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="base")
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
    assert data["month_target"] == "25000.00"
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
        store = SalesTargetStore(writer, archive_root=tmp_path / "writer", basis="base")
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
    store = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets", basis="base")
    first = store.run(lambda hb: candidate())

    def fail(c, version, input):
        raise SalesError("INJECTED_REPLAY_FAILURE")

    monkeypatch.setattr(store, "_validate_stored", fail)
    changed = rows()
    changed[1]["GMV基础"] = "3"
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
        source[1]["GMV基础"] = str(count)
        return source, manifest(len(source))

    with pytest.raises(SalesError, match="CHANGED_DURING_READ"):
        FeishuTargetReader.for_test(page).read_stable(basis="base")
