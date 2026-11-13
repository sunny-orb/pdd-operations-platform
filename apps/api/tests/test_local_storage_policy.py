"""本地空间策略精确库/角色/表范围，禁止生产。"""

import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]


def engine(database="pdd_operations", role="pdd_ingest", host="127.0.0.1", port=55432):
    return SimpleNamespace(
        url=sa.URL.create(
            "postgresql+psycopg", username=role, host=host, port=port, database=database
        )
    )


def policy_file(monkeypatch, tmp_path, **changes):
    from app.modules import local_storage

    path = tmp_path / "content-reuse.json"
    value = {
        "revision": 1,
        "tables": ["product_daily_facts", "product_promotion_records", "product_region_records"],
    }
    value.update(changes)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setattr(local_storage, "POLICY_FILE", path)
    return path


def test_local_policy_is_explicit_and_excludes_aftersales(monkeypatch, tmp_path):
    from app.modules import fact_content, local_storage

    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    monkeypatch.setattr(local_storage, "POLICY_FILE", tmp_path / "absent")
    c = SimpleNamespace(engine=engine())
    assert not fact_content.enabled(c, "product_daily_facts")
    policy_file(monkeypatch, tmp_path)
    for table in ("product_daily_facts", "product_promotion_records", "product_region_records"):
        assert fact_content.enabled(c, table)
    for table in (None, "product_sales_records", "return_records", "unknown"):
        assert not fact_content.enabled(c, table)


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "remote"},
        {"port": 5432},
        {"role": "pdd_app"},
        {"database": "other"},
        {"database": "pdd_dashboard"},
    ],
)
def test_policy_never_enables_wrong_runtime(monkeypatch, tmp_path, changes):
    from app.modules import fact_content

    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    policy_file(monkeypatch, tmp_path)
    assert not fact_content.enabled(
        SimpleNamespace(engine=engine(**changes)), "product_daily_facts"
    )


@pytest.mark.parametrize(
    "tables", [["return_records"], ["unknown"], ["product_daily_facts", "product_daily_facts"]]
)
def test_unknown_or_aftersales_policy_refused(monkeypatch, tmp_path, tables):
    from app.modules import fact_content

    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    policy_file(monkeypatch, tmp_path, tables=tables)
    with pytest.raises(ValueError, match="LOCAL_STORAGE_POLICY"):
        fact_content.enabled(SimpleNamespace(engine=engine()), "product_daily_facts")


def test_policy_symlink_and_public_permissions_refused(monkeypatch, tmp_path):
    from app.modules import fact_content, local_storage

    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    path = policy_file(monkeypatch, tmp_path)
    link = tmp_path / "link"
    link.symlink_to(path)
    monkeypatch.setattr(local_storage, "POLICY_FILE", link)
    with pytest.raises(ValueError, match="LOCAL_STORAGE_POLICY"):
        fact_content.enabled(SimpleNamespace(engine=engine()), "product_daily_facts")
    monkeypatch.setattr(local_storage, "POLICY_FILE", path)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="LOCAL_STORAGE_POLICY"):
        fact_content.enabled(SimpleNamespace(engine=engine()), "product_daily_facts")


def test_local_maintenance_still_requires_admin_and_exact_endpoint():
    from app.modules.local_storage import require_local_maintenance

    require_local_maintenance(engine(role="pdd_admin"))
    for e in (
        engine(),
        engine(role="pdd_app"),
        engine(role="pdd_admin", host="remote"),
        engine(role="pdd_admin", database="pdd_dashboard"),
    ):
        with pytest.raises(ValueError, match="PRODUCTION_DISABLED"):
            require_local_maintenance(e)


def test_real_connection_cannot_be_disguised_as_project_database(sales_pg):
    from app.modules.local_storage import verify_connection

    with sales_pg.connect() as c, c.begin():
        disguised = SimpleNamespace(engine=engine(role="pdd_admin"), execute=c.execute)
        with pytest.raises(ValueError, match="LOCAL_STORAGE_IDENTITY"):
            verify_connection(disguised)
        verify_connection(c)


def test_local_scoped_publications_reuse_three_tables_without_switching_aftersales(
    sales_pg, monkeypatch, tmp_path
):
    """只在隔离库模拟本机策略，走真实发布/查询；售后普通路径保持legacy。"""
    from app.modules import local_storage
    from tests.test_aftersales_ingestion import (
        test_real_pg_source_report_idempotence_and_failure_preserves_pointer,
    )
    from tests.test_fact_content_api import (
        test_product_promotion_business_contracts_with_shared_facts,
        test_region_exact_values_and_publisher_reads_previous_shared_version,
    )

    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    policy_file(monkeypatch, tmp_path)
    monkeypatch.setattr(local_storage, "project_endpoint", lambda e, **kw: e.url == sales_pg.url)
    test_real_pg_source_report_idempotence_and_failure_preserves_pointer(
        sales_pg, tmp_path / "aftersales"
    )
    test_product_promotion_business_contracts_with_shared_facts(sales_pg, tmp_path / "products")
    test_region_exact_values_and_publisher_reads_previous_shared_version(
        sales_pg, tmp_path / "regions"
    )
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_daily_facts")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_return_records")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM return_records")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM product_sales_records")) > 0


def test_real_ingest_session_can_publish_with_local_policy(sales_pg, monkeypatch, tmp_path):
    """使用真实会话身份，不用SET ROLE或管理员连接冒充同步账号。"""
    from datetime import date
    from decimal import Decimal

    from app.modules import local_storage
    from app.modules.fact_content import write_version
    from migrations_v2.local import ROOT, local_url
    from tests.test_fact_content import seal
    from tests.test_published_versions import publish_pointer

    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    policy_file(monkeypatch, tmp_path)
    monkeypatch.setattr(
        local_storage, "project_endpoint", lambda e, **kw: e.url.database == sales_pg.url.database
    )
    publish_pointer(sales_pg, "product_regions", "ingest-role", published=False)
    password = (ROOT / "var/local-db/sync.env").read_text().strip().split("=", 1)[1]
    ingest = sa.create_engine(
        local_url(sales_pg.url.database).set(username="pdd_ingest", password=password),
        hide_parameters=True,
    )
    try:
        with ingest.begin() as c:
            assert c.scalar(sa.text("SELECT session_user")) == "pdd_ingest"
            write_version(
                c,
                "product_region_records",
                "product_regions-ingest-role",
                [
                    dict(
                        source_row_key="local-policy-test",
                        shop_match_status="unmatched",
                        source_granularity="day",
                        date_status="verified_period",
                        period_start=date(2026, 11, 1),
                        period_end=date(2026, 11, 1),
                        actual_shipped_amount=Decimal("10.01"),
                    )
                ],
            )
            seal(c, "product_regions-ingest-role")
        with ingest.connect() as c:
            assert c.scalar(
                sa.text("SELECT actual_shipped_amount FROM read_product_region_records")
            ) == Decimal("10.01")
        for query in [
            "UPDATE alembic_version_v2 SET version_num='bad'",
            "DELETE FROM shared_product_region_records",
        ]:
            with pytest.raises(sa.exc.DBAPIError):
                with ingest.begin() as c:
                    c.exec_driver_sql(query)
    finally:
        ingest.dispose()
