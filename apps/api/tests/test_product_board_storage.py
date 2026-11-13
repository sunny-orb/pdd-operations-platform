from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore

pytest_plugins = ["tests.sales_pg_support"]


def candidate(amount="23.17", duplicate_profile=True):
    rows = {
        "product_daily": normalize(
            "product_daily",
            [
                dict(
                    z_id=1,
                    shop_id="663599807",
                    spu_id="123",
                    dt="2026-10-26",
                    time_granularity="day",
                    transaction_amount=amount,
                    product_uv=10,
                    transaction_users=2,
                    transaction_orders=3,
                )
            ],
            date(2026, 10, 26),
        ),
        "profile_records": normalize(
            "profile_records",
            [
                dict(__id=i, spigf="123", pl="冲锋衣", cpdj="新品", spmc="商品A")
                for i in (1, 2)
                if duplicate_profile or i == 1
            ],
            date(2026, 10, 26),
        ),
        "product_promotion": [],
    }
    return date(2026, 10, 26), rows


def test_atomic_idempotent_and_profiles_preserved(sales_pg, tmp_path):
    store = ProductBoardStore(sales_pg, tmp_path)
    first = store.run(lambda hb: candidate())
    again = store.run(lambda hb: candidate())
    assert first["versions"] == again["versions"]
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM current_datasets")).scalar_one() == 4
        assert c.execute(sa.text("SELECT count(*) FROM profile_listing_links")).scalar_one() == 2
        assert (
            str(
                c.execute(
                    sa.text("SELECT sum(product_revenue) FROM product_daily_facts")
                ).scalar_one()
            )
            == "23.17"
        )
    with pytest.raises(ValueError, match="SOURCE_DELETE_UNCONFIRMED"):
        end, rows = candidate(duplicate_profile=False)
        store.run(lambda hb: (end, rows))
    with sales_pg.connect() as c:
        assert (
            dict(c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all())
            == first["versions"]
        )
        assert (
            c.execute(sa.text("SELECT count(*) FROM task_runs WHERE status='failed'")).scalar_one()
            == 1
        )


def test_failed_receiver_does_not_publish(sales_pg, tmp_path):
    def fail(hb):
        raise ValueError("SOURCE_CHANGED_DURING_READ")

    with pytest.raises(ValueError):
        ProductBoardStore(sales_pg, tmp_path).run(fail)
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM current_datasets")).scalar_one() == 0


def test_profile_rebuild_preserves_sources_and_is_idempotent(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import storage
    from app.modules.product_board.repository import snapshot

    end, rows = candidate(duplicate_profile=False)
    rows["profile_records"][0]["source_listing_ids_official_raw"] = "123-下架"
    store = ProductBoardStore(sales_pg, tmp_path)
    with monkeypatch.context() as old_policy:
        old_policy.setattr(storage, "parse_ids", lambda value: ())
        first = store.run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        source_success = repo.meta()["success_at"]

    def source_failed(hb):
        raise ValueError("SOURCE_DOWN")

    with pytest.raises(ValueError, match="SOURCE_DOWN"):
        store.run(source_failed)
    with sales_pg.connect() as c:
        source_before = c.execute(
            sa.text(
                "SELECT id,received_at FROM dataset_versions WHERE origin_kind='source' ORDER BY id"
            )
        ).all()
    rebuilt = store.rebuild_profiles()
    assert rebuilt["versions"]["profile_links"] != first["versions"]["profile_links"]
    for code in storage.TABLES:
        assert rebuilt["versions"][code] == first["versions"][code]
    with snapshot(sales_pg) as repo:
        profile = next(iter(repo.profiles().values()))
        assert profile["category"] == "冲锋衣"
        assert repo.meta()["success_at"] == source_success
        source_failure = repo.meta()["latest_attempt"]
        assert source_failure["status"] == "failed"
    from app.modules.promotion.repository import snapshot as promotion_snapshot

    with promotion_snapshot(sales_pg, "product") as repo:
        for source in repo.metadata()["sources"]:
            assert source["last_success_at"] == source_success
            assert source["latest_attempt"] == source_failure
    again = store.rebuild_profiles()
    assert again["versions"] == rebuilt["versions"]
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text(
                    "SELECT id,received_at FROM dataset_versions "
                    "WHERE origin_kind='source' ORDER BY id"
                )
            ).all()
            == source_before
        )
        assert c.execute(sa.text("SELECT count(*) FROM product_daily_facts")).scalar_one() == 1
        assert c.execute(
            sa.text("SELECT sum(product_revenue) FROM product_daily_facts")
        ).scalar_one() == Decimal("23.17")
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM task_runs WHERE job_code='rebuild_product_profiles'")
            ).scalar_one()
            == 2
        )


def test_failed_profile_rebuild_keeps_all_current_inputs(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import storage

    store = ProductBoardStore(sales_pg, tmp_path)
    first = store.run(lambda hb: candidate())

    def fail(*args, **kwargs):
        raise ValueError("PROFILE_REBUILD_TEST_FAILURE")

    monkeypatch.setattr(storage, "parse_ids", lambda value: ())
    monkeypatch.setattr(store, "_stage", fail)
    with pytest.raises(ValueError, match="PROFILE_REBUILD_TEST_FAILURE"):
        store.rebuild_profiles()
    with sales_pg.connect() as c:
        assert (
            dict(c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all())
            == first["versions"]
        )


def test_code_change_during_source_read_cannot_publish(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import storage

    def receive(hb):
        monkeypatch.setattr(storage, "producer_hash", lambda: "changed-on-disk")
        return candidate()

    with pytest.raises(ValueError, match="PRODUCER_CODE_CHANGED"):
        ProductBoardStore(sales_pg, tmp_path).run(receive)
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM current_datasets")).scalar_one() == 0


def test_reused_source_id_cannot_silently_move_business_identity(sales_pg, tmp_path):
    store = ProductBoardStore(sales_pg, tmp_path)
    store.run(lambda hb: candidate())
    end, rows = candidate()
    raw = dict(
        z_id=1,
        shop_id="663599807",
        spu_id="456",
        dt="2026-10-26",
        time_granularity="day",
        transaction_amount="23.17",
    )
    rows["product_daily"] = normalize("product_daily", [raw], end)
    with pytest.raises(ValueError, match="SOURCE_KEY_IDENTITY_CHANGED"):
        store.run(lambda hb: (end, rows))


def test_task_events_use_database_clock_when_worker_clock_differs(sales_pg, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.modules.product_board import storage

    class WorkerClock:
        @staticmethod
        def now(*_):
            return datetime.now(UTC) + timedelta(days=1)

    monkeypatch.setattr(storage, "datetime", WorkerClock, raising=False)

    def fail(hb):
        raise ValueError("SOURCE_DOWN")

    with pytest.raises(ValueError, match="SOURCE_DOWN"):
        ProductBoardStore(sales_pg, tmp_path).run(fail)
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT status FROM task_runs")).scalar_one() == "failed"


def test_empty_comparison_window_is_no_records_not_observed(sales_pg, tmp_path):
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: candidate())
    with sales_pg.connect() as c:
        state = c.execute(
            sa.text(
                "SELECT availability_state FROM coverage_records r JOIN dataset_versions v "
                "ON r.dataset_version_id=v.id WHERE v.dataset_code='product_daily' "
                "AND r.window_end<'2026-01-01'::date"
            )
        ).scalar_one()
        assert state == "no_records"

    from app.modules.product_board.repository import snapshot

    with snapshot(sales_pg) as repo:
        sources = {r["dataset"]: r for r in repo.meta()["sources"]}
        assert sources["product_promotion"]["status"] == "no_records"
        assert sources["product_daily"]["status"] == "partial"


def test_reconciled_full_scope_can_remove_source_rows_without_deleting_history(sales_pg, tmp_path):
    import sqlalchemy as sa

    store = ProductBoardStore(sales_pg, tmp_path, reconcile_full_scope=True)
    end, rows = candidate()
    first = store.run(lambda hb: (end, rows))
    rows["profile_records"] = rows["profile_records"][:1]
    second = store.run(lambda hb: (end, rows))
    assert first["versions"]["profile_records"] != second["versions"]["profile_records"]
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text(
                    "SELECT count(*) FROM public.profile_source_records WHERE dataset_version_id=:v"
                ),
                dict(v=first["versions"]["profile_records"]),
            ).scalar_one()
            > 0
        )


def test_selected_source_preserves_other_versions_and_receipt_times(sales_pg, tmp_path):
    store = ProductBoardStore(sales_pg, tmp_path, reconcile_full_scope=True)
    first = store.run(lambda hb: candidate())
    end, rows = candidate(amount="41.23")
    result = store.run_selected(
        ("product_daily",), lambda hb: (end, {"product_daily": rows["product_daily"]})
    )
    assert result["versions"]["product_daily"] != first["versions"]["product_daily"]
    assert result["versions"]["product_promotion"] == first["versions"]["product_promotion"]
    assert result["versions"]["profile_records"] == first["versions"]["profile_records"]
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT count(*) FROM dataset_versions WHERE dataset_code='profile_records'"
                )
            )
            == 1
        )
        assert c.scalar(
            sa.text(
                "SELECT sum(product_revenue) FROM product_daily_facts WHERE dataset_version_id=:id"
            ),
            {"id": result["versions"]["product_daily"]},
        ) == Decimal("41.23")


def test_selected_source_without_other_inputs_does_not_expand_download(sales_pg, tmp_path):
    calls = []
    with pytest.raises(ValueError, match="SOURCE_INPUT_MISSING"):
        ProductBoardStore(sales_pg, tmp_path).run_selected(
            ("product_daily",), lambda hb: calls.append(True)
        )
    assert not calls
