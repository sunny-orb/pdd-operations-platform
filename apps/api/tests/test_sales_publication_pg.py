"""新版发布的真实PG测试；只写随机隔离库。"""

from datetime import date

import pytest
import sqlalchemy as sa
from test_sales_source import raw_row

from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_policy import SHOPS
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def test_publish_reuse_failed_change_and_immutable_history(sales_pg, tmp_path):
    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    candidate = normalize_shop_daily([raw_row()], window)
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window)
    first = store.publish_candidate(candidate)
    again = store.publish_candidate(candidate)
    assert again.reused and again.version_id == first.version_id
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM public.shop_daily_facts")).scalar_one() == 1
    changed = normalize_shop_daily(
        [
            raw_row(
                metrics={
                    "成交金额": "20.20",
                    "店铺访客数": "10",
                    "成交买家数": "2",
                    "成交订单数": "3",
                }
            )
        ],
        window,
    )
    second = store.publish_candidate(changed)
    assert second.version_id != first.version_id
    with sales_pg.begin() as c:
        assert (
            c.execute(
                sa.text("SELECT COUNT(*) FROM public.task_runs WHERE status='succeeded'")
            ).scalar_one()
            == 3
        )
        assert (
            c.execute(
                sa.text(
                    "SELECT version_id FROM public.current_datasets WHERE dataset_code='shop_daily'"
                )
            ).scalar_one()
            == second.version_id
        )
    with pytest.raises(sa.exc.DBAPIError), sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE shop_daily_facts SET shop_revenue=999 WHERE dataset_version_id=:v"),
            dict(v=first.version_id),
        )
    missing = normalize_shop_daily([raw_row(id="other")], window)
    with pytest.raises(SalesError, match="SOURCE_OLD_KEY_MISSING"):
        store.publish_candidate(missing)
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT version_id FROM current_datasets WHERE dataset_code='shop_daily'")
            ).scalar_one()
            == second.version_id
        )
        assert (
            c.execute(sa.text("SELECT COUNT(*) FROM task_runs WHERE status='failed'")).scalar_one()
            == 1
        )


def test_partial_publish_refused_before_side_effects(sales_pg, tmp_path):
    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    candidate = normalize_shop_daily([raw_row()], window)
    store = ShopDailyStore(sales_pg, archive_root=tmp_path)
    with pytest.raises(SalesError, match="PUBLICATION_SCOPE_MISMATCH"):
        store.publish_candidate(candidate)


def test_real_runtime_role_has_only_authorized_writes(sales_pg, tmp_path):
    from pathlib import Path

    from migrations_v2.local import local_url

    root = Path(__file__).resolve().parents[3]
    password = (root / "var/local-db/sync.env").read_text().strip().split("=", 1)[1]
    url = local_url(sales_pg.url.database).set(username="pdd_ingest", password=password)
    runtime = sa.create_engine(url, hide_parameters=True)
    try:
        window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
        store = ShopDailyStore(runtime, archive_root=tmp_path, approved=window)
        result = store.publish_candidate(normalize_shop_daily([raw_row()], window))
        assert result.status == "succeeded"
        with pytest.raises(sa.exc.DBAPIError), runtime.begin() as c:
            c.execute(sa.text("DELETE FROM task_runs"))
        with pytest.raises(sa.exc.DBAPIError), runtime.begin() as c:
            c.execute(sa.text("CREATE TABLE unauthorized(id TEXT)"))
        with pytest.raises(sa.exc.DBAPIError), runtime.begin() as c:
            c.execute(sa.text("UPDATE shop_daily_facts SET shop_revenue=1"))
        with runtime.connect() as c:
            assert not c.execute(
                sa.text("SELECT has_table_privilege('pdd_app','shop_daily_facts','INSERT')")
            ).scalar_one()
    finally:
        runtime.dispose()


def test_failed_receive_lock_and_expired_lease_preserve_success(sales_pg, tmp_path):
    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window)
    candidate = normalize_shop_daily([raw_row()], window)
    previous = store.publish_candidate(candidate)

    def receive(heartbeat):
        with pytest.raises(SalesError, match="SYNC_ALREADY_RUNNING"):
            store.publish_candidate(candidate)
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "UPDATE task_runs SET lease_expires_at=clock_timestamp()-interval '1 "
                    "second' WHERE status='running'"
                )
            )
        heartbeat(force=True)
        return candidate

    with pytest.raises(SalesError, match="TASK_LEASE_LOST"):
        store.run(receive)
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT version_id FROM current_datasets WHERE dataset_code='shop_daily'")
            ).scalar_one()
            == previous.version_id
        )
    recovered = store.publish_candidate(candidate)
    assert recovered.reused
    path = next(tmp_path.glob("*/input.ndjson"))
    assert path.stat().st_mode & 0o077 == 0
    assert path.read_text().count("\n") == 1


def test_publication_transaction_failure_preserves_pointer(sales_pg, tmp_path):
    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window)
    candidate = normalize_shop_daily([raw_row()], window)
    previous = store.publish_candidate(candidate)

    def fail(conn, cursor, statement, params, context, many):
        if "INSERT INTO public.current_datasets" in statement:
            raise RuntimeError("synthetic interrupt before publish")

    sa.event.listen(sales_pg, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            store.publish_candidate(candidate)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail)
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text("SELECT version_id FROM current_datasets WHERE dataset_code='shop_daily'")
            ).scalar_one()
            == previous.version_id
        )
        assert (
            c.execute(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")).scalar_one()
            == 1
        )


def test_retained_input_includes_recoverable_producer_code(sales_pg, tmp_path):
    import json

    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    result = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window).publish_candidate(
        normalize_shop_daily([raw_row()], window)
    )
    folder = tmp_path / result.version_id
    sources = json.loads((folder / "producer-source.json").read_text())
    assert "modules/sales/normalization.py" in sources
    manifest = json.loads((folder / "interpretation.json").read_text())
    from app.modules.sales.normalization import fingerprint

    assert manifest["producer_revision"] == "sha256:" + fingerprint(sources)


def test_changed_region_recovery_code_requires_worker_restart(sales_pg, tmp_path, monkeypatch):
    """新恢复模块参与生产者身份；进程启动后换代码不得混用旧解释发布。"""
    from pathlib import Path

    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window)
    read = Path.read_text

    def changed(path, *args, **kwargs):
        content = read(path, *args, **kwargs)
        return content + "\n# changed" if path.name == "region_retention.py" else content

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(SalesError, match="PRODUCER_CODE_CHANGED"):
        store.run(lambda heartbeat: pytest.fail("代码变化后不得开始取数"))
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM task_runs")) == 0


def test_archive_replay_preserves_source_types_and_matches_postgresql(sales_pg, tmp_path):
    from decimal import Decimal

    from app.modules.sales.archive_replay import restore_candidate

    window = ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))
    original = normalize_shop_daily(
        [
            raw_row(
                metrics={
                    "成交金额": "10.20",
                    "店铺访客数": 10,
                    "成交买家数": "2",
                    "成交订单数": 3,
                    "客单价": "-",
                    "成交UV价值": Decimal("1.020"),
                }
            )
        ],
        window,
    )
    result = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window).publish_candidate(
        original
    )
    folder = tmp_path / result.version_id
    restored = restore_candidate(folder)
    assert restored.fingerprint == original.fingerprint
    metrics = restored.raw_rows[0]["metrics"]
    assert isinstance(metrics["成交金额"], str)
    assert type(metrics["店铺访客数"]) is int
    assert isinstance(metrics["成交UV价值"], Decimal)
    assert metrics["客单价"] == "-"
    with sales_pg.connect() as c:
        actual = (
            c.execute(
                sa.text("SELECT * FROM shop_daily_facts WHERE dataset_version_id=:v"),
                {"v": result.version_id},
            )
            .mappings()
            .one()
        )
    for key, value in restored.rows[0].items():
        assert actual[key] == value, key
    assert actual["source_aov"] is None
    path = folder / "input.ndjson"
    path.write_bytes(path.read_bytes().replace(b"10.20", b"99.99"))
    with pytest.raises(SalesError, match="ARCHIVE_CHECKSUM_MISMATCH"):
        restore_candidate(folder)
