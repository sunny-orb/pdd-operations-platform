"""F01 原公式进入同版本预测；只用真实隔离 PG，生产来源由合成分页替代。"""

import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.connectors.sales_feishu import FeishuRefundReader
from app.modules.sales.archive_replay import restore_candidate
from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.datasets import REFUND_BASE, REFUND_SHOPS, REFUND_TABLE, SPECS
from app.modules.sales.f01_gmv import F01_GMV_REVISION
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import SHOPS, approved_window
from app.modules.sales.storage import ShopDailyStore
from app.modules.sales.targets import MEMBERS, SalesTargetStore
from migrations_v2.local import ROOT
from tests.test_project_sync_runtime import paths_for
from tests.test_sales_targets import candidate as target_candidate
from tests.test_sales_targets import rows as target_source_rows

pytest_plugins = ["tests.sales_pg_support"]
MONTH = date(2026, 11, 1)
WINDOW = ReadWindow(MONTH, date(2026, 11, 8), tuple(SHOPS))


def source_days(count):
    names = {sid: name for name, sid in REFUND_SHOPS.items()}
    return [
        {
            "record_id": f"{shop}-{day}",
            "日期": f"2026-11-{day:02}T00:00:00+08:00",
            "平台": "拼多多",
            "部门": "市场部",
            "店铺": names[shop],
            "统计退款金额": 0,
            "统计金额": 10,
            "做单金额": 1,
            "销售收入（GMV）": 9,
            "退款占比": 0,
        }
        for day in range(1, count + 1)
        for shop in MEMBERS
    ]


def read_source(rows):
    def page(_window, _offset):
        return rows, {
            "base_token": REFUND_BASE,
            "table_id": REFUND_TABLE,
            "rev": 123,
            "timezone": "Asia/Shanghai",
            "records_count": len(rows),
            "has_more": False,
        }

    return FeishuRefundReader.for_test(page, include_gmv=True).read_stable(WINDOW)


def test_f01_preserves_the_reported_formula_and_raw_inputs_in_stable_revision():
    rows = source_days(1)
    rows[0].update({"统计金额": None, "做单金额": None, "销售收入（GMV）": 0})
    candidate = read_source(rows)
    by_id = {row["source_record_id"]: row for row in candidate.rows}
    zero = by_id[rows[0]["record_id"]]
    assert zero["source_reported_amount"] is zero["source_adjustment_amount"] is None
    assert zero["source_reported_gmv"] == 0
    assert by_id[rows[1]["record_id"]]["source_reported_gmv"] == Decimal("9")
    assert by_id[rows[1]["record_id"]]["source_adjustment_amount"] == Decimal("1")
    assert (
        candidate.fingerprint
        != read_source([dict(rows[0], **{"销售收入（GMV）": None}), *rows[1:]]).fingerprint
    )


@contextmanager
def read_transaction(engine):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        c.exec_driver_sql("SET TRANSACTION READ ONLY")
        yield c


def f01_store(engine, tmp_path):
    return ShopDailyStore(
        engine, archive_root=tmp_path, approved=WINDOW, spec=SPECS["shop_refunds"]
    )


def test_forecast_uses_contiguous_six_store_f01_excludes_today_and_freezes_versions(
    sales_pg, tmp_path
):
    from app.modules.sales.forecast_inputs import read_forecast_inputs

    rows = source_days(4)
    for row in rows[:6]:
        row.update({"统计金额": 0, "销售收入（GMV）": 0, "做单金额": None})
    # 一店填了零公式，基础尚空；其后完整的第三天也不能接至月初。
    rows[6].update({"统计金额": None, "销售收入（GMV）": 0, "做单金额": None})
    store = f01_store(sales_pg, tmp_path)
    first = store.publish_candidate(read_source(rows))
    targets = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets")
    targets.run(lambda heartbeat: target_candidate())
    with read_transaction(sales_pg) as c:
        result = read_forecast_inputs(c, month=MONTH, as_of=date(2026, 11, 4))
        assert result["cutoff"] == MONTH
        assert result["daily"] == {MONTH: Decimal(0)}
        assert result["target"] == Decimal("25000.25")
        assert result["versions"]["shop_refunds"] == first.version_id
        assert result["coverage"]["sales_observed_through"] == "2026-11-03"
        assert result["coverage"]["source_complete_through"] is None
        assert result["coverage"]["sales_gaps"][0]["invalid_amount_shop_ids"] == [MEMBERS[0]]
        assert result["promotion"] == {}
        rows[6].update({"统计金额": 10, "销售收入（GMV）": 9, "做单金额": 1})
        second = store.publish_candidate(read_source(rows))
        assert second.version_id != first.version_id
        assert read_forecast_inputs(c, month=MONTH, as_of=date(2026, 11, 4)) == result
    with read_transaction(sales_pg) as c:
        result = read_forecast_inputs(c, month=MONTH, as_of=date(2026, 11, 4))
        assert result["cutoff"] == date(2026, 11, 3)
        assert sum(result["daily"].values()) == Decimal("108")
        assert result["versions"]["shop_refunds"] == second.version_id


def test_missing_day_missing_store_formula_mismatch_and_legacy_archive(sales_pg, tmp_path):
    from app.modules.sales.forecast_inputs import read_forecast_inputs

    rows = source_days(4)
    store = f01_store(sales_pg, tmp_path)
    # 模拟历史七字段发布与重放；已发布记录永远不会通过算式倒填新增列。
    legacy_rows = [
        {key: value for key, value in row.items() if key not in ("做单金额", "销售收入（GMV）")}
        for row in rows[:6]
    ]
    old = normalize_source("shop_refunds", legacy_rows, WINDOW)
    old_version = store.publish_candidate(old)
    assert restore_candidate(tmp_path / old_version.version_id).rows == old.rows
    with read_transaction(sales_pg) as c:
        result = read_forecast_inputs(c, month=MONTH, as_of=date(2026, 11, 3))
        assert result["cutoff"] is result["target"] is None
        assert result["coverage"]["sales_state"] == "projection_unavailable"
        assert result["coverage"]["sales_checked_through"] is None
    del rows[6:12]  # 第二天整日未报。
    del rows[7]  # 第三天缺一家，且另一家金额不合源公式。
    rows[6]["销售收入（GMV）"] = Decimal("0.88")
    current = read_source(rows)
    stored = store.publish_candidate(current)
    assert restore_candidate(tmp_path / stored.version_id).rows == current.rows
    repeated = store.publish_candidate(read_source(rows[::-1]))
    assert repeated.reused and repeated.version_id == stored.version_id
    with read_transaction(sales_pg) as c:
        result = read_forecast_inputs(c, month=MONTH, as_of=date(2026, 11, 4))
        assert result["cutoff"] == MONTH
        assert result["daily"] == {MONTH: Decimal("54")}
        assert result["coverage"]["sales_gaps"][0]["missing_shop_ids"] == list(MEMBERS)
        assert result["coverage"]["sales_gaps"][1]["invalid_amount_shop_ids"] == [MEMBERS[0]]
        assert result["coverage"]["sales_gaps"][1]["missing_shop_ids"] == [MEMBERS[1]]
        assert result["coverage"]["target_state"] == "source_unavailable"
        empty = read_forecast_inputs(c, month=MONTH, as_of=MONTH)
        assert empty["daily"] == {}
        assert empty["cutoff"] is None


def test_target_conflict_withholds_only_target_and_duplicate_day_never_counts(sales_pg, tmp_path):
    from app.modules.sales.forecast_inputs import read_forecast_inputs

    rows = source_days(2)
    extra = dict(rows[-1], record_id="extra-source-key")
    with pytest.raises(SalesError, match="SOURCE_DUPLICATE_BUSINESS_KEY"):
        read_source([*rows, extra])
    # 模拟技术导入绕过正常重复检查的版本。数据库本身只约束版本内来源行键。
    candidate = read_source(rows)
    duplicate = dict(candidate.rows[-1], source_row_key="extra", source_record_id="extra")
    unexpected = replace(candidate, rows=(*candidate.rows, duplicate))
    f01_store(sales_pg, tmp_path).publish_candidate(unexpected)
    targets = SalesTargetStore(sales_pg, archive_root=tmp_path / "targets")
    source = target_source_rows()
    targets.run(lambda _: target_candidate(source))
    with pytest.raises(SalesError, match="TARGET_MONTH_CONFLICT"):
        targets.run(lambda _: target_candidate([*source, dict(source[1], record_id="conflict")]))
    with read_transaction(sales_pg) as c:
        result = read_forecast_inputs(c, month=MONTH, as_of=date(2026, 11, 3))
        assert result["target"] is None
        assert result["coverage"]["target_state"] == "source_conflict"
        assert result["daily"] == {MONTH: Decimal("54")}
        assert result["coverage"]["sales_gaps"][0]["duplicate_shop_ids"] == [duplicate["shop_id"]]
        assert c.scalar(sa.text("SHOW transaction_read_only")) == "on"


def test_revision_22_preserves_old_rows_and_refuses_to_discard_new_inputs(sales_pg, tmp_path):
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    config.attributes["v2_expected_database"] = sales_pg.url.database
    with sales_pg.begin() as c:
        config.attributes["connection"] = c
        command.downgrade(config, "20261127_21")
    legacy = normalize_source("shop_refunds", source_days(1), WINDOW)
    version = f01_store(sales_pg, tmp_path).publish_candidate(legacy).version_id
    with sales_pg.begin() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "20261127_22")
        assert c.execute(
            sa.text("""SELECT count(*),count(source_reported_gmv),count(source_adjustment_amount),
                 sum(source_reported_amount) FROM public.shop_refund_records
                 WHERE dataset_version_id=:version"""),
            {"version": version},
        ).one() == (6, 0, 0, Decimal("60"))
    f01_store(sales_pg, tmp_path).publish_candidate(read_source(source_days(1)))
    with (
        pytest.raises(sa.exc.DBAPIError, match="F01_GMV_DOWNGRADE_HAS_DATA"),
        sales_pg.begin() as c,
    ):
        config.attributes["connection"] = c
        command.downgrade(config, "20261127_21")
    with read_transaction(sales_pg) as c:
        assert (
            c.scalar(sa.text("SELECT version_num FROM public.alembic_version_v2")) == "20261127_22"
        )


@pytest.mark.parametrize("revision", ["20261118_20", "20261127_22", "20261128_23"])
def test_f01_scheduler_and_manual_cli_publish_and_replay_the_supported_projection(
    sales_pg, tmp_path, monkeypatch, capsys, revision
):
    from app.connectors import sales_feishu
    from app.jobs import project_runtime, sync_sales_sources
    from app.modules.sales.datasets import EXTENDED_REVISION, REFUND_FIELDS
    from tests.sales_pg_support import downgrade_to

    with sales_pg.begin() as c:
        downgrade_to(c, revision)
        assert c.scalar(sa.text("SELECT version_num FROM public.alembic_version_v2")) == revision
    expected_fields = list(REFUND_FIELDS)
    if revision in ("20261127_22", "20261128_23"):
        expected_fields += ["fldax7fYxe", "fldQmlTprE"]
    source = source_days(1)
    labels = [
        "日期",
        "平台",
        "部门",
        "店铺",
        "统计退款金额",
        "统计金额",
        "退款占比",
        "销售收入（GMV）",
        "做单金额",
    ]
    kinds = ["datetime", "text", "text", "text", "number", "number", "formula", "formula", "number"]
    columns = dict(
        zip(
            [*REFUND_FIELDS, "fldax7fYxe", "fldQmlTprE"],
            zip(labels, kinds, strict=True),
            strict=True,
        )
    )
    calls = []

    def controlled_page(args, *, cwd, **kwargs):
        selected = [args[i + 1] for i, flag in enumerate(args) if flag == "--field-id"]
        offset = int(args[args.index("--offset") + 1])
        calls.append((selected, offset))
        schema = {columns[k][0]: {"field_id": k, "field_type": columns[k][1]} for k in selected}
        records = source[offset : offset + 3]
        out = Path(cwd) / args[args.index("--output") + 1]
        out.write_text(
            "\n".join(
                json.dumps({"record_id": r["record_id"], **{label: r[label] for label in schema}})
                for r in records
            )
        )
        out.with_suffix(".manifest.json").write_text(
            json.dumps(
                {
                    "base_token": REFUND_BASE,
                    "table_id": REFUND_TABLE,
                    "rev": 12,
                    "timezone": "Asia/Shanghai",
                    "columns": schema,
                    "records_count": len(records),
                    "has_more": offset == 0,
                    "next_offset": 3,
                }
            )
        )
        return CompletedProcess([], 0)

    monkeypatch.setattr(sales_feishu, "run_source_cli", controlled_page)
    monkeypatch.setattr(sync_sales_sources, "runtime_engine", lambda: sales_pg)
    monkeypatch.setattr(sync_sales_sources, "ROOT", tmp_path)
    paths = paths_for(tmp_path)
    result = project_runtime.execute_project(
        sales_pg,
        "forecast-input-test",
        paths=paths,
        only=("shop_refunds",),
        max_attempts=1,
        warehouse_password=lambda: pytest.fail("F01 不需要数仓连接"),
    )
    assert result["status"] == "succeeded", result
    capsys.readouterr()
    window = approved_window("shop_refunds")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync",
            "--dataset",
            "shop_refunds",
            "--from",
            str(window.start),
            "--to",
            str(window.end),
            "--publish",
        ],
    )
    assert sync_sales_sources.main() == 0, capsys.readouterr().out
    manual = json.loads(capsys.readouterr().out)
    assert manual["reused"] and manual["status"] == "succeeded"
    sys.argv[-1] = "--verify-only"
    assert sync_sales_sources.main() == 0, capsys.readouterr().out
    verified = json.loads(capsys.readouterr().out)
    assert calls == [(expected_fields, n) for n in (0, 3, 0, 3) * 3]
    with read_transaction(sales_pg) as c:
        version = c.execute(
            sa.text("""SELECT v.row_schema_revision,v.retained_input_ref
            FROM public.current_datasets d JOIN public.dataset_versions v ON v.id=d.version_id
            WHERE d.dataset_code='shop_refunds' AND d.version_id=:v"""),
            {"v": manual["version_id"]},
        ).one()
    saved = restore_candidate(Path(version.retained_input_ref).parent)
    assert verified["fingerprint"] == saved.fingerprint and verified["rows"] == len(source)
    if revision == "20261118_20":
        old = normalize_source("shop_refunds", source, window)
        assert version.row_schema_revision == EXTENDED_REVISION
        assert (saved.rows, saved.fingerprint) == (old.rows, old.fingerprint)
        assert "gmv_projection" not in saved.source_metadata
    else:
        assert version.row_schema_revision == F01_GMV_REVISION
        assert [r["source_reported_gmv"] for r in saved.rows] == [Decimal(9)] * 6
    assert saved.source_metadata["field_mapping"].keys() == set(expected_fields)
