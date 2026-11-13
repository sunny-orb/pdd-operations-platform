"""17约束升级、原数据保留及拒绝丢失对账证据的降级。"""

import pytest

from tests.test_deployment_database import allocated_database
from tests.test_deployment_upgrade import deployed12
from tests.test_return_summary_index import install, revision_sql

__all__ = ["allocated_database", "deployed12"]


def test_upgrade16_and_downgrade_preserve_state_acl_and_monitor(allocated_database):
    from migrations_v2.company_db_objects import verify_application

    install(allocated_database, "20261111_16")
    with allocated_database.connect() as c:
        raw = c.connection.driver_connection
        raw.execute(
            "INSERT INTO source_reconciliations(source_id,cycle_at,status) "
            "VALUES ('shop_daily','2026-11-15 17:00+08','waiting')"
        )
        before = raw.execute("SELECT * FROM source_reconciliations").fetchall()
        acl = raw.execute(
            "SELECT relacl FROM pg_class WHERE oid='source_reconciliations'::regclass"
        ).fetchone()
        with raw.transaction():
            raw.execute(revision_sql("20261111_16", "20261113_17"), prepare=False)
        verify_application(raw, "20261113_17", has_rpa=False)
        assert raw.execute("SELECT * FROM source_reconciliations").fetchall() == before
        assert (
            raw.execute(
                "SELECT relacl FROM pg_class WHERE oid='source_reconciliations'::regclass"
            ).fetchone()
            == acl
        )
        assert raw.execute("SELECT value FROM monitor.sentinel").fetchone() == ("preserved",)
        raw.execute(revision_sql("20261113_17", "20261111_16", downgrade=True), prepare=False)
        verify_application(raw, "20261111_16", has_rpa=False)
        assert raw.execute("SELECT * FROM source_reconciliations").fetchall() == before
        raw.execute(revision_sql("20261111_16", "20261113_17"), prepare=False)
        raw.execute(
            "INSERT INTO source_reconciliations(source_id,cycle_at,status) "
            "VALUES ('product_regions','2026-11-15 17:00+08','waiting')"
        )
        with (
            pytest.raises(Exception, match="PRODUCT_REGIONS_DOWNGRADE_EVIDENCE_EXISTS"),
            raw.transaction(),
        ):
            raw.execute(revision_sql("20261113_17", "20261111_16", downgrade=True), prepare=False)
        verify_application(raw, "20261113_17", has_rpa=False)
        assert raw.execute("SELECT count(*) FROM source_reconciliations").fetchone()[0] == 2


def test_controlled_upgrade_from16_preserves_company_objects(deployed12):
    import re

    from tests.test_deployment_database import load_script
    from tests.test_deployment_upgrade import data_snapshot

    upgrade = load_script("regions_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        rpa = c.exec_driver_sql("SELECT * FROM aftersales_reports_rpa").all()
        c.exec_driver_sql("SET ROLE pdd_app")
        body = re.sub(
            r"(?m)^GRANT [^;\n]+ TO pdd_(?:app|ingest);\n",
            "",
            revision_sql("20261104_12", "20261111_16"),
        )
        c.connection.driver_connection.execute(body, prepare=False)
        before = data_snapshot(c)
        assert upgrade.upgrade(c.connection.driver_connection, apply=False) == "ready"
        assert upgrade.upgrade(c.connection.driver_connection, apply=True) == "upgraded"
        assert data_snapshot(c) == before
        assert upgrade.upgrade(c.connection.driver_connection, apply=True) == "already_upgraded"
        c.exec_driver_sql("RESET ROLE")
        assert c.exec_driver_sql("SELECT * FROM aftersales_reports_rpa").all() == rpa
        assert c.exec_driver_sql("SELECT value FROM monitor.sentinel").scalar() == "preserved"
        assert c.exec_driver_sql("SELECT value FROM extension_data").scalar() == "preserved"
