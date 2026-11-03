"""08目标区间真实PG反例；随机隔离库，不读写项目经营数据。"""

import importlib.util
import io
import os
import sys
import unittest
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url, migrate  # noqa: E402
from migrations_v2.tests.test_offline_baseline import dictionary_columns  # noqa: E402


def load_upgrade_entry():
    spec = importlib.util.spec_from_file_location(
        "target_upgrade_test", ROOT / "scripts/upgrade-targets-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def alembic_config(connection=None):
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
        config.attributes["v2_expected_database"] = connection.engine.url.database
    return config


class TargetRangeOfflineTests(unittest.TestCase):
    def test_current_dictionary_exact_08_extensions_and_nullable_original_rate(self):
        fields = dictionary_columns()["refund_targets"]
        self.assertEqual(
            [
                (name, fields[name][2:5])
                for name in (
                    "target_rate",
                    "metric_code",
                    "lower_rate",
                    "upper_rate",
                    "application_policy_code",
                )
            ],
            [
                ("target_rate", ["NUMERIC", "是", "CHECK"]),
                ("metric_code", ["TEXT", "否", "CHECK"]),
                ("lower_rate", ["NUMERIC", "是", "CHECK"]),
                ("upper_rate", ["NUMERIC", "是", "CHECK"]),
                ("application_policy_code", ["TEXT", "是", "CHECK"]),
            ],
        )

    def test_derived_review_sql_is_exact_08_migration_output(self):
        config = alembic_config()
        output = io.StringIO()
        config.output_buffer = output
        command.upgrade(config, "20261028_07:20261028_08", sql=True)
        expected = (
            "-- 仅审阅：Alembic 07→08目标区间与最小权限派生SQL；执行使用受控升级入口。\n"
            "-- API只读下限为SELECT；writer三表SELECT/INSERT及sales_targets.id UPDATE；"
            "回退保留API只读和03已有销售目标权限。\n" + output.getvalue().rstrip() + "\n"
        )
        self.assertEqual((ROOT / "docs/database-schema/target-ranges.sql").read_text(), expected)


@unittest.skipUnless(os.environ.get("PDD_V2_INTEGRATION") == "1", "需显式启用隔离PG验证")
class TargetRangePostgreSQLTests(unittest.TestCase):
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
            command.upgrade(alembic_config(c), "20261028_08")

    @classmethod
    def cleanup_database(cls):
        cls.engine.dispose()
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{cls.database}"')
        cls.admin.dispose()

    def setUp(self):
        self.c = self.engine.connect()
        tx = self.c.begin()
        self.addCleanup(self.c.close)
        self.addCleanup(tx.rollback)
        self.c.execute(
            sa.text(
                "INSERT INTO dataset_versions(id,dataset_code,origin_kind,state,"
                "row_schema_revision,interpretation_ref,producer_revision) VALUES "
                "('target-v','refund_targets','derived','draft','test','synthetic:test','synthetic:test')"
            )
        )
        self.c.execute(
            sa.text(
                "INSERT INTO shops(id,shop_name,status) "
                "VALUES ('target-shop','合成目标店','active')"
            )
        )

    def target(self, id="target", **changes):
        values = dict(
            id=id,
            dataset_version_id="target-v",
            source_row_key=id,
            source_scope_label="合成整体",
            source_scope_text="合成验证目标",
            scope_kind="all",
            category_match_mode="all",
            style_match_mode="all",
            scope_status="complete",
            period_kind="custom",
            period_status="reference_only",
            target_rate=None,
            metric_code="total_refund_rate",
            lower_rate="0.40",
            upper_rate="0.43",
            application_policy_code="rolling_30d_and_natural_month",
        )
        values.update(changes)
        table = sa.Table("refund_targets", sa.MetaData(), autoload_with=self.c)
        return table.insert().values(**values)

    def rejected(self, statement, sqlstate="23514"):
        with self.assertRaises(DBAPIError) as error, self.c.begin_nested():
            self.c.execute(statement)
        self.assertEqual(error.exception.orig.sqlstate, sqlstate)

    def test_exact_four_columns_nullable_scalar_and_minimum_permissions(self):
        columns = sa.inspect(self.c).get_columns("refund_targets", schema="public")
        self.assertEqual(
            [x["name"] for x in columns[-4:]],
            ["metric_code", "lower_rate", "upper_rate", "application_policy_code"],
        )
        self.assertEqual(
            [(x["name"], str(x["type"]), x["nullable"], x["default"]) for x in columns[-4:]],
            [
                ("metric_code", "TEXT", False, "'total_refund_rate'::text"),
                ("lower_rate", "NUMERIC", True, None),
                ("upper_rate", "NUMERIC", True, None),
                ("application_policy_code", "TEXT", True, None),
            ],
        )
        self.assertTrue(next(x for x in columns if x["name"] == "target_rate")["nullable"])
        load_upgrade_entry().verify(self.c)

    def test_range_and_scalar_are_exclusive_complete_finite_and_ordered(self):
        self.c.execute(self.target())
        self.c.execute(
            self.target(
                "aftersale",
                metric_code="aftersale_refund_rate",
                lower_rate="0.32",
                upper_rate="0.36",
            )
        )
        self.c.execute(
            self.target(
                "scalar",
                target_rate="0.25",
                lower_rate=None,
                upper_rate=None,
                application_policy_code=None,
            )
        )
        self.c.execute(self.target("zero-range", lower_rate="0", upper_rate="0"))
        self.c.execute(self.target("one-range", lower_rate="1", upper_rate="1"))
        defects = [
            dict(lower_rate=None),
            dict(upper_rate=None),
            dict(target_rate="0.42"),
            dict(lower_rate=None, upper_rate=None),
            dict(lower_rate="0.44"),
            dict(lower_rate="-0.01"),
            dict(upper_rate="1.01"),
            dict(application_policy_code=None),
            dict(application_policy_code="unknown"),
            dict(metric_code="unknown"),
        ]
        for field in ("lower_rate", "upper_rate"):
            defects.extend({field: value} for value in ("NaN", "Infinity", "-Infinity"))
        defects.extend(
            dict(target_rate=value, lower_rate=None, upper_rate=None)
            for value in ("NaN", "Infinity", "-Infinity", "-0.01", "1.01")
        )
        for index, changes in enumerate(defects):
            with self.subTest(changes=changes):
                self.rejected(self.target("bad-" + str(index), **changes))
        self.rejected(self.target("bad-null-metric", metric_code=None), "23502")

    def test_original_seventeen_scalars_survive_07_upgrade_and_safe_downgrade(self):
        command.downgrade(alembic_config(self.c), "20261028_07")
        for n in range(17):
            self.c.execute(
                sa.text(
                    "INSERT INTO refund_targets(id,dataset_version_id,source_row_key,"
                    "source_scope_label,source_scope_text,scope_kind,category_match_mode,"
                    "style_match_mode,scope_status,period_kind,period_status,target_rate) VALUES "
                    "(:id,'target-v',:id,'旧细类','合成旧17组','all','all','all','complete',"
                    "'custom','reference_only',:rate)"
                ),
                dict(id=f"legacy-{n}", rate=f"0.{10 + n}"),
            )
        self.c.execute(
            sa.text(
                "UPDATE dataset_versions SET state='validated',content_fingerprint='synthetic:old',"
                "validation_evidence_ref='synthetic:old' WHERE id='target-v'"
            )
        )
        migrate(self.c)
        rows = self.c.execute(
            sa.text(
                "SELECT target_rate::text,metric_code,lower_rate,upper_rate,"
                "application_policy_code FROM refund_targets ORDER BY id"
            )
        ).all()
        self.assertEqual(len(rows), 17)
        self.assertEqual(sorted(row[0] for row in rows), [f"0.{10 + n}" for n in range(17)])
        self.assertTrue(all(row[1:] == ("total_refund_rate", None, None, None) for row in rows))
        command.downgrade(alembic_config(self.c), "20261028_07")
        self.assertFalse(sa.inspect(self.c).get_columns("refund_targets")[-2]["nullable"])
        self.assertEqual(
            self.c.execute(sa.text("SELECT count(*) FROM refund_targets")).scalar_one(), 17
        )
        migrate(self.c)

    def test_downgrade_refuses_new_semantics_without_mutating_rows_or_permissions(self):
        variants = [
            dict(),
            dict(target_rate="0.25", lower_rate=None, upper_rate=None),
            dict(
                target_rate="0.25",
                lower_rate=None,
                upper_rate=None,
                application_policy_code=None,
                metric_code="aftersale_refund_rate",
            ),
        ]
        for changes in variants:
            savepoint = self.c.begin_nested()
            try:
                self.c.execute(self.target(**changes))
                with self.assertRaises(DBAPIError) as error, self.c.begin_nested():
                    command.downgrade(alembic_config(self.c), "20261028_07")
                self.assertEqual(error.exception.orig.sqlstate, "P0001")
                self.assertEqual(
                    self.c.execute(
                        sa.text("SELECT version_num FROM alembic_version_v2")
                    ).scalar_one(),
                    "20261028_08",
                )
                self.assertEqual(
                    self.c.execute(sa.text("SELECT count(*) FROM refund_targets")).scalar_one(), 1
                )
                load_upgrade_entry().verify(self.c)
            finally:
                savepoint.rollback()

    def test_writer_can_insert_targets_members_and_seal_but_cannot_rewrite_history(self):
        self.c.execute(sa.text("SET LOCAL ROLE pdd_ingest"))
        self.c.execute(self.target())
        self.c.execute(
            sa.text(
                "INSERT INTO sales_targets(id,dataset_version_id,source_row_key,period_kind,"
                "period_status,target_amount,scope_status) VALUES "
                "('sales-target','target-v','sales-target','unknown','unknown',100,'unmapped')"
            )
        )
        self.c.execute(
            sa.text(
                "INSERT INTO sales_target_shops(target_id,shop_id) "
                "VALUES ('sales-target','target-shop')"
            )
        )
        self.c.execute(
            sa.text(
                "UPDATE dataset_versions SET state='validated',"
                "content_fingerprint='synthetic:targets',"
                "validation_evidence_ref='synthetic:targets' WHERE id='target-v'"
            )
        )
        self.rejected(self.target("late"))
        self.rejected(sa.text("UPDATE sales_targets SET id=id WHERE id='sales-target'"))
        self.rejected(
            sa.text(
                "INSERT INTO sales_target_shops(target_id,shop_id) "
                "VALUES ('sales-target','target-shop')"
            )
        )
        self.rejected(sa.text("UPDATE refund_targets SET target_rate=0.1"), "42501")
        self.rejected(sa.text("DELETE FROM refund_targets"), "42501")
        self.rejected(sa.text("ALTER TABLE refund_targets ADD COLUMN forbidden TEXT"), "42501")
        self.c.execute(sa.text("RESET ROLE"))
        self.rejected(sa.text("UPDATE refund_targets SET lower_rate=0.1"))
        self.rejected(sa.text("DELETE FROM dataset_versions WHERE id='target-v'"))

    def test_api_only_reads_and_safe_downgrade_preserves_03_sales_permissions(self):
        self.c.execute(self.target())
        self.rejected(sa.text("DELETE FROM dataset_versions WHERE id='target-v'"), "23503")
        self.c.execute(sa.text("SET LOCAL ROLE pdd_app"))
        self.assertEqual(
            self.c.execute(sa.text("SELECT count(*) FROM refund_targets")).scalar_one(), 1
        )
        self.rejected(self.target("api"), "42501")
        self.rejected(sa.text("UPDATE sales_targets SET id=id"), "42501")
        self.rejected(sa.text("DELETE FROM refund_targets"), "42501")
        self.c.execute(sa.text("RESET ROLE"))
        self.c.execute(sa.text("DELETE FROM refund_targets"))
        command.downgrade(alembic_config(self.c), "20261028_07")
        for table in ("sales_targets", "sales_target_shops"):
            for permission in ("SELECT", "INSERT"):
                self.assertTrue(
                    self.c.execute(
                        sa.text("SELECT has_table_privilege('pdd_ingest',:table,:permission)"),
                        dict(table=table, permission=permission),
                    ).scalar_one()
                )
        for table in ("sales_targets", "sales_target_shops", "refund_targets"):
            self.assertTrue(
                self.c.execute(
                    sa.text("SELECT has_table_privilege('pdd_app',:table,'SELECT')"),
                    dict(table=table),
                ).scalar_one()
            )
        self.assertFalse(
            self.c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest','refund_targets','INSERT')")
            ).scalar_one()
        )
        self.assertFalse(
            self.c.execute(
                sa.text("SELECT has_column_privilege('pdd_ingest','sales_targets','id','UPDATE')")
            ).scalar_one()
        )
        migrate(self.c)


if __name__ == "__main__":
    unittest.main()
