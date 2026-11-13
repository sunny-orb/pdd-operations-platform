"""16索引的真实PG契约、固定输入结果和事务回退；仅随机合成库。"""

import io
from datetime import date
from decimal import Decimal, localcontext

import pytest
from alembic import command
from alembic.config import Config

from tests.test_aftersales_api import publish
from tests.test_deployment_database import ROOT, allocated_database, load_script

__all__ = ["allocated_database"]
INDEX = "ix_return_records_summary_window"


def revision_sql(start, target, *, downgrade=False):
    output = io.StringIO()
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"), output_buffer=output)
    (command.downgrade if downgrade else command.upgrade)(config, f"{start}:{target}", sql=True)
    return output.getvalue()


def install(engine, target="20261111_16"):
    renderer = load_script("summary_index_renderer", "render-db-schema.py")
    with engine.connect() as c:
        options = (
            {"runtime_account": "pdd_admin", "database": engine.url.database}
            if target == "head"
            else {}
        )
        c.connection.driver_connection.execute(
            renderer.render_sql(target, **options), prepare=False
        )
    return renderer


def index_contract(c):
    return c.exec_driver_sql(
        "SELECT i.indisunique,i.indisvalid,i.indisready,i.indnkeyatts,i.indnatts,"
        "pg_get_indexdef(i.indexrelid),pg_get_expr(i.indpred,i.indrelid) "
        "FROM pg_index i WHERE i.indexrelid=to_regclass(%s)",
        (f"public.{INDEX}",),
    ).first()


def test_head_index_contract_and_downgrade_preserve_facts(allocated_database):
    install(allocated_database)
    publish(allocated_database)
    with allocated_database.connect() as c:
        contract = index_contract(c)
        assert contract is not None, "head缺少已审的售后历史汇总期间索引"
        assert contract[:5] == (False, True, True, 3, 3)
        assert contract[5] == (
            "CREATE INDEX ix_return_records_summary_window ON public.return_records USING btree "
            "(dataset_version_id, period_start, period_end) WHERE "
            "((row_kind = 'detail'::text) AND (period_start IS NOT NULL) "
            "AND (period_end IS NOT NULL))"
        )
        before = c.exec_driver_sql("SELECT to_jsonb(t) FROM return_records t ORDER BY id").all()
        acl = c.exec_driver_sql(
            "SELECT relacl::text FROM pg_class WHERE oid='return_records'::regclass"
        ).scalar()
        c.connection.driver_connection.execute(
            revision_sql("20261111_16", "20261111_15", downgrade=True), prepare=False
        )
        assert index_contract(c) is None
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == "20261111_15"
        )
        assert (
            c.exec_driver_sql("SELECT to_jsonb(t) FROM return_records t ORDER BY id").all()
            == before
        )
        assert (
            c.exec_driver_sql(
                "SELECT relacl::text FROM pg_class WHERE oid='return_records'::regclass"
            ).scalar()
            == acl
        )
        assert c.exec_driver_sql("SELECT to_regclass('ix_return_records_query_14')").scalar()
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar() == "preserved"
        c.connection.driver_connection.execute(
            revision_sql("20261111_15", "20261111_16"), prepare=False
        )
        assert index_contract(c) == contract


def test_index_preserves_exact_null_zero_and_period_summaries(allocated_database):
    install(allocated_database, "20261111_15")
    rows = [
        dict(
            id="zero",
            source_row_key="zero",
            actual_returned_qty=Decimal("0"),
            actual_inbound_amount=Decimal("0"),
        ),
        dict(
            id="null", source_row_key="null", actual_returned_qty=None, actual_inbound_amount=None
        ),
        dict(
            id="large",
            source_row_key="large",
            actual_returned_qty=Decimal("1.5"),
            actual_inbound_amount=Decimal("123456789012345678901234567890.1234567890123456789"),
        ),
        dict(
            id="summary",
            source_row_key="summary",
            row_kind="subtotal",
            actual_returned_qty=Decimal("999"),
            actual_inbound_amount=Decimal("999"),
        ),
        dict(
            id="undated",
            source_row_key="undated",
            date_status="missing",
            period_start=None,
            period_end=None,
            actual_returned_qty=Decimal("999"),
            actual_inbound_amount=Decimal("999"),
        ),
        dict(
            id="outside",
            source_row_key="outside",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 1),
            actual_returned_qty=Decimal("999"),
            actual_inbound_amount=Decimal("999"),
        ),
    ]
    publish(allocated_database, returns=rows)
    statement = """SELECT count(*),min(period_start),max(period_end),
      CASE WHEN count(actual_inbound_amount)=count(*) THEN sum(actual_inbound_amount) END,
      sum(actual_inbound_amount),sum(actual_returned_qty)
      FROM return_records WHERE dataset_version_id=%s AND row_kind='detail'
      AND period_start>=%s AND period_end<=%s
      AND period_start IS NOT NULL AND period_end IS NOT NULL"""
    params = ("jst_returns1", date(2026, 9, 28), date(2026, 10, 27))
    with allocated_database.connect() as c:
        before = c.exec_driver_sql(statement, params).one()
        assert before == (
            3,
            date(2026, 10, 27),
            date(2026, 10, 27),
            None,
            Decimal("123456789012345678901234567890.1234567890123456789"),
            Decimal("1.5"),
        )
        c.connection.driver_connection.execute(
            revision_sql("20261111_15", "20261111_16"), prepare=False
        )
        assert c.exec_driver_sql(statement, params).one() == before
        assert c.exec_driver_sql(statement, ("missing", *params[1:])).one() == (
            0,
            None,
            None,
            None,
            None,
            None,
        )
        assert index_contract(c) is not None


def test_same_name_wrong_index_is_not_silently_accepted(allocated_database):
    install(allocated_database, "20261111_15")
    import psycopg

    with allocated_database.connect() as c:
        raw = c.connection.driver_connection
        c.exec_driver_sql(f"CREATE INDEX {INDEX} ON return_records (dataset_version_id)")
        before = index_contract(c)
        guards = guard_definitions(c)
        with pytest.raises(psycopg.errors.DuplicateTable), raw.transaction():
            raw.execute(revision_sql("20261111_15", "20261111_16"), prepare=False)
        assert index_contract(c) == before
        assert guard_definitions(c) == guards
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == "20261111_15"
        )


def test_index_statement_timeout_rolls_back_the_entire_increment(allocated_database):
    install(allocated_database, "20261111_15")
    import psycopg

    with allocated_database.connect() as c, allocated_database.connect() as blocker:
        raw = c.connection.driver_connection
        guards = guard_definitions(c)
        with blocker.connection.driver_connection.transaction():
            blocker.exec_driver_sql("LOCK TABLE return_records IN ROW EXCLUSIVE MODE")
            with pytest.raises(psycopg.errors.QueryCanceled), raw.transaction():
                raw.execute("SET LOCAL statement_timeout='50ms'")
                raw.execute("SET LOCAL lock_timeout='5s'")
                raw.execute(revision_sql("20261111_15", "20261111_16"), prepare=False)
        assert index_contract(c) is None
        assert guard_definitions(c) == guards
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == "20261111_15"
        )


def test_existing_and_new_unbounded_numeric_remain_exact(allocated_database):
    """15合法大数可升级；16仍能写入合法大数，既有数值域不能因索引缩小。"""
    import hashlib

    install(allocated_database, "20261111_15")
    digits = "".join(str(int(hashlib.sha256(str(i).encode()).hexdigest(), 16)) for i in range(160))
    amount = Decimal(digits)
    publish(
        allocated_database,
        returns=[
            dict(
                id="wide",
                source_row_key="wide",
                actual_returned_qty=None,
                actual_inbound_amount=amount,
            )
        ],
    )
    with allocated_database.connect() as c:
        raw = c.connection.driver_connection
        before = c.exec_driver_sql("SELECT sum(actual_inbound_amount) FROM return_records").scalar()
        assert before == amount
        with raw.transaction():
            raw.execute(revision_sql("20261111_15", "20261111_16"), prepare=False)
        assert (
            c.exec_driver_sql("SELECT sum(actual_inbound_amount) FROM return_records").scalar()
            == before
        )
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == "20261111_16"
        )
        assert index_contract(c) is not None
    second = Decimal(digits + "9")
    publish(
        allocated_database,
        "new-wide",
        revision_no=2,
        returns=[
            dict(
                id="new-wide",
                source_row_key="new-wide",
                actual_returned_qty=second,
                actual_inbound_amount=second,
            )
        ],
    )
    with allocated_database.connect() as c:
        assert c.exec_driver_sql(
            "SELECT actual_returned_qty,actual_inbound_amount "
            "FROM return_records WHERE id='new-wide'"
        ).one() == (second, second)
        with localcontext() as ctx:
            ctx.prec = len(digits) + 10
            assert (
                c.exec_driver_sql("SELECT sum(actual_inbound_amount) FROM return_records").scalar()
                == amount + second
            )


def test_invalid_index_is_rejected_even_if_definition_matches(allocated_database):
    """仅随机库模拟无效目录状态；定义哈希相同也不能签发已升级。"""
    from migrations_v2.company_db_objects import verify_application

    install(allocated_database)
    with allocated_database.connect() as c:
        raw = c.connection.driver_connection
        verify_application(raw, "20261111_16", has_rpa=False)
        with raw.transaction():
            raw.execute(
                "UPDATE pg_index SET indisvalid=false WHERE indexrelid=to_regclass(%s)",
                (f"public.{INDEX}",),
            )
            with pytest.raises(ValueError, match="DEPLOY_SCHEMA_MISMATCH:index_state"):
                verify_application(raw, "20261111_16", has_rpa=False)


def guard_definitions(c):
    return c.exec_driver_sql(
        "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
        "WHERE pronamespace='public'::regnamespace "
        "AND proname IN ('pdd_retention_context','pdd_retention_item_guard') ORDER BY proname"
    ).all()


def test_retention_head_guards_block_unsafe_transitions_and_preserve_receipts(
    allocated_database, tmp_path, monkeypatch
):
    """真实15/16归档演练，不手填回执、不绕过封版及维护触发器。"""
    import os

    import psycopg

    from app.modules import database_archive as archive
    from app.modules import database_retention as retention
    from tests.test_database_archive import old_candidate

    monkeypatch.setattr(archive, "HEAD", "20261111_16")
    monkeypatch.setattr(retention, "HEAD", "20261111_16")
    install(allocated_database, "20261111_15")
    engine = allocated_database.execution_options(isolation_level="READ COMMITTED")
    old_candidate(engine)
    key = os.urandom(32)
    with monkeypatch.context() as previous:
        previous.setattr(archive, "HEAD", "20261111_15")
        previous.setattr(retention, "HEAD", "20261111_15")
        selected = retention.plan(engine)
        bundle15 = archive.export(engine, selected, tmp_path / "head15", key)
        archive.drill(bundle15, key)
        assert retention.apply(engine, selected, bundle15, key)["state"] == "offline"
        with allocated_database.connect() as c:
            raw = c.connection.driver_connection
            before = guard_definitions(c)
            with pytest.raises(
                psycopg.errors.RaiseException, match="SUMMARY_INDEX_UPGRADE_RESTORE_REQUIRED"
            ):
                raw.execute(revision_sql("20261111_15", "20261111_16"), prepare=False)
            raw.rollback()
            assert guard_definitions(c) == before
            assert index_contract(c) is None
            assert (
                c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
                == "20261111_15"
            )
        assert retention.restore(engine, bundle15, key)["state"] == "online"

    with allocated_database.connect() as c:
        raw = c.connection.driver_connection
        c.exec_driver_sql(
            "INSERT INTO source_reconciliations(source_id,cycle_at,status) "
            "VALUES('product_daily',clock_timestamp(),'waiting')"
        )
        receipts15 = c.exec_driver_sql(
            "SELECT to_jsonb(t) FROM retention_archives t ORDER BY id"
        ).all()
        source = c.exec_driver_sql("SELECT to_jsonb(t) FROM source_reconciliations t").all()
        raw.execute(revision_sql("20261111_15", "20261111_16"), prepare=False)
        assert guard_definitions(c) == [
            (name, definition.replace("20261111_15", "20261111_16")) for name, definition in before
        ]
        with pytest.raises(ValueError, match="ARCHIVE"):
            archive.verify(bundle15, key)
        raw.execute(revision_sql("20261111_16", "20261111_15", downgrade=True), prepare=False)
        assert guard_definitions(c) == before
        assert (
            c.exec_driver_sql("SELECT to_jsonb(t) FROM retention_archives t ORDER BY id").all()
            == receipts15
        )
        assert c.exec_driver_sql("SELECT to_jsonb(t) FROM source_reconciliations t").all() == source
        raw.execute(revision_sql("20261111_15", "20261111_16"), prepare=False)

    selected = retention.plan(engine)
    bundle16 = archive.export(engine, selected, tmp_path / "head16", key)
    archive.drill(bundle16, key)
    assert retention.apply(engine, selected, bundle16, key)["state"] == "offline"
    for state in ("offline", "online"):
        with allocated_database.connect() as c:
            raw = c.connection.driver_connection
            guards16 = guard_definitions(c)
            with pytest.raises(
                psycopg.errors.RaiseException, match="SUMMARY_INDEX_DOWNGRADE_RESTORE_REQUIRED"
            ):
                raw.execute(
                    revision_sql("20261111_16", "20261111_15", downgrade=True), prepare=False
                )
            raw.rollback()
            assert guard_definitions(c) == guards16
            assert index_contract(c) is not None
            assert (
                c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
                == "20261111_16"
            )
        if state == "offline":
            assert retention.restore(engine, bundle16, key)["state"] == "online"
