"""当前head空库初始化：实际PG、固定集群、随机新库，不写项目库。"""

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from psycopg.errors import DivisionByZero
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url, migrate  # noqa: E402


def runner():
    spec = importlib.util.spec_from_file_location("v2_initialization", ROOT / "scripts/db-v2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.environ.get("PDD_V2_INTEGRATION") == "1", "需显式启用隔离PG验证")
class CurrentInitializationTests(unittest.TestCase):
    def setUp(self):
        self.database = "pdd_operations_v2_test_" + uuid.uuid4().hex[:12]
        self.admin = sa.create_engine(
            local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
        )
        self.engine = None
        with self.admin.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{self.database}" OWNER pdd_admin')
        self.addCleanup(self.cleanup_database)
        self.engine = sa.create_engine(local_url(self.database), hide_parameters=True)
        self.entry = runner()
        directory = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / "var"))
        self.enterContext(patch.object(self.entry, "PRIVATE", Path(directory)))
        self.enterContext(patch.object(self.entry, "EVIDENCE", Path(directory) / "verified.json"))

    def cleanup_database(self):
        if self.engine is not None:
            self.engine.dispose()
        with self.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{self.database}"')
        self.admin.dispose()

    def evidence(self):
        with self.engine.connect() as c:
            before = self.entry.snapshot(c, database=self.database)
        self.entry.private_write(
            self.entry.EVIDENCE,
            json.dumps(
                {
                    "result": "passed",
                    "fingerprint": self.entry.fingerprint(),
                    "project_before": before,
                    "test_log": "synthetic:initialization",
                }
            ),
        )
        return before

    def assert_empty_database(self):
        with self.engine.connect() as c:
            self.assertEqual(sa.inspect(c).get_table_names(schema="public"), [])
            self.assertEqual(
                c.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_proc p "
                        "JOIN pg_namespace n ON n.oid=p.pronamespace "
                        "WHERE n.nspname='public'"
                    )
                ).scalar_one(),
                0,
            )

    def test_current_head_empty_inspection_accepts_weather_and_target_extensions(self):
        with self.engine.begin() as c:
            migrate(c)
            result = self.entry.inspect_empty(c)
            self.assertEqual(result["revision"], "20261031_09")
            self.assertEqual(result["business_tables"], 31)
            self.assertEqual(result["columns"], 506)
            self.assertEqual(result["foreign_keys"], 51)
            self.assertEqual(len(result["row_counts"]), 31)
            self.assertTrue(all(n == 0 for n in result["row_counts"].values()))

    def test_create_initializes_current_head_read_permissions_and_rejects_repeat(self):
        self.evidence()
        self.entry.create(database=self.database)
        with self.engine.connect() as c:
            before = self.entry.snapshot(c, database=self.database)
            result = self.entry.inspect_empty(c)
            self.entry.inspect_permissions(c)
            self.assertEqual(result["business_tables"], 31)
            c.execute(sa.text("SET LOCAL ROLE pdd_app"))
            self.assertEqual(c.execute(sa.text("SELECT count(*) FROM shops")).scalar_one(), 0)
            with self.assertRaises(DBAPIError), c.begin_nested():
                c.execute(
                    sa.text("INSERT INTO shops(id,shop_name,status) VALUES ('x','合成','active')")
                )
            c.execute(sa.text("RESET ROLE"))
            c.execute(sa.text("SET LOCAL ROLE pdd_ingest"))
            c.execute(
                sa.text("INSERT INTO shops(id,shop_name,status) VALUES ('x','合成','active')")
            )
            with self.assertRaises(DBAPIError), c.begin_nested():
                c.execute(sa.text("DELETE FROM shops"))
        with self.assertRaisesRegex(RuntimeError, "空结构"):
            self.entry.create(database=self.database)
        with self.engine.connect() as c:
            self.assertEqual(before, self.entry.snapshot(c, database=self.database))
            self.entry.inspect_empty(c)
            self.entry.inspect_permissions(c)
        records = list(self.entry.PRIVATE.glob("*-created.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["server"]["database"], self.database)
        for path in self.entry.PRIVATE.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        backups = list(self.entry.PRIVATE.glob("*-before-schema.sql"))
        self.assertEqual(len(backups), 1)
        self.assertGreater(backups[0].stat().st_size, 100)

    def test_mid_head_failure_rolls_back_schema_revision_permissions_and_can_retry(self):
        before = self.evidence()
        with self.engine.connect() as c:
            original_acl = c.execute(
                sa.text("SELECT datacl::text FROM pg_database WHERE datname=current_database()")
            ).scalar_one()

        def inject_failure(c, cursor, statement, parameters, context, executemany):
            if c.engine.url.database == self.database and statement.startswith(
                "ALTER TABLE public.refund_targets ADD COLUMN metric_code"
            ):
                cursor.execute("SELECT 1 / 0")

        sa.event.listen(sa.Engine, "before_cursor_execute", inject_failure)
        try:
            with self.assertRaises(DivisionByZero):
                self.entry.create(database=self.database)
        finally:
            sa.event.remove(sa.Engine, "before_cursor_execute", inject_failure)
        self.assert_empty_database()
        with self.engine.connect() as c:
            self.assertEqual(before, self.entry.snapshot(c, database=self.database))
            self.assertEqual(
                c.execute(
                    sa.text("SELECT datacl::text FROM pg_database WHERE datname=current_database()")
                ).scalar_one(),
                original_acl,
            )
        self.assertEqual(len(list(self.entry.PRIVATE.glob("*-before-schema.sql"))), 1)
        self.assertEqual(len(list(self.entry.PRIVATE.glob("*-created.json"))), 0)
        self.entry.create(database=self.database)
        with self.engine.connect() as c:
            self.entry.inspect_empty(c)
            self.entry.inspect_permissions(c)

    def test_existing_unrelated_objects_refused_without_backup_or_mutation(self):
        self.evidence()
        with self.engine.begin() as c:
            c.execute(sa.text("CREATE TABLE public.synthetic_blocker(id text)"))
            c.execute(sa.text("INSERT INTO public.synthetic_blocker VALUES ('retained')"))
        with self.assertRaisesRegex(RuntimeError, "空结构"):
            self.entry.create(database=self.database)
        with self.engine.connect() as c:
            self.assertEqual(sa.inspect(c).get_table_names(schema="public"), ["synthetic_blocker"])
            self.assertEqual(
                c.execute(sa.text("SELECT id FROM synthetic_blocker")).scalar_one(), "retained"
            )
        self.assertEqual(list(self.entry.PRIVATE.glob("*-before*")), [])

    def test_file_change_before_commit_rolls_back_database_and_schema_acl(self):
        before = self.evidence()
        digest = self.entry.fingerprint()
        with self.engine.connect() as c:
            acl_before = c.execute(
                sa.text(
                    "SELECT datacl::text,(SELECT nspacl::text FROM pg_namespace "
                    "WHERE nspname='public') "
                    "FROM pg_database WHERE datname=current_database()"
                )
            ).one()
        with patch.object(self.entry, "fingerprint", side_effect=[digest, digest, "changed"]):
            with self.assertRaisesRegex(RuntimeError, "事务回滚"):
                self.entry.create(database=self.database)
        self.assert_empty_database()
        with self.engine.connect() as c:
            self.assertEqual(before, self.entry.snapshot(c, database=self.database))
            self.assertEqual(
                c.execute(
                    sa.text(
                        "SELECT datacl::text,(SELECT nspacl::text FROM pg_namespace "
                        "WHERE nspname='public') "
                        "FROM pg_database WHERE datname=current_database()"
                    )
                ).one(),
                acl_before,
            )
        self.assertEqual(len(list(self.entry.PRIVATE.glob("*-created.json"))), 0)

    def test_inspection_rejects_extra_partial_column_write_permissions(self):
        self.evidence()
        self.entry.create(database=self.database)
        with self.engine.begin() as c:
            c.exec_driver_sql("GRANT INSERT(id) ON public.shops TO pdd_app")
            with self.assertRaisesRegex(RuntimeError, "列"):
                self.entry.inspect_permissions(c)

    def test_nonpublic_objects_refused_without_touching_them(self):
        self.evidence()
        with self.engine.begin() as c:
            c.exec_driver_sql("CREATE SCHEMA synthetic_private")
            c.exec_driver_sql("CREATE TABLE synthetic_private.retained(id text)")
        with self.assertRaisesRegex(RuntimeError, "空结构"):
            self.entry.create(database=self.database)
        with self.engine.connect() as c:
            self.assertEqual(
                sa.inspect(c).get_table_names(schema="synthetic_private"), ["retained"]
            )
        self.assertEqual(list(self.entry.PRIVATE.glob("*-before*")), [])


if __name__ == "__main__":
    unittest.main()
