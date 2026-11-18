"""生产启用边界：本地文件系统和随机PG，不访问生产端点。"""

import json
import os
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]
URL = "postgresql+psycopg://songbing:synthetic@pg.kktree.cn:5432/pdd_dashboard?sslmode=require"


def module():
    from app.modules import deployment_storage

    return deployment_storage


@pytest.fixture
def context(monkeypatch, tmp_path):
    m = module()
    root = tmp_path.resolve() / "app"
    root.mkdir(mode=0o700)
    (root / "var").mkdir(mode=0o700)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CONTENT_REUSE_ENABLED", raising=False)
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(m, "EXPECTED_ROOT", root)
    monkeypatch.setattr(m, "UID", os.getuid())
    return m, root


def engine(url=URL):
    return SimpleNamespace(url=sa.engine.make_url(url))


def save(m, root, **changes):
    value = {
        "revision": 1,
        "project": "/home/songbing/pdd-operations-platform",
        "account": "songbing",
        "database": "pdd_dashboard",
        "tables": ["product_daily_facts", "product_promotion_records", "product_region_records"],
    }
    value.update(changes)
    path = root / "var/storage-policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_missing_policy_keeps_original_and_explicit_policy_only_switches_three_tables(context):
    m, root = context
    assert not m.content_enabled(engine(), "product_daily_facts")
    save(m, root)
    for table in ("product_daily_facts", "product_promotion_records", "product_region_records"):
        assert m.content_enabled(engine(), table)
    for table in ("product_sales_records", "return_records", "aftersales_reports", "other"):
        assert not m.content_enabled(engine(), table)


@pytest.mark.parametrize(
    "change",
    [
        {"username": "other"},
        {"database": "pdd_operations"},
        {"host": "evil.example"},
        {"port": 5433},
        {"drivername": "postgresql"},
        {"query": {"sslmode": "disable"}},
        {"query": {"sslmode": "require", "hostaddr": "127.0.0.1"}},
        {"query": {"sslmode": "require", "options": "-c role=postgres"}},
    ],
)
def test_unapproved_endpoint_rejected_before_connection(context, change):
    m, root = context
    save(m, root)
    bad = SimpleNamespace(url=engine().url.set(**change))
    with pytest.raises(ValueError, match="STORAGE_ENDPOINT"):
        m.content_enabled(bad, "product_daily_facts")


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"project": "/home/someone/project"},
        {"account": "postgres"},
        {"database": "other"},
        {"tables": ["return_records"]},
        {"tables": ["product_daily_facts", "product_daily_facts"]},
        {"extra": 1},
    ],
)
def test_invalid_policy_fails_closed_without_switching_aftersales(context, changes):
    m, root = context
    save(m, root, **changes)
    with pytest.raises(ValueError, match="STORAGE_POLICY"):
        m.content_enabled(engine(), "product_daily_facts")
    assert not m.content_enabled(engine(), "return_records")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "directory", "wrong-root"])
def test_unsafe_policy_paths_refused(context, kind, monkeypatch):
    m, root = context
    path = save(m, root)
    if kind == "symlink":
        target = path.with_suffix(".other")
        path.rename(target)
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(path, path.with_suffix(".other"))
    elif kind == "public":
        path.chmod(0o644)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    else:
        monkeypatch.setattr(m, "EXPECTED_ROOT", root / "other")
    with pytest.raises(ValueError):
        m.content_enabled(engine(), "product_daily_facts")


def connection(
    *, login="songbing", current="songbing", host="pg.kktree.cn", ssl=True, head="20261114_18"
):
    raw = SimpleNamespace(
        info=SimpleNamespace(host=host, port=5432), pgconn=SimpleNamespace(ssl_in_use=ssl)
    )
    return SimpleNamespace(
        engine=engine(),
        connection=SimpleNamespace(driver_connection=raw),
        execute=lambda *_: SimpleNamespace(one=lambda: ("pdd_dashboard", login, current, "170005")),
        scalar=lambda *_: head,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"login": "other"},
        {"current": "postgres"},
        {"host": "other"},
        {"ssl": False},
        {"head": "20261111_16"},
        {"head": "20261116_19"},
    ],
)
def test_actual_session_transport_and_head_are_checked(context, change):
    m, _ = context
    with pytest.raises(ValueError):
        m.require_session(connection(**change))
    m.require_session(connection())


def test_new_policy_does_not_change_local_or_unconfigured_environment(context, monkeypatch):
    m, root = context
    save(m, root)
    monkeypatch.setenv("APP_ENV", "development")
    assert not m.content_enabled(engine(), "product_daily_facts")


def test_scoped_production_policy_reuses_pg_content_and_leaves_aftersales_legacy(
    context, sales_pg, monkeypatch, tmp_path
):
    from tests.test_return_summary_index import revision_sql

    # 公司生产契约仍固定18；19仅为本地说明修订，不扩大生产接受的head。
    with sales_pg.begin() as c:
        c.connection.driver_connection.execute(
            revision_sql("20261116_19", "20261114_18", downgrade=True), prepare=False
        )
    m, root = context
    save(m, root)
    # 只替代生产传输身份边界；业务发布/事务/视图/旧报告均走真实隔离PG。
    monkeypatch.setattr(m, "require_endpoint", lambda e: None)

    def identity(c):
        assert c.scalar(sa.text("SELECT current_database()")) == sales_pg.url.database
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == "20261114_18"

    monkeypatch.setattr(m, "require_session", identity)
    from tests.test_aftersales_ingestion import (
        test_real_pg_source_report_idempotence_and_failure_preserves_pointer as aftersales,
    )
    from tests.test_fact_content_api import (
        test_product_promotion_business_contracts_with_shared_facts as products,
    )
    from tests.test_product_regions_incremental import (
        test_incremental_equals_full_and_old_version_stays_readable as regions,
    )

    aftersales(sales_pg, tmp_path / "aftersales")
    products(sales_pg, tmp_path / "products")
    # 此测试不能用测试总开关代替生产策略。
    with monkeypatch.context() as isolated:
        regions(sales_pg, tmp_path / "regions", isolated, False)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_daily_facts")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM product_sales_records")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_return_records")) == 0


def test_enable_disable_are_explicit_atomic_and_leave_database_unchanged(
    context, sales_pg, monkeypatch
):
    m, root = context
    monkeypatch.setattr(m, "require_endpoint", lambda e: None)
    monkeypatch.setattr(m, "require_session", lambda c: None)
    with sales_pg.connect() as c:
        before = c.exec_driver_sql("SELECT version_num FROM alembic_version_v2").scalar()
    result = m.configure(sales_pg)
    assert result["enabled_tables"] == [] and result["production_delete_enabled"] is False
    assert not (root / "var/storage-policy.json").exists()
    assert len(m.configure(sales_pg, "enable")["enabled_tables"]) == 3
    path = root / "var/storage-policy.json"
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    with sales_pg.begin() as blocker:
        blocker.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('project-source-sync'))")
        with pytest.raises(ValueError, match="STORAGE_BUSY"):
            m.configure(sales_pg, "disable")
    assert path.read_bytes() == original

    def disk_failure(*args):
        raise OSError("synthetic disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "replace", disk_failure)
        with pytest.raises(OSError):
            m.configure(sales_pg, "enable")
    assert path.read_bytes() == original
    assert not list((root / "var").glob(".storage-policy-*"))
    assert m.configure(sales_pg, "disable")["enabled_tables"] == []
    assert not path.exists()
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT version_num FROM alembic_version_v2")) == before
        assert c.scalar(sa.text("SELECT count(*) FROM dataset_versions")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM retention_operations")) == 0


def test_running_task_prevents_policy_change(context, sales_pg, monkeypatch):
    m, root = context
    monkeypatch.setattr(m, "require_endpoint", lambda e: None)
    monkeypatch.setattr(m, "require_session", lambda c: None)
    with sales_pg.begin() as c:
        c.exec_driver_sql("""INSERT INTO task_runs(id,job_code,request_ref,lock_key,status,
            queued_at,started_at,worker_ref,lease_expires_at) VALUES
            ('running','sync_shop_daily','test','test','running',clock_timestamp(),
            clock_timestamp(),'test',clock_timestamp()+interval '1 minute')""")
    with pytest.raises(ValueError, match="RETENTION_BUSY"):
        m.configure(sales_pg, "enable")
    assert not (root / "var/storage-policy.json").exists()


def test_test_only_switch_cannot_pass_production_preflight(context, monkeypatch):
    m, _ = context
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    with pytest.raises(ValueError, match="STORAGE_TEST_SWITCH"):
        m.require_context()


def test_disabling_policy_keeps_shared_history_readable_and_new_publications_work(
    context, sales_pg, monkeypatch, tmp_path
):
    from decimal import Decimal

    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_extended_sources import sample, window

    m, root = context
    save(m, root)
    monkeypatch.setattr(m, "require_endpoint", lambda e: None)
    monkeypatch.setattr(m, "require_session", lambda c: None)
    store = ShopDailyStore(
        sales_pg,
        archive_root=tmp_path / "archives",
        approved=window(),
        spec=SPECS["product_regions"],
    )
    for index, amount in enumerate(("100.01", "101.99", "103.45")):
        if index == 1:
            assert m.configure(sales_pg, "disable")["enabled_tables"] == []
        elif index == 2:
            assert len(m.configure(sales_pg, "enable")["enabled_tables"]) == 3
        candidate = normalize_source(
            "product_regions",
            [sample("product_regions", actual_shipped_amount=Decimal(amount))],
            window(),
        )
        assert store.publish_candidate(candidate).status == "succeeded"
    with sales_pg.connect() as c:
        assert c.exec_driver_sql(
            "SELECT actual_shipped_amount FROM read_product_region_records "
            "ORDER BY actual_shipped_amount"
        ).scalars().all() == [Decimal("100.01"), Decimal("101.99"), Decimal("103.45")]
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 1
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_region_records")) == 2
