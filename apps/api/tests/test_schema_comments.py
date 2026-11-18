"""中文目录说明：真实PG验证覆盖、原子回退及18归档格式兼容。"""

import os
import re

import psycopg
import pytest

from tests.test_deployment_database import allocated_database
from tests.test_deployment_upgrade import data_snapshot
from tests.test_return_summary_index import install, revision_sql
from tests.test_schema_delivery import catalog

__all__ = ["allocated_database"]
PREVIOUS = "20261114_18"
HEAD = "20261116_19"


def comments(c):
    return c.exec_driver_sql("""SELECT r.relname, a.attnum, a.attname,
        CASE WHEN a.attnum=0 THEN obj_description(r.oid,'pg_class')
        ELSE col_description(r.oid,a.attnum) END
        FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
        CROSS JOIN LATERAL (
          SELECT 0 AS attnum, NULL::text AS attname
          UNION ALL SELECT attnum,attname FROM pg_attribute
          WHERE attrelid=r.oid AND attnum>0 AND NOT attisdropped
        ) a WHERE n.nspname='public' AND r.relkind IN ('r','v')
        ORDER BY r.relname,a.attnum""").all()


def test_head_has_chinese_comments_for_every_table_view_and_column(allocated_database):
    install(allocated_database, "head")
    with allocated_database.connect() as c:
        values = comments(c)
        assert len([r for r in values if r[1] == 0]) == 52
        assert len([r for r in values if r[1] != 0]) == 862
        assert all(r[3] and re.search(r"[\u4e00-\u9fff]", r[3]) for r in values)
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == HEAD


def test_comment_upgrade_repeat_and_downgrade_preserve_schema_and_data(allocated_database):
    from tests.test_aftersales_api import publish

    install(allocated_database, PREVIOUS)
    publish(allocated_database)
    with allocated_database.connect() as c:
        before = catalog(c)
        descriptions = comments(c)
        data = data_snapshot(c)
        raw = c.connection.driver_connection
        raw.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
        assert catalog(c) == before
        assert data_snapshot(c) == data
        after = comments(c)
        changed = [(a, b) for a, b in zip(descriptions, after, strict=True) if a != b]
        assert len(changed) == 383
        assert all(a[3] is None and b[3] for a, b in changed)
        raw.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
        assert comments(c) == after
        raw.execute(revision_sql(HEAD, PREVIOUS, downgrade=True), prepare=False)
        assert comments(c) == descriptions
        assert catalog(c) == before
        assert data_snapshot(c) == data


@pytest.mark.parametrize("downgrade", [False, True])
def test_conflicting_comment_rejects_entire_migration(allocated_database, downgrade):
    install(allocated_database, HEAD if downgrade else PREVIOUS)
    with allocated_database.connect() as c:
        c.exec_driver_sql("COMMENT ON COLUMN weather_publications.received_at IS '另行核定说明'")
        before = comments(c)
        start, target = (HEAD, PREVIOUS) if downgrade else (PREVIOUS, HEAD)
        with pytest.raises(psycopg.Error, match="SCHEMA_COMMENT_CONFLICT"):
            c.connection.driver_connection.execute(
                revision_sql(start, target, downgrade=downgrade), prepare=False
            )
        c.connection.driver_connection.rollback()
        assert comments(c) == before
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == start


def test_comment_only_head_preserves_archive_restore_in_both_directions(
    allocated_database, tmp_path
):
    from app.modules import database_archive as archive
    from app.modules import database_retention as retention
    from tests.test_database_archive import old_candidate

    install(allocated_database, PREVIOUS)
    engine = allocated_database.execution_options(isolation_level="READ COMMITTED")
    old_candidate(engine)
    key = os.urandom(32)
    plan18 = retention.plan(engine)
    bundle18 = archive.export(engine, plan18, tmp_path / "before-comments", key)
    archive.drill(bundle18, key)
    assert retention.apply(engine, plan18, bundle18, key)["state"] == "offline"
    with allocated_database.connect() as c:
        c.connection.driver_connection.execute(revision_sql(PREVIOUS, HEAD), prepare=False)
        assert retention.validate_schema(c)
    assert retention.restore(engine, bundle18, key)["state"] == "online"
    plan19 = retention.plan(engine)
    bundle19 = archive.export(engine, plan19, tmp_path / "with-comments", key)
    assert archive.verify(bundle19, key)["schema_head"] == PREVIOUS
    archive.drill(bundle19, key)
    assert retention.apply(engine, plan19, bundle19, key)["state"] == "offline"
    with allocated_database.connect() as c:
        c.connection.driver_connection.execute(
            revision_sql(HEAD, PREVIOUS, downgrade=True), prepare=False
        )
    assert retention.restore(engine, bundle19, key)["state"] == "online"


def test_unregistered_head_is_not_accepted_as_metadata_compatible(allocated_database):
    from app.modules.database_retention import validate_schema

    install(allocated_database, "head")
    with allocated_database.connect() as c:
        c.exec_driver_sql("UPDATE alembic_version_v2 SET version_num='20261116_20'")
        with pytest.raises(ValueError, match="RETENTION_SCHEMA_HEAD"):
            validate_schema(c)
