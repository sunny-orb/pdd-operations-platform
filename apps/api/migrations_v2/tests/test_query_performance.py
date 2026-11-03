"""09日期查询索引：真实PG升降级保持内容、约束和权限。"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url, migrate  # noqa: E402

EXPECTED = {
    "ix_product_promotion_records_version_period_start": "product_promotion_records",
    "ix_product_region_records_version_period_start": "product_region_records",
}


def config(connection=None):
    value = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    if connection is not None:
        value.attributes["connection"] = connection
        value.attributes["v2_expected_database"] = connection.engine.url.database
    return value


class QueryPerformanceOfflineTests(unittest.TestCase):
    def test_review_sql_matches_09_migration(self):
        value = config()
        output = io.StringIO()
        value.output_buffer = output
        command.upgrade(value, "20261028_08:20261031_09", sql=True)
        expected = (
            "-- 仅审阅：Alembic 08→09日期查询索引；执行使用受控本地升级入口。\n"
            + output.getvalue().rstrip()
            + "\n"
        )
        self.assertEqual(
            (ROOT / "docs/database-schema/query-performance.sql").read_text(), expected
        )


@unittest.skipUnless(os.environ.get("PDD_V2_INTEGRATION") == "1", "需显式启用隔离PG验证")
class QueryPerformancePostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = "pdd_operations_v2_test_" + uuid.uuid4().hex[:12]
        cls.admin = sa.create_engine(
            local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
        )
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{cls.database}" OWNER pdd_admin')
        cls.engine = sa.create_engine(local_url(cls.database), hide_parameters=True)
        cls.addClassCleanup(cls.cleanup_database)
        with cls.engine.begin() as c:
            migrate(c)

    @classmethod
    def cleanup_database(cls):
        cls.engine.dispose()
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{cls.database}"')
        cls.admin.dispose()

    def test_current_head_has_two_nonunique_date_indexes(self):
        with self.engine.connect() as c:
            actual = dict(
                c.execute(
                    sa.text("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public'")
                ).all()
            )
            for name, table in EXPECTED.items():
                self.assertIn(name, actual)
                self.assertEqual(
                    actual[name],
                    f"CREATE INDEX {name} ON public.{table} USING btree "
                    "(dataset_version_id, period_start)",
                )

    def test_downgrade_reupgrade_preserves_rows_constraints_and_permissions(self):
        with self.engine.connect() as c, c.begin():
            c.execute(
                sa.text(
                    "INSERT INTO shops(id,shop_name,status) VALUES "
                    "('index-shop','合成索引店','active')"
                )
            )
            rows = c.execute(sa.text("SELECT * FROM shops")).all()
            constraints = c.execute(
                sa.text(
                    "SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2"
                )
            ).all()
            acl = c.execute(
                sa.text(
                    "SELECT relname,relacl::text FROM pg_class "
                    "WHERE relnamespace='public'::regnamespace "
                    "AND relkind='r' ORDER BY relname"
                )
            ).all()
            command.downgrade(config(c), "20261028_08")
            self.assertFalse(
                c.execute(
                    sa.text("SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE indexname=ANY(:names))"),
                    {"names": list(EXPECTED)},
                ).scalar_one()
            )
            migrate(c)
            self.assertEqual(c.execute(sa.text("SELECT * FROM shops")).all(), rows)
            self.assertEqual(
                c.execute(
                    sa.text(
                        "SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) "
                        "FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2"
                    )
                ).all(),
                constraints,
            )
            self.assertEqual(
                c.execute(
                    sa.text(
                        "SELECT relname,relacl::text FROM pg_class "
                        "WHERE relnamespace='public'::regnamespace "
                        "AND relkind='r' ORDER BY relname"
                    )
                ).all(),
                acl,
            )
            with self.assertRaises(DBAPIError), c.begin_nested():
                c.execute(
                    sa.text(
                        "INSERT INTO shops(id,shop_name,status) VALUES "
                        "('index-shop','重复','active')"
                    )
                )
            c.rollback()

    def test_failed_upgrade_transaction_restores_old_revision_and_indexes(self):
        with self.engine.begin() as c:
            command.downgrade(config(c), "20261028_08")
        try:
            with self.assertRaises(DBAPIError), self.engine.begin() as c:
                migrate(c)
                c.exec_driver_sql("SELECT 1/0")
            with self.engine.connect() as c:
                self.assertEqual(
                    c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar_one(),
                    "20261028_08",
                )
                self.assertFalse(
                    c.execute(
                        sa.text(
                            "SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE indexname=ANY(:names))"
                        ),
                        {"names": list(EXPECTED)},
                    ).scalar_one()
                )
        finally:
            with self.engine.begin() as c:
                migrate(c)

    def test_backup_timeout_releases_real_project_and_schema_locks(self):
        spec = importlib.util.spec_from_file_location(
            "index_timeout_test", ROOT / "scripts/upgrade-query-indexes-local.py"
        )
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        helper = entry.runner()
        original_snapshot = helper.snapshot
        with tempfile.TemporaryDirectory(dir=ROOT / "var") as directory:
            evidence = Path(directory) / "verified.json"
            evidence.write_text(
                json.dumps({"result": "passed", "fingerprint": helper.fingerprint()})
            )
            with (
                patch.object(entry, "runner", return_value=helper),
                patch.object(entry, "local_url", return_value=local_url(self.database)),
                patch.object(helper, "EVIDENCE", evidence),
                patch.object(
                    helper,
                    "snapshot",
                    side_effect=lambda c: original_snapshot(c, database=self.database),
                ),
                patch.object(
                    helper,
                    "schema_backup",
                    side_effect=RuntimeError("本机结构备份进程超时，已中止后续迁移"),
                ),
                self.assertRaisesRegex(RuntimeError, "备份.*超时"),
            ):
                entry.main("downgrade")
        with self.engine.connect() as c, c.begin():
            self.assertTrue(
                c.exec_driver_sql(
                    "SELECT pg_try_advisory_xact_lock(hashtext('project-source-sync'))"
                ).scalar_one()
            )
            self.assertTrue(
                c.exec_driver_sql(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended('pdd-v2-schema',0))"
                ).scalar_one()
            )
            self.assertEqual(
                c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar_one(),
                "20261031_09",
            )
