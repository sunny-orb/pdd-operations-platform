"""仅在显式授权入口运行：一次性真实PG17库、合成样例，绝不读取上游。

PDD_V2_INTEGRATION=1才启用。临时库由本类新建并独占，所有反例使用保存点；
不复用pdd_operations_test，不对项目库运行测试/回退，不导入业务数据。
"""

import os
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from psycopg.errors import DivisionByZero
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.baseline_20261028.schema import build_metadata  # noqa: E402
from migrations_v2.local import local_url, migrate  # noqa: E402


@unittest.skipUnless(os.environ.get("PDD_V2_INTEGRATION") == "1", "需显式启用隔离PG验证")
class PostgreSQLBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = build_metadata()
        cls.database = "pdd_operations_v2_test_" + uuid.uuid4().hex[:12]
        cls.admin = sa.create_engine(
            local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
        )
        with cls.admin.connect() as conn:
            role, version = conn.execute(
                sa.text("SELECT current_user, current_setting('server_version_num')::integer")
            ).one()
            if role != "pdd_admin" or version // 10000 != 17:
                raise RuntimeError("隔离验证服务器身份/版本与授权不符")
            conn.exec_driver_sql(f'CREATE DATABASE "{cls.database}" OWNER pdd_admin')
        cls.engine = None
        cls.addClassCleanup(cls.cleanup_database)
        cls.engine = sa.create_engine(
            local_url(cls.database),
            hide_parameters=True,
            connect_args={"options": "-c statement_timeout=10000"},
        )
        with cls.engine.begin() as conn:
            migrate(conn)

    @classmethod
    def cleanup_database(cls):
        if cls.engine is not None:
            cls.engine.dispose()
        with cls.admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{cls.database}"')
        cls.admin.dispose()
        print("隔离v2测试库已清理；未操作项目库/原独立测试库", flush=True)

    def setUp(self):
        self.conn = self.engine.connect()
        self.transaction = self.conn.begin()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.transaction.rollback)
        if not self._testMethodName.startswith("test_00"):
            self.seed()

    def table(self, name):
        return self.metadata.tables["public." + name]

    def insert(self, name, **values):
        self.conn.execute(self.table(name).insert().values(**values))

    def version(self, id, *, kind="source", code=None):
        self.insert(
            "dataset_versions",
            id=id,
            dataset_code=code or id,
            origin_kind=kind,
            state="draft",
            source_system="synthetic" if kind == "source" else None,
            source_locator="synthetic://fixture" if kind == "source" else None,
            row_schema_revision="test-v1",
            interpretation_ref="synthetic:test-v1",
            producer_revision="synthetic-test-v1",
        )

    def seal(self, id, *, publish=False):
        versions = self.table("dataset_versions")
        kind = self.conn.execute(
            sa.select(versions.c.origin_kind).where(versions.c.id == id)
        ).scalar_one()
        self.conn.execute(
            versions.update()
            .where(versions.c.id == id)
            .values(
                state="validated",
                content_fingerprint="synthetic:" + id,
                validation_evidence_ref="synthetic:validation",
                received_at=datetime.now(UTC) if kind == "source" else None,
            )
        )
        if publish:
            self.conn.execute(
                versions.update().where(versions.c.id == id).values(state="published")
            )

    def seed(self):
        """各对象至少一条合法行；这些只是结构样例，不宣称通过业务发布验收。"""
        self.insert("shops", id="shop", shop_name="合成店铺", status="active")
        self.insert(
            "listings",
            id="listing",
            shop_id="shop",
            platform_code="pdd",
            platform_product_id="000123",
            status="active",
        )
        self.version("input")
        self.seal("input", publish=True)
        self.version("output", kind="derived")
        self.insert(
            "version_inputs",
            output_version_id="output",
            input_role="source",
            input_version_id="input",
        )
        self.insert("current_datasets", dataset_code="input", version_id="input")
        for name in (
            "product_daily_facts",
            "shop_daily_facts",
            "product_promotion_records",
            "shop_channel_promotion_daily",
            "shop_rating_daily",
            "shop_refund_records",
            "shop_region_daily",
            "product_region_records",
            "product_sales_records",
            "return_records",
        ):
            values = {
                "id": name,
                "dataset_version_id": "output",
                "source_row_key": "row-1",
                "date_status": "parsed_label",
            }
            target = self.table(name)
            identity = "listing" if "listing_match_status" in target.c else "shop"
            values[identity + "_id"] = identity
            values[identity + "_match_status"] = "matched"
            if "row_kind" in target.c:
                values["row_kind"] = "detail"
            if "business_date" in target.c:
                values["business_date"] = date(2026, 10, 1)
            elif "rating_date" in target.c:
                values["rating_date"] = date(2026, 10, 1)
            else:
                values.update(period_start=date(2026, 10, 1), period_end=date(2026, 10, 1))
            if "source_granularity" in target.c:
                values["source_granularity"] = "day"
            if "channel_code" in target.c:
                values["channel_code"] = "product_promotion"
            self.insert(name, **values)
        self.insert(
            "profile_source_records",
            id="profile",
            dataset_version_id="output",
            source_row_key="profile-row",
            source_product_name_raw="  ",
        )
        self.insert(
            "profile_listing_links",
            id="link",
            dataset_version_id="output",
            profile_record_id="profile",
            listing_id="listing",
            relationship_status="matched",
            relationship_reason="synthetic:identity",
            listed_date_applicability_status="unknown",
        )
        self.insert(
            "sales_targets",
            id="sales",
            dataset_version_id="output",
            source_row_key="sales",
            period_kind="unknown",
            period_status="unknown",
            target_amount=Decimal("1.23"),
            scope_status="unmapped",
        )
        self.insert("sales_target_shops", target_id="sales", shop_id="shop")
        self.insert(
            "refund_targets",
            id="refund",
            dataset_version_id="output",
            source_row_key="refund",
            source_scope_label="synthetic",
            source_scope_text="synthetic",
            scope_kind="restricted",
            category_match_mode="include_union",
            style_match_mode="include_union",
            scope_status="partial",
            period_kind="unknown",
            period_status="unknown",
            target_rate=Decimal("0.1"),
        )
        self.insert(
            "refund_target_categories",
            target_id="refund",
            source_member_key="category",
            effect="include",
            source_category_system="synthetic",
            source_category_key="cat",
            match_basis="source_code",
            condition_status="confirmed",
        )
        self.insert(
            "refund_target_styles",
            target_id="refund",
            source_member_key="style",
            effect="include",
            source_style_namespace="synthetic",
            source_style_code="0001",
            condition_status="confirmed",
        )
        self.insert(
            "rule_sets",
            id="rules",
            set_code="synthetic",
            revision="test-v1",
            domain="aftersales",
            schema_revision="v1",
            parameters={
                "policy": {
                    "comparison_policy_code": "frozen_report_periods",
                    "grouping_policy_code": "full_source_groups_unique_denominator",
                },
                "branches": [],
                "dictionaries": [],
                "suggestions": [],
            },
            confirmation_status="pending_confirmation",
            source_locator="synthetic:rules",
            content_sha256="0" * 64,
        )
        self.insert(
            "score_standards",
            id="standard",
            standard_code="synthetic",
            dataset_version_id="output",
            source_category_system="synthetic",
            source_category_label="synthetic",
            basis_kind="proposed",
            identity_match_status="unmapped",
            source_date_status="unknown",
            source_locator="synthetic:standard",
        )
        self.insert(
            "score_standard_items",
            standard_id="standard",
            metric_code="visitors_7d",
            threshold_status="available",
            basic_threshold=0,
            good_threshold=0,
            excellent_threshold=0,
            outstanding_threshold=0,
            precision_kind="unknown",
            sample_basis_status="unknown",
            fallback_basis_status="unknown",
            source_locator="synthetic:threshold",
        )
        self.insert(
            "product_scores",
            id="score",
            dataset_version_id="output",
            listing_id="listing",
            decision_code="unscored",
            lifecycle_reason="synthetic:unknown",
        )
        self.insert(
            "product_score_items",
            score_id="score",
            metric_code="visitors_7d",
            signal_status="valid",
        )
        self.insert(
            "aftersales_reports",
            id="report",
            dataset_version_id="output",
            report_type="monthly",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 28),
            revision_no=1,
            source_generated_time_status="unknown",
            previous_comparison_status="missing",
            previous_comparison_reason="synthetic:none",
            yoy_comparison_status="missing",
            yoy_comparison_reason="synthetic:none",
        )
        self.insert(
            "task_runs",
            id="task",
            job_code="synthetic",
            request_ref="synthetic:request",
            lock_key="synthetic",
            status="queued",
        )
        self.insert(
            "coverage_records",
            id="coverage",
            dataset_version_id="output",
            scope_kind="dataset",
            availability_state="observed",
            completeness_state="unknown",
            reason_code="synthetic",
            evidence_ref="synthetic:evidence",
            checked_at=datetime.now(UTC),
        )

    def rejected(self, statement, code="23514"):
        """真实PG保存点反例；失败不污染外层样例事务。"""
        with self.assertRaises(DBAPIError) as error:
            with self.conn.begin_nested():
                self.conn.execute(statement)
        self.assertEqual(error.exception.orig.sqlstate, code)

    def test_01_catalog_matches_all_columns_constraints_comments_indexes(self):
        inspector = sa.inspect(self.conn)
        self.assertEqual(
            set(inspector.get_table_names(schema="public")),
            {t.name for t in self.metadata.tables.values()} | {"alembic_version_v2"},
        )
        for table in self.metadata.tables.values():
            with self.subTest(table=table.name):
                columns = inspector.get_columns(table.name, schema="public")
                self.assertEqual([c["name"] for c in columns], list(table.c.keys()))
                for actual, expected in zip(columns, table.c, strict=True):
                    self.assertEqual(actual["nullable"], expected.nullable)
                    self.assertEqual(actual["comment"], expected.comment)
                    self.assertEqual(
                        str(actual["type"].compile(dialect=self.conn.dialect)),
                        str(expected.type.compile(dialect=self.conn.dialect)),
                    )
                self.assertEqual(
                    inspector.get_pk_constraint(table.name, schema="public")["constrained_columns"],
                    list(table.primary_key.columns.keys()),
                )
                self.assertEqual(
                    len(inspector.get_foreign_keys(table.name, schema="public")),
                    len(table.foreign_keys),
                )
                self.assertEqual(
                    len(inspector.get_check_constraints(table.name, schema="public")),
                    sum(isinstance(c, sa.CheckConstraint) for c in table.constraints),
                )
                self.assertEqual(
                    len(inspector.get_unique_constraints(table.name, schema="public")),
                    sum(isinstance(c, sa.UniqueConstraint) for c in table.constraints),
                )
                actual_indexes = {
                    i["name"]
                    for i in inspector.get_indexes(table.name, schema="public")
                    if not i.get("duplicates_constraint")
                }
                self.assertEqual(actual_indexes, {i.name for i in table.indexes})
                self.assertGreater(
                    self.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one(), 0
                )
        self.assertEqual(
            self.conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND NOT t.tgisinternal"
                )
            ).scalar_one(),
            58,
        )

    def test_00_empty_upgrade_down_upgrade(self):
        migrate(self.conn, "base")
        self.assertEqual(
            sa.inspect(self.conn).get_table_names(schema="public"), ["alembic_version_v2"]
        )
        self.assertEqual(
            self.conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public'"
                )
            ).scalar_one(),
            0,
        )
        self.assertEqual(
            self.conn.execute(
                sa.text("SELECT count(*) FROM public.alembic_version_v2")
            ).scalar_one(),
            0,
        )
        migrate(self.conn)
        self.assertEqual(len(sa.inspect(self.conn).get_table_names(schema="public")), 31)
        self.assertEqual(
            self.conn.execute(
                sa.text("SELECT version_num FROM public.alembic_version_v2")
            ).scalar_one(),
            "20261028_05",
        )

    def test_02_all_numeric_columns_reject_nonfinite_values(self):
        count = 0
        for table in self.metadata.tables.values():
            for column in table.c:
                if isinstance(column.type, sa.Numeric):
                    count += 1
                    for bad in ("NaN", "Infinity", "-Infinity"):
                        with self.subTest(table=table.name, column=column.name, value=bad):
                            self.rejected(table.update().values({column.name: Decimal(bad)}))
        self.assertEqual(count, 65)

    def test_00b_preflight_and_mid_migration_error_leave_no_partial_schema(self):
        migrate(self.conn, "base")
        self.conn.execute(sa.text("CREATE TABLE public.synthetic_blocker (id text)"))
        with self.assertRaises(DBAPIError) as error:
            with self.conn.begin_nested():
                migrate(self.conn)
        self.assertEqual(error.exception.orig.sqlstate, "P0001")
        self.assertEqual(
            set(sa.inspect(self.conn).get_table_names(schema="public")),
            {"alembic_version_v2", "synthetic_blocker"},
        )
        self.conn.execute(sa.text("DROP TABLE public.synthetic_blocker"))

        def inject_failure(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().startswith("CREATE TABLE public.listings"):
                cursor.execute("SELECT 1 / 0")

        sa.event.listen(self.conn, "before_cursor_execute", inject_failure)
        try:
            with self.assertRaises(DivisionByZero) as error:
                with self.conn.begin_nested():
                    migrate(self.conn)
            self.assertEqual(error.exception.sqlstate, "22012")
        finally:
            sa.event.remove(self.conn, "before_cursor_execute", inject_failure)
        self.assertEqual(
            sa.inspect(self.conn).get_table_names(schema="public"), ["alembic_version_v2"]
        )
        self.assertEqual(
            self.conn.execute(
                sa.text("SELECT count(*) FROM public.alembic_version_v2")
            ).scalar_one(),
            0,
        )
        migrate(self.conn)

    def test_03_amount_precision_and_raw_blank_evidence_are_preserved(self):
        table = self.table("sales_targets")
        self.conn.execute(table.update().values(target_amount=Decimal("123.456700")))
        self.assertEqual(
            self.conn.execute(sa.select(table.c.target_amount)).scalar_one(), Decimal("123.456700")
        )
        self.assertEqual(
            self.conn.execute(
                sa.select(self.table("profile_source_records").c.source_product_name_raw)
            ).scalar_one(),
            "  ",
        )
        self.assertEqual(
            self.conn.execute(sa.select(self.table("listings").c.platform_product_id)).scalar_one(),
            "000123",
        )

    def test_04_fk_pk_and_unique_constraints_reject_bad_references(self):
        self.rejected(self.table("listings").update().values(shop_id="missing"))
        self.rejected(
            self.table("coverage_records")
            .update()
            .values(listing_id="missing", scope_kind="listing"),
            "23503",
        )
        self.rejected(self.table("shops").insert().values(id="shop", status="active"), "23505")
        self.rejected(
            self.table("listings")
            .insert()
            .values(
                id="duplicate",
                shop_id="shop",
                platform_code="pdd",
                platform_product_id="000123",
                status="active",
            ),
            "23505",
        )

    def test_05_automatic_times_noop_soft_delete_and_business_identity(self):
        shops = self.table("shops")
        before = self.conn.execute(sa.select(shops)).mappings().one()
        self.conn.execute(shops.update().values(updated_at=datetime(2000, 1, 1, tzinfo=UTC)))
        self.assertEqual(
            self.conn.execute(sa.select(shops.c.updated_at)).scalar_one(), before["updated_at"]
        )
        self.rejected(shops.update().values(created_at=datetime(2000, 1, 1, tzinfo=UTC)))
        self.conn.execute(shops.update().values(deleted_at=datetime(2000, 1, 1, tzinfo=UTC)))
        deleted = self.conn.execute(sa.select(shops)).mappings().one()
        self.assertEqual(deleted["status"], "inactive")
        self.assertGreaterEqual(deleted["deleted_at"], deleted["created_at"])
        self.rejected(shops.delete())
        self.rejected(self.table("listings").update().values(platform_product_id="999"))
        self.conn.execute(shops.update().values(deleted_at=None, status="active"))
        self.assertIsNone(self.conn.execute(sa.select(shops.c.deleted_at)).scalar_one())

    def test_06_fact_identity_date_and_source_row_shapes(self):
        table = self.table("product_daily_facts")
        self.rejected(table.update().values(listing_id=None))
        self.rejected(table.update().values(business_date=None))
        self.rejected(table.update().values(date_status="made_up"))
        self.rejected(
            table.insert().values(
                id="dup",
                dataset_version_id="output",
                source_row_key="row-1",
                listing_match_status="unmatched",
                date_status="missing",
            ),
            "23505",
        )
        self.conn.execute(
            table.update().values(
                listing_id=None,
                listing_match_status="unmatched",
                business_date=None,
                date_status="missing",
            )
        )

    def test_07_period_and_channel_rules(self):
        self.rejected(self.table("product_promotion_records").update().values(period_end=None))
        self.rejected(
            self.table("product_promotion_records").update().values(period_end=date(2026, 10, 2))
        )
        self.rejected(
            self.table("shop_channel_promotion_daily")
            .update()
            .values(channel_code="star_shop", net_attributed_revenue=1)
        )

    def test_08_nullable_coverage_scope_is_unique_and_states_need_evidence(self):
        table = self.table("coverage_records")
        row = dict(self.conn.execute(sa.select(table)).mappings().one())
        row["id"] = "duplicate"
        self.rejected(table.insert().values(**row), "23505")
        self.rejected(table.update().values(scope_kind="shop"))
        self.rejected(table.update().values(availability_state="field_null"))
        self.rejected(table.update().values(completeness_state="complete"))

    def test_09_target_members_are_unique_independent_of_effect(self):
        table = self.table("refund_target_categories")
        row = dict(self.conn.execute(sa.select(table)).mappings().one())
        row.update(source_member_key="other", effect="exclude")
        self.rejected(table.insert().values(**row), "23505")
        self.rejected(self.table("sales_targets").update().values(target_amount=-1))
        self.rejected(self.table("refund_targets").update().values(target_rate=2))

    def test_10_rule_json_and_evidence_json_fail_closed(self):
        # 规则包追加不可更新；使用新ID插入反例。
        rules = self.table("rule_sets")
        row = dict(self.conn.execute(sa.select(rules)).mappings().one())
        for value in (
            {},
            [],
            None,
            {"policy": {}, "branches": [], "dictionaries": [], "suggestions": []},
        ):
            row.update(id="bad-rule", revision="bad", parameters=value)
            self.rejected(rules.insert().values(**row))
        row["parameters"] = sa.null()
        self.rejected(rules.insert().values(**row), "23502")
        items = self.table("product_score_items")
        for value in (
            {"unexpected": "bad"},
            {"input_role": None},
            {"field_codes": ["unknown"]},
            {"missing_dates": ["garbage"], "input_role": "product_current"},
        ):
            self.rejected(items.update().values(evidence_context=value))
        self.conn.execute(items.update().values(evidence_context={"note": "synthetic:valid"}))

    def test_11_threshold_and_score_missing_shapes(self):
        self.rejected(self.table("score_standard_items").update().values(basic_threshold=None))
        self.rejected(self.table("score_standard_items").update().values(basic_threshold=1))
        self.rejected(self.table("product_score_items").update().values(signal_status="missing"))
        self.conn.execute(
            self.table("product_score_items")
            .update()
            .values(
                signal_status="missing",
                missing_reason_code="synthetic:missing",
                missing_reason_text="synthetic:no-data",
            )
        )

    def test_12_report_period_revision_and_comparison_shapes(self):
        table = self.table("aftersales_reports")
        self.rejected(table.update().values(period_end=date(2026, 9, 1)))
        self.rejected(table.update().values(revision_no=0))
        self.rejected(
            table.update().values(
                previous_report_id="report", previous_comparison_status="available"
            )
        )
        self.rejected(table.update().values(yoy_comparison_status="available"))

    def test_13_tasks_error_observation_and_running_scope(self):
        table = self.table("task_runs")
        self.rejected(table.update().values(status="succeeded"))
        self.rejected(table.update().values(source_updated_at=datetime.now(UTC)))
        self.conn.execute(
            table.update().values(
                status="running",
                started_at=datetime.now(UTC),
                worker_ref="synthetic",
                lease_expires_at=datetime.now(UTC),
            )
        )
        row = dict(self.conn.execute(sa.select(table)).mappings().one())
        row["id"] = "second-task"
        self.rejected(table.insert().values(**row), "23505")

    def test_14_sealed_content_direct_and_children_are_immutable(self):
        self.seal("output")
        self.rejected(
            self.table("product_daily_facts").update().values(source_product_name="changed")
        )
        self.rejected(self.table("sales_target_shops").delete())
        self.rejected(self.table("score_standard_items").update().values(source_locator="changed"))
        self.rejected(self.table("version_inputs").delete())
        self.rejected(
            self.table("dataset_versions")
            .update()
            .where(self.table("dataset_versions").c.id == "output")
            .values(state="draft")
        )
        self.rejected(self.table("rule_sets").delete())
        self.rejected(sa.text("TRUNCATE public.product_daily_facts"))

    def test_15_current_pointer_requires_published_and_no_revision_aba(self):
        table = self.table("current_datasets")
        self.rejected(table.update().values(version_id="output"))
        self.rejected(table.delete())
        self.conn.execute(table.update().values(revision=999))
        self.assertEqual(self.conn.execute(sa.select(table.c.revision)).scalar_one(), 0)
        self.version("new-input", code="input")
        self.seal("new-input", publish=True)
        self.conn.execute(table.update().values(version_id="new-input"))
        self.assertEqual(self.conn.execute(sa.select(table.c.revision)).scalar_one(), 1)

    def test_16_inputs_need_derived_draft_and_sealed_input(self):
        self.version("unsealed")
        self.rejected(self.table("version_inputs").update().values(input_version_id="unsealed"))
        self.rejected(self.table("version_inputs").update().values(output_version_id="input"))
        self.rejected(self.table("version_inputs").update().values(input_role="invented"))

    def test_17_rollback_refuses_any_business_data(self):
        self.rejected(sa.text("SELECT 1 / 0"), "22012")
        with self.assertRaises(DBAPIError) as error:
            with self.conn.begin_nested():
                migrate(self.conn, "base")
        self.assertEqual(error.exception.orig.sqlstate, "P0001")
        self.assertEqual(
            self.conn.execute(
                sa.text("SELECT version_num FROM public.alembic_version_v2")
            ).scalar_one(),
            "20261028_05",
        )

    def test_18_locking_seal_blocks_content_and_rejects_after_commit(self):
        # 此项另用已提交合成版本，才能在两条真实连接间验证锁。
        with self.engine.begin() as conn:
            conn.execute(
                self.table("dataset_versions")
                .insert()
                .values(
                    id="concurrency",
                    dataset_code="synthetic_concurrency",
                    origin_kind="derived",
                    state="draft",
                    row_schema_revision="test",
                    interpretation_ref="synthetic:test",
                    producer_revision="synthetic:test",
                )
            )
        try:
            with self.engine.connect() as first:
                tx = first.begin()
                first.execute(
                    sa.text(
                        "SELECT id FROM public.dataset_versions WHERE id='concurrency' FOR UPDATE"
                    )
                )

                def blocked_write():
                    with self.engine.begin() as second:
                        second.execute(sa.text("SET LOCAL lock_timeout = '200ms'"))
                        try:
                            second.execute(
                                self.table("profile_source_records")
                                .insert()
                                .values(
                                    id="blocked",
                                    dataset_version_id="concurrency",
                                    source_row_key="blocked",
                                )
                            )
                        except DBAPIError as error:
                            return error.orig.sqlstate
                    return "unexpected-success"

                with ThreadPoolExecutor(max_workers=1) as executor:
                    self.assertEqual(executor.submit(blocked_write).result(timeout=5), "55P03")
                first.execute(
                    self.table("dataset_versions")
                    .update()
                    .where(self.table("dataset_versions").c.id == "concurrency")
                    .values(
                        state="validated",
                        content_fingerprint="synthetic:concurrency",
                        validation_evidence_ref="synthetic:validated",
                    )
                )
                tx.commit()
            with self.engine.begin() as conn:
                with self.assertRaises(DBAPIError):
                    with conn.begin_nested():
                        conn.execute(
                            self.table("profile_source_records")
                            .insert()
                            .values(
                                id="blocked",
                                dataset_version_id="concurrency",
                                source_row_key="blocked",
                            )
                        )
        finally:
            # 已封版内容不能删除；此记录只在测试库，最终清理整库。
            pass

    def test_19_failed_publish_transaction_keeps_old_pointer(self):
        table = self.table("current_datasets")
        self.version("rollback", code="input")
        self.seal("rollback", publish=True)
        try:
            with self.conn.begin_nested():
                self.conn.execute(table.update().values(version_id="rollback"))
                self.conn.execute(sa.text("SELECT 1 / 0"))
        except DBAPIError:
            pass
        self.assertEqual(self.conn.execute(sa.select(table.c.version_id)).scalar_one(), "input")


if __name__ == "__main__":
    unittest.main()
