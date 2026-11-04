"""只核对离线设计文件，不连接数据库；不代表真实PostgreSQL约束验证。"""

import importlib.util
import io
import re
import runpy
import sys
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from migrations_v2.baseline_20261028.schema import build_metadata  # noqa: E402


def load_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dictionary_columns():
    result = {}
    for path in sorted((ROOT / "docs/database-schema").glob("0*.md")):
        current = None
        active = False
        for line in path.read_text().splitlines():
            heading = re.match(r"^## `([a-z_]+)`", line)
            if heading:
                current = heading.group(1)
                result[current] = {}
                active = False
            if not line.startswith("|"):
                active = False
                continue
            cells = [value.strip().replace("`", "") for value in line.strip("|").split("|")]
            if len(cells) == 6 and cells[0] == "字段" and cells[2] == "PostgreSQL类型":
                active = True
                continue
            if active and not re.fullmatch(r"[: -]+", cells[0]):
                result[current][cells[0]] = cells
    return result


class OfflineBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = build_metadata()

    def test_exact_dictionary_columns_types_nullability_keys_and_comments(self):
        expected = dictionary_columns()
        actual = {table.name: table for table in self.metadata.tables.values()}
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(len(actual), 30)
        self.assertEqual(sum(len(table.columns) for table in actual.values()), 494)
        for table_name, fields in expected.items():
            target = actual[table_name]
            if table_name == "refund_targets":
                # 当前字典已到08；只逆投影本次明确变化，不改01已执行冻结结构。
                # 新列/当前可空规则由test_target_ranges独立严格核验。
                fields = fields.copy()
                for name in ("metric_code", "lower_rate", "upper_rate", "application_policy_code"):
                    fields.pop(name)
                rate = fields["target_rate"].copy()
                self.assertEqual(rate[1], "既有单值目标")
                self.assertEqual(rate[3], "是")
                rate[1], rate[3] = "本组总退款率目标", "否"
                fields["target_rate"] = rate
            self.assertEqual(list(target.c.keys()), list(fields), table_name)
            for column_name, cells in fields.items():
                column = target.c[column_name]
                compiled_type = str(column.type.compile(dialect=postgresql.dialect()))
                if cells[2] == "TIMESTAMPTZ":
                    self.assertTrue(column.type.timezone)
                else:
                    self.assertEqual(compiled_type, cells[2], (table_name, column_name))
                self.assertEqual(column.nullable, cells[3] == "是")
                self.assertEqual(column.primary_key, "PK" in cells[4])
                self.assertEqual(column.comment, cells[1])
                expected_fks = re.findall(r"FK\s*→\s*([a-z_]+\.[a-z_]+)", cells[4])
                self.assertEqual(
                    sorted(
                        fk.target_fullname.removeprefix("public.") for fk in column.foreign_keys
                    ),
                    sorted(expected_fks),
                )

    def test_foreign_keys_and_types_are_restrict_and_same_type(self):
        keys = [fk for table in self.metadata.tables.values() for fk in table.foreign_keys]
        self.assertEqual(len(keys), 50)
        for key in keys:
            self.assertEqual(key.ondelete, "RESTRICT")
            self.assertEqual(key.onupdate, "NO ACTION")
            self.assertTrue(key.column.primary_key)
            self.assertIsInstance(key.parent.type, sa.Text)
            self.assertIsInstance(key.column.type, sa.Text)

    def test_no_silent_amount_scale_or_business_zero_default(self):
        for table in self.metadata.tables.values():
            for column in table.c:
                if isinstance(column.type, sa.Numeric):
                    self.assertIsNone(column.type.precision)
                    self.assertIsNone(column.type.scale)
                    self.assertIsNone(column.server_default)
                    constraints = " ".join(
                        str(c.sqltext)
                        for c in table.constraints
                        if isinstance(c, sa.CheckConstraint)
                    )
                    self.assertIn(column.name, constraints)
                    self.assertIn("Infinity", constraints)

    def test_only_master_identities_have_soft_delete(self):
        self.assertEqual(
            sorted(t.name for t in self.metadata.tables.values() if "deleted_at" in t.c),
            ["listings", "shops"],
        )
        listings = self.metadata.tables["public.listings"]
        self.assertTrue(
            any(
                isinstance(c, sa.UniqueConstraint)
                and tuple(c.columns.keys()) == ("platform_code", "shop_id", "platform_product_id")
                for c in listings.constraints
            )
        )

    def test_only_event_defaults_and_technical_revision_are_declared(self):
        defaults = {
            (table.name, column.name): str(column.server_default.arg)
            for table in self.metadata.tables.values()
            for column in table.c
            if column.server_default is not None
        }
        expected = {
            (table, column): "transaction_timestamp()"
            for table in ("shops", "listings")
            for column in ("created_at", "updated_at")
        }
        expected.update(
            {
                ("dataset_versions", "created_at"): "transaction_timestamp()",
                ("task_runs", "queued_at"): "transaction_timestamp()",
                ("current_datasets", "revision"): "0",
            }
        )
        self.assertEqual(defaults, expected)

    def test_offline_render_never_builds_engine_or_network_connection(self):
        script = ROOT / "scripts/render-db-schema.py"
        module = load_script(script, "render_db_schema")
        with (
            patch("sqlalchemy.create_engine", side_effect=AssertionError("禁止数据库连接")),
            patch("socket.create_connection", side_effect=AssertionError("禁止网络连接")),
        ):
            sql = module.render_sql("20261028_01")
        self.assertEqual(len(re.findall(r"CREATE TABLE public\.(?!alembic_)", sql)), 30)
        self.assertIn("ON DELETE RESTRICT ON UPDATE NO ACTION", sql)
        self.assertIn("NULLS NOT DISTINCT", sql)
        self.assertIn("BEFORE INSERT OR UPDATE", sql)
        self.assertNotIn("DROP SCHEMA", sql)
        self.assertNotIn("CASCADE", sql)
        self.assertNotIn("INSERT INTO public.shops", sql)
        self.assertNotIn("%%", sql)
        self.assertIn("BEFORE INSERT OR UPDATE OR DELETE ON public.current_datasets", sql)
        self.assertIn("当前入口不可删除重建", sql)

    def test_all_constraint_names_fit_pg_and_dependency_order_is_acyclic(self):
        names = []
        ordered = list(self.metadata.sorted_tables)
        positions = {t.name: i for i, t in enumerate(ordered)}
        for table in ordered:
            for obj in [*table.constraints, *table.indexes]:
                names.append(obj.name)
                self.assertLessEqual(len(obj.name.encode()), 63)
            for fk in table.foreign_keys:
                if fk.column.table is not table:
                    self.assertLess(positions[fk.column.table.name], positions[table.name])
        self.assertEqual(len(set(names)), len(names))

    def test_json_constants_are_not_parsed_as_text_bind_parameters(self):
        renderer = load_script(ROOT / "scripts/render-db-schema.py", "json_constants_renderer")
        sql = renderer.render_sql("20261028_01")
        self.assertIn('"baseline_days":7', sql)
        self.assertIn('"require_source_completeness":true', sql)
        self.assertIn('"count_policy_code":null', sql)
        self.assertNotIn('"baseline_days"NULL', sql)

    def test_supplementary_indexes_match_documented_column_order(self):
        text = (ROOT / "docs/database-schema/README.md").read_text()
        expected = []
        for line in text.splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not line.startswith("|") or len(cells) < 2:
                continue
            if cells[0] not in {t.name for t in self.metadata.tables.values()}:
                continue
            for sequence in re.findall(r"\(([^)]+)\)", cells[1]):
                expected.append((cells[0], sequence.replace(" ", "")))
        actual = []
        for table in self.metadata.tables.values():
            for index in table.indexes:
                if not index.unique:
                    columns = ",".join(
                        str(x.compile(dialect=postgresql.dialect()))
                        .replace("public." + table.name + ".", "")
                        .replace(" ", "")
                        for x in index.expressions
                    )
                    actual.append((table.name, columns))
        self.assertEqual(len(actual), 22)
        self.assertEqual(sorted(expected), sorted(actual))

    def test_downgrade_locks_before_empty_check_and_never_cascades(self):
        migration = load_script(
            ROOT / "apps/api/migrations_v2/versions/20261028_01_initial_30_tables.py",
            "test_initial_baseline",
        )
        self.assertIsNone(migration.down_revision)
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={
                "as_sql": True,
                "output_buffer": output,
            },
        )
        with Operations.context(context):
            migration.downgrade()
        sql = output.getvalue()
        self.assertLess(sql.index("LOCK TABLE"), sql.index("IF EXISTS"))
        self.assertLess(sql.index("含记录"), sql.index("DROP TABLE"))
        self.assertEqual(len(re.findall(r"DROP TABLE public\.", sql)), 30)
        self.assertNotIn("CASCADE", sql)

    def test_online_entry_rejects_before_configuration_or_engine(self):
        with (
            patch("alembic.context.is_offline_mode", return_value=False, create=True),
            patch("alembic.context.configure", create=True) as configure,
            patch("alembic.context.config", SimpleNamespace(attributes={}), create=True),
            patch("sqlalchemy.create_engine", side_effect=AssertionError("禁止接库")),
        ):
            with self.assertRaisesRegex(RuntimeError, "显式传入受控连接"):
                runpy.run_path(str(ROOT / "apps/api/migrations_v2/env.py"))
            configure.assert_not_called()

    def test_display_inventory_and_coverage_totals_are_consistent(self):
        contract = (ROOT / "docs/data-contract.md").read_text()
        # 现有M1文档含不可见排版字符；只用于识别表头，不修改业务原文。
        contract = "".join(c for c in contract if unicodedata.category(c) != "Cf")
        counts = []
        blocks = 0
        for number in range(1, 6):
            part = re.split(r"#{3,4} 3\.5\." + str(number) + r"\b", contract, maxsplit=1)[1]
            part = re.split(r"#{3,4} 3\.5\.[0-9]", part, maxsplit=1)[0]
            count = 0
            active = False
            for line in part.splitlines():
                if not line.startswith("|"):
                    active = False
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if cells[0] == "展示项" and "展示字段（中文）" in cells:
                    active = True
                    blocks += 1
                elif active and not re.fullmatch(r"[: -]+", cells[0]):
                    count += 1
            counts.append(count)
        self.assertEqual(counts, [91, 96, 49, 64, 63])
        self.assertEqual(blocks, 49)
        coverage = (ROOT / "docs/database-schema/README.md").read_text()
        entries = [line for line in coverage.splitlines() if re.match(r"\| M[12356]\s*\|", line)]
        self.assertEqual(len(entries), 49)
        self.assertEqual(sum(int(line.split("|")[3].strip()) for line in entries), 363)


if __name__ == "__main__":
    unittest.main()
