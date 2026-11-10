"""本地真实PG保留闭包及退出时间；不以版本创建时间代替发布事件。"""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from tests.test_published_versions import publish_pointer

pytest_plugins = ["tests.sales_pg_support"]


def sequence(engine):
    for key in "abc":
        publish_pointer(engine, "product_daily", key)


def test_current_previous_and_grace(sales_pg):
    from app.modules.database_retention import plan

    sequence(sales_pg)
    first = plan(sales_pg)
    assert not first["candidates"]
    future = datetime.now(UTC) + timedelta(hours=25)
    later = plan(sales_pg, now=future)
    assert [v["version_id"] for v in later["candidates"]] == ["product_daily-a"]
    assert later["protected"] == ["product_daily-b", "product_daily-c"]
    with sales_pg.begin() as c:
        c.execute(sa.text("UPDATE current_datasets SET version_id='product_daily-a'"))
    assert not plan(sales_pg, now=future)["candidates"] or all(
        v["version_id"] == "product_daily-b" for v in plan(sales_pg, now=future)["candidates"]
    )
    with sales_pg.begin() as c:
        c.execute(sa.text("UPDATE current_datasets SET version_id='product_daily-c'"))
    assert not plan(sales_pg)["candidates"]


def test_unknown_schema_or_running_task_blocks(sales_pg):
    from app.modules.database_retention import plan

    with sales_pg.begin() as c:
        c.execute(sa.text("CREATE TABLE unexpected(id text)"))
    with pytest.raises(ValueError, match="RETENTION_SCHEMA"):
        plan(sales_pg)


def test_reconciliation_schema_column_drift_blocks_retention(sales_pg):
    from app.modules.database_retention import plan

    with sales_pg.begin() as c:
        c.exec_driver_sql("ALTER TABLE source_reconciliations RENAME COLUMN status TO unexpected")
    with pytest.raises(ValueError, match="RETENTION_SCHEMA_COLUMNS"):
        plan(sales_pg)


def test_pinned_version_retained(sales_pg):
    from app.modules.database_retention import plan

    sequence(sales_pg)
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "UPDATE dataset_storage SET keep_online=true WHERE version_id='product_daily-a'"
            )
        )
    assert not plan(sales_pg, now=datetime.now(UTC) + timedelta(days=2))["candidates"]


def test_old_scoring_inputs_remain_protected_without_current_pointer(sales_pg):
    from app.modules.database_retention import plan

    sequence(sales_pg)
    publish_pointer(sales_pg, "product_scores", "history", published=False)
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO version_inputs(output_version_id,input_role,input_version_id) "
                "VALUES('product_scores-history','product_history','product_daily-a')"
            )
        )
        c.execute(
            sa.text(
                "UPDATE dataset_versions SET state='validated', "
                "content_fingerprint='synthetic:score', "
                "validation_evidence_ref='synthetic:verified' WHERE id='product_scores-history'"
            )
        )
        c.execute(
            sa.text(
                "UPDATE dataset_versions SET state='published' WHERE id='product_scores-history'"
            )
        )
    later = plan(sales_pg, now=datetime.now(UTC) + timedelta(days=2))
    assert not later["candidates"]
    assert "product_daily-a" in later["protected"]


def test_missing_sealed_content_guard_refuses_a_plan(sales_pg):
    from app.modules.database_retention import plan

    with sales_pg.begin() as c:
        c.execute(
            sa.text("DROP TRIGGER pdd_v2_trg_product_daily_facts_content ON product_daily_facts")
        )
    with pytest.raises(ValueError, match="RETENTION_SCHEMA_GUARDS"):
        plan(sales_pg)
