"""以随机真实PG库验收受控增量升级，保留历史报告、事实及外部来源。"""

from datetime import date

import psycopg
import pytest
from psycopg import sql

from tests.test_aftersales_api import publish
from tests.test_deployment_database import allocated_database, extension_snapshot, load_script

__all__ = ["allocated_database"]


def install_rpa(c):
    c.exec_driver_sql("""
        CREATE TABLE public.aftersales_reports_rpa (
            id bigint GENERATED ALWAYS AS IDENTITY
                (SEQUENCE NAME public.aftersale_reports_rpa_id_seq),
            start_date date NOT NULL,
            time_granularity text NOT NULL,
            json_data jsonb NOT NULL,
            updated_at timestamptz NOT NULL,
            class text NOT NULL,
            end_date date NOT NULL,
            CONSTRAINT aftersale_reports_rpa_pkey PRIMARY KEY (id)
        );
        COMMENT ON TABLE public.aftersales_reports_rpa IS 'RPA 抓取每周月售后报表';
        INSERT INTO public.aftersales_reports_rpa
            VALUES (DEFAULT, '2026-10-01', 'month', '{"payload":1}',
                    '2026-11-01 00:00:00+00', 'test', '2026-10-31');
        GRANT SELECT ON public.aftersales_reports_rpa TO pdd_ingest;
    """)


@pytest.fixture
def deployed12(allocated_database):
    renderer = load_script("upgrade_test_renderer", "render-db-schema.py")
    with allocated_database.connect() as c:
        c.exec_driver_sql("SET ROLE pdd_app")
        body = renderer.render_sql("20261104_12")
        for line in body.splitlines():
            if line.startswith("GRANT "):
                body = body.replace(line + "\n", "")
        c.connection.driver_connection.execute(body, prepare=False)
        c.exec_driver_sql("RESET ROLE")
        install_rpa(c)
        c.exec_driver_sql("CREATE EXTENSION pg_trgm WITH SCHEMA public")
        c.exec_driver_sql("CREATE TABLE public.extension_data (value text)")
        c.exec_driver_sql("INSERT INTO public.extension_data VALUES ('preserved')")
        c.exec_driver_sql("ALTER EXTENSION pg_trgm ADD TABLE public.extension_data")
    return allocated_database


def data_snapshot(c):
    return {
        table: c.exec_driver_sql(f"SELECT to_jsonb(r) FROM public.{table} r ORDER BY to_jsonb(r)")
        .scalars()
        .all()
        for table in (
            "dataset_versions",
            "current_datasets",
            "version_inputs",
            "aftersales_reports",
            "product_sales_records",
            "return_records",
        )
    }


def test_upgrade_preserves_data_and_owned_company_objects(deployed12):
    publish(deployed12, "older", date(2026, 10, 10))
    publish(deployed12, "newer", previous_report_id="reportolder")
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    initializer = load_script("company_initializer", "init-deployment-db.py")
    with deployed12.connect() as c, deployed12.connect() as writer:
        extension_before = extension_snapshot(c)
        rpa_before = c.exec_driver_sql("SELECT * FROM public.aftersales_reports_rpa").all()
        sequence_before = c.exec_driver_sql(
            "SELECT last_value, is_called FROM public.aftersale_reports_rpa_id_seq"
        ).one()
        data_before = data_snapshot(c)
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        assert upgrade.upgrade(raw) == "ready"
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == (
            "20261104_12"
        )
        assert upgrade.upgrade(raw, apply=True) == "upgraded"
        assert (
            c.exec_driver_sql(
                "SELECT to_regclass('public.ix_return_records_summary_window')"
            ).scalar()
            is not None
        )
        writer.exec_driver_sql("SELECT pg_advisory_lock(hashtext('project-source-sync'))")
        try:
            assert upgrade.upgrade(raw, apply=True) == "already_upgraded"
        finally:
            writer.exec_driver_sql("SELECT pg_advisory_unlock(hashtext('project-source-sync'))")
        assert (
            initializer.initialize(raw, database=deployed12.url.database, account="pdd_app")
            == "already_initialized"
        )
        assert c.exec_driver_sql("SELECT count(*) FROM public.dataset_storage").scalar() == 8
        assert c.exec_driver_sql("SELECT count(*) FROM public.read_return_records").scalar() == 4
        assert c.exec_driver_sql("SELECT count(*) FROM public.source_reconciliations").scalar() == 0
        assert data_snapshot(c) == data_before
        c.exec_driver_sql("RESET ROLE")
        assert extension_snapshot(c) == extension_before
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar() == "preserved"
        assert c.exec_driver_sql("SELECT * FROM public.aftersales_reports_rpa").all() == rpa_before
        assert (
            c.exec_driver_sql(
                "SELECT last_value, is_called FROM public.aftersale_reports_rpa_id_seq"
            ).one()
            == sequence_before
        )


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        ("UPDATE alembic_version_v2 SET version_num='20261110_13'", "REVISION_MISMATCH"),
        ("UPDATE alembic_version_v2 SET version_num='20261111_15'", "REVISION_MISMATCH"),
        ("CREATE TABLE unknown_data (id text)", "SCHEMA_MISMATCH"),
        ("CREATE SEQUENCE public.unknown_seq", "SCHEMA_MISMATCH"),
        ("CREATE COLLATION public.unknown_collation (provider=libc,locale='C')", "SCHEMA_MISMATCH"),
        ("CREATE POLICY unknown_policy ON public.shops USING (true)", "SCHEMA_MISMATCH"),
        (
            "CREATE FUNCTION public.unknown() RETURNS int LANGUAGE sql AS 'SELECT 1'",
            "SCHEMA_MISMATCH",
        ),
        ("ALTER TABLE aftersales_reports_rpa ADD COLUMN unknown text", "RPA_MISMATCH"),
        (
            "ALTER TABLE source_checkpoints RENAME COLUMN sync_status TO unrelated",
            "SCHEMA_MISMATCH",
        ),
    ],
)
def test_upgrade_refuses_version_or_object_drift(deployed12, statement, error):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        c.exec_driver_sql(statement)
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        for apply in (False, True):
            with pytest.raises(ValueError, match=error):
                upgrade.upgrade(raw, apply=apply)
        assert c.exec_driver_sql("SELECT to_regclass('public.dataset_storage')").scalar() is None


def test_upgrade_refuses_unreviewed_database_ddl_callback(deployed12):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        c.exec_driver_sql("""
            CREATE FUNCTION monitor.unreviewed_callback() RETURNS event_trigger LANGUAGE plpgsql
            AS $$ BEGIN NULL; END $$;
            CREATE EVENT TRIGGER unreviewed_callback ON ddl_command_end
              WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION monitor.unreviewed_callback()
        """)
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        for apply in (False, True):
            with pytest.raises(ValueError, match="DEPLOY_EVENT_TRIGGER_MISMATCH"):
                upgrade.upgrade(raw, apply=apply)
        assert c.exec_driver_sql("SELECT to_regclass('public.dataset_storage')").scalar() is None


def test_upgrade_refuses_disabled_internal_foreign_key_trigger(deployed12):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        raw = c.connection.driver_connection
        trigger, validated = raw.execute("""
            SELECT t.tgname, k.convalidated FROM pg_trigger t
            JOIN pg_constraint k ON k.oid=t.tgconstraint
            WHERE t.tgrelid='public.current_datasets'::regclass
              AND t.tgisinternal AND k.contype='f'
            ORDER BY t.tgname LIMIT 1
        """).fetchone()
        assert validated
        raw.execute(
            sql.SQL("ALTER TABLE public.current_datasets DISABLE TRIGGER {}").format(
                sql.Identifier(trigger)
            )
        )
        assert raw.execute(
            "SELECT convalidated FROM pg_constraint WHERE oid=(SELECT tgconstraint FROM pg_trigger "
            "WHERE tgname=%s AND tgrelid='public.current_datasets'::regclass)",
            (trigger,),
        ).fetchone() == (True,)
        c.exec_driver_sql("SET ROLE pdd_app")
        for apply in (False, True):
            with pytest.raises(ValueError, match="DEPLOY_SCHEMA_MISMATCH:internal_triggers"):
                upgrade.upgrade(raw, apply=apply)
        assert c.exec_driver_sql("SELECT to_regclass('public.dataset_storage')").scalar() is None


def test_upgrade_refuses_wrong_actual_role(deployed12):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c, pytest.raises(ValueError, match="TARGET_MISMATCH"):
        upgrade.upgrade(c.connection.driver_connection, apply=True)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://songbing:test@other.example/pdd_dashboard?sslmode=require",
        "postgresql+psycopg://admin:test@pg.kktree.cn/pdd_dashboard?sslmode=require",
        "postgresql+psycopg://songbing:test@pg.kktree.cn/pdd_operations?sslmode=require",
        "postgresql+psycopg://songbing:test@pg.kktree.cn:5433/pdd_dashboard?sslmode=require",
        "postgresql+psycopg://songbing:test@pg.kktree.cn:0/pdd_dashboard?sslmode=require",
        "postgresql+psycopg://songbing:test@pg.kktree.cn/pdd_dashboard?sslmode=disable",
        "postgresql+psycopg://songbing:test@pg.kktree.cn/pdd_dashboard?sslmode=require&hostaddr=1.2.3.4",
        "postgresql+psycopg://pdd_admin:test@127.0.0.1:55432/pdd_operations_v2_test_abcd1234abcd",
    ],
)
def test_upgrade_cli_refuses_nonproduction_connections(url):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with pytest.raises(ValueError, match="CONNECTION_INVALID"):
        upgrade.connection_parameters(url)


@pytest.mark.parametrize(
    "lock", ["project-source-sync", "m5-source-report", "pdd-database-maintenance"]
)
def test_upgrade_refuses_busy_project_writer(deployed12, lock):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c, deployed12.connect() as other:
        other.exec_driver_sql("SELECT pg_advisory_lock(hashtext(%s))", (lock,))
        try:
            c.exec_driver_sql("SET ROLE pdd_app")
            with pytest.raises(ValueError, match="UPGRADE_BUSY"):
                upgrade.upgrade(c.connection.driver_connection, apply=True)
        finally:
            other.exec_driver_sql("SELECT pg_advisory_unlock(hashtext(%s))", (lock,))


def test_upgrade_refuses_running_task_even_if_advisory_lock_is_free(deployed12):
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        c.exec_driver_sql("""
            INSERT INTO public.task_runs
                (id,job_code,request_ref,lock_key,status,queued_at,started_at,
                 worker_ref,lease_expires_at)
            VALUES ('running','sync_shop_daily','test','test','running',transaction_timestamp(),
                    transaction_timestamp(),'test',transaction_timestamp()+interval '1 minute')
        """)
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        for apply in (False, True):
            with pytest.raises(ValueError, match="UPGRADE_RUNNING_TASKS"):
                upgrade.upgrade(raw, apply=apply)
        assert c.exec_driver_sql("SELECT to_regclass('public.dataset_storage')").scalar() is None


def test_upgrade_rolls_back_versions_tables_and_data_after_late_ddl_failure(
    deployed12, monkeypatch
):
    publish(deployed12, "older", date(2026, 10, 10))
    upgrade = load_script("production_upgrade", "upgrade-deployment-db.py")
    reviewed_sql = upgrade.renderer.render_upgrade_sql()
    # 在真实PG完成增量DDL和版本更新后注入SQL错误，不在实际库登记DDL回调。
    fault_sql = (
        reviewed_sql
        + """
        DO $$ BEGIN
          IF to_regclass('public.source_reconciliations') IS NULL OR
             NOT EXISTS (SELECT 1 FROM public.alembic_version_v2
                         WHERE version_num='20261118_20') THEN
            RAISE EXCEPTION 'injection point did not reach final migration';
          END IF;
          RAISE EXCEPTION 'injected final migration failure';
        END $$;
    """
    )
    with deployed12.connect() as c:
        before = data_snapshot(c)
        extensions_before = extension_snapshot(c)
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        with monkeypatch.context() as patch:
            patch.setattr(upgrade.renderer, "render_upgrade_sql", lambda start: fault_sql)
            with pytest.raises(psycopg.Error, match="injected final migration failure"):
                upgrade.upgrade(raw, apply=True)
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == (
            "20261104_12"
        )
        for rel in ("dataset_storage", "fact_content_blocks", "source_reconciliations"):
            assert c.exec_driver_sql("SELECT to_regclass(%s)", (f"public.{rel}",)).scalar() is None
        from migrations_v2.company_db_objects import verify_application

        verify_application(c.connection.driver_connection, "20261104_12", has_rpa=True)
        assert data_snapshot(c) == before
        c.exec_driver_sql("RESET ROLE")
        assert extension_snapshot(c) == extensions_before
        assert c.exec_driver_sql("SELECT count(*) FROM public.aftersales_reports_rpa").scalar() == 1
        c.exec_driver_sql("SET ROLE pdd_app")
        assert upgrade.upgrade(raw, apply=True) == "upgraded"


def test_upgrade_renderer_refuses_sql_for_an_unreviewed_role(monkeypatch):
    renderer = load_script("upgrade_renderer", "render-db-schema.py")
    upgrade = renderer.command.upgrade

    def inject_grant(config, *args, **kwargs):
        upgrade(config, *args, **kwargs)
        output = config.output_buffer
        body = output.getvalue().replace(
            "COMMIT;", "GRANT SELECT ON public.dataset_storage TO dbrole_admin;\nCOMMIT;"
        )
        output.seek(0)
        output.truncate()
        output.write(body)

    monkeypatch.setattr(renderer.command, "upgrade", inject_grant)
    with pytest.raises(RuntimeError, match="DEPLOY_MIGRATION_SQL_CHANGED"):
        renderer.render_upgrade_sql()


def test_index_timeout_rolls_back_direct_12_to_16(deployed12, monkeypatch):
    """普通建索引等待真实冲突锁时被PG取消，13—16必须全部回滚。"""
    publish(deployed12)
    upgrade = load_script("index_timeout_upgrade", "upgrade-deployment-db.py")
    prepare = upgrade.prepare_transaction

    def short_test_budget(connection, *, read_only):
        prepare(connection, read_only=read_only)
        if not read_only:
            connection.execute("SET LOCAL statement_timeout='50ms'")

    monkeypatch.setattr(upgrade, "prepare_transaction", short_test_budget)
    with deployed12.connect() as c, deployed12.connect() as blocker:
        before = data_snapshot(c)
        c.exec_driver_sql("SET ROLE pdd_app")
        with blocker.connection.driver_connection.transaction():
            blocker.exec_driver_sql("LOCK TABLE return_records IN ROW EXCLUSIVE MODE")
            with pytest.raises(psycopg.errors.QueryCanceled):
                upgrade.upgrade(c.connection.driver_connection, apply=True)
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == "20261104_12"
        )
        for name in (
            "dataset_storage",
            "fact_content_blocks",
            "source_reconciliations",
            "ix_return_records_summary_window",
        ):
            assert c.exec_driver_sql("SELECT to_regclass(%s)", (f"public.{name}",)).scalar() is None
        from migrations_v2.company_db_objects import verify_application

        verify_application(c.connection.driver_connection, "20261104_12", has_rpa=True)
        assert data_snapshot(c) == before
        c.exec_driver_sql("RESET ROLE")
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar() == "preserved"
        assert c.exec_driver_sql("SELECT count(*) FROM aftersales_reports_rpa").scalar() == 1


@pytest.mark.parametrize("start", ["20261110_13", "20261110_14", "20261118_20", "head", "unknown"])
def test_increment_renderer_refuses_unreviewed_start(start):
    renderer = load_script("bounded_index_renderer", "render-db-schema.py")
    with pytest.raises(RuntimeError, match="DEPLOY_MIGRATION_START_INVALID"):
        renderer.render_upgrade_sql(start)


@pytest.mark.parametrize("start", ["20261114_18", "20261116_19"])
def test_region_upgrade_preserves_readers_and_company_objects(deployed12, start):
    from tests.test_return_summary_index import revision_sql

    publish(deployed12)
    upgrade = load_script("region_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c, deployed12.connect() as reader:
        c.exec_driver_sql("SET ROLE pdd_app")
        raw = c.connection.driver_connection
        raw.execute(revision_sql("20261104_12", start), prepare=False)
        data = data_snapshot(c)
        reader.exec_driver_sql("SET ROLE pdd_app")
        before_rpa = upgrade.rpa_manifest(raw, owner="pdd_admin")
        before_acl = upgrade.application_acl(raw, has_rpa=True)
        before_company = upgrade.protected_catalog(raw)
        assert upgrade.upgrade(raw) == "ready"
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar() == start
        with reader.connection.driver_connection.transaction():
            reader.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            assert data_snapshot(reader) == data
            assert upgrade.upgrade(raw, apply=True) == "upgraded"
            assert data_snapshot(reader) == data
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
            == "20261118_20"
        )
        assert c.exec_driver_sql("SELECT count(*) FROM region_retirements").scalar() == 0
        assert data_snapshot(c) == data
        assert upgrade.rpa_manifest(raw, owner="pdd_admin") == before_rpa
        assert upgrade.protected_catalog(raw) == before_company
        after_acl = upgrade.application_acl(raw, has_rpa=True)
        assert all(after_acl[key] == value for key, value in before_acl.items())
        assert upgrade.upgrade(raw, apply=True) == "already_upgraded"


def test_company_forecast_upgrade_preserves_existing_objects(deployed12):
    upgrade = load_script("forecast_company_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("SET ROLE pdd_app")
        raw = connection.connection.driver_connection
        assert upgrade.upgrade(raw, apply=True) == "upgraded"
        before = data_snapshot(connection)
        assert upgrade.upgrade(raw, forecast=True) == "ready"
        assert upgrade.upgrade(raw, forecast=True, apply=True) == "upgraded"
        assert data_snapshot(connection) == before
        assert upgrade.upgrade(raw, forecast=True, apply=True) == "already_upgraded"
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM public.read_forecast_sop").scalar_one()
            == 0
        )
