"""用随机PG的公司对象布局检验只读预检，不接公司端点或改集群角色。"""

import pytest
import sqlalchemy as sa

from tests.test_deployment_database import allocated_database, load_script
from tests.test_deployment_storage_policy import context
from tests.test_deployment_upgrade import data_snapshot, deployed12

__all__ = ["allocated_database", "deployed12", "context"]


def test_company_policy_preflight_preserves_external_objects_and_rejects_drift(
    context, deployed12, monkeypatch
):
    from migrations_v2 import company_db_objects as objects

    m, root = context
    upgrade = load_script("storage_company_upgrade", "upgrade-deployment-db.py")
    with deployed12.connect() as c:
        c.exec_driver_sql("SET ROLE pdd_app")
        assert upgrade.upgrade(c.connection.driver_connection, apply=True) == "upgraded"
        c.exec_driver_sql("RESET ROLE")
        c.exec_driver_sql("GRANT USAGE ON SCHEMA monitor TO pdd_app")
        c.exec_driver_sql("GRANT SELECT ON monitor.sentinel, extension_data TO pdd_app")
        before = data_snapshot(c)
        protected = objects.protected_catalog(c.connection.driver_connection)
    # 传输边界单独反例覆盖；此处只映射本机随机端点，不增加产品中的测试旁路。
    monkeypatch.setattr(m, "DATABASE", deployed12.url.database)
    monkeypatch.setattr(m, "require_endpoint", lambda e: None)

    def identity(c):
        c.exec_driver_sql("SET LOCAL ROLE pdd_app")
        assert c.scalar(sa.text("SELECT current_database()")) == deployed12.url.database
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == "20261114_18"

    monkeypatch.setattr(m, "require_session", identity)
    rpa = objects.rpa_manifest
    monkeypatch.setattr(objects, "rpa_manifest", lambda c: rpa(c, owner="pdd_admin"))

    def local_company(c):
        assert c.execute("SELECT value FROM monitor.sentinel").fetchone() == ("preserved",)
        assert c.execute("SELECT value FROM extension_data").fetchone() == ("preserved",)

    monkeypatch.setattr(objects, "verify_company_protections", local_company)
    result = m.configure(deployed12)
    assert result["schema_head"] == "20261114_18"
    assert result["candidate_versions"] == 0
    assert result["production_delete_enabled"] is False
    assert not (root / "var/storage-policy.json").exists()
    with deployed12.connect() as c:
        assert data_snapshot(c) == before
        assert objects.protected_catalog(c.connection.driver_connection) == protected
        c.exec_driver_sql("ALTER TABLE aftersales_reports_rpa ADD COLUMN unknown text")
    with pytest.raises(ValueError, match="DEPLOY_RPA_MISMATCH"):
        m.configure(deployed12, "enable")
    assert not (root / "var/storage-policy.json").exists()
