from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from app.modules.product_board.query import BoardQuery, score_cards
from app.modules.product_board.repository import snapshot
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from app.modules.product_scoring.publisher import publish_scores

pytest_plugins = ["tests.sales_pg_support"]


def inputs():
    end = date(2026, 10, 26)
    product, ads, profiles = [], [], []
    for i in range(1, 14):
        profiles.append(dict(__id=i, spigf=str(i), pl="帽子" if i < 13 else None, cpdj="新品"))
        for offset in range(30):
            day = str(end - timedelta(days=offset))
            product.append(
                dict(
                    z_id=f"{i}-{day}",
                    shop_id="663599807",
                    spu_id=str(i),
                    dt=day,
                    time_granularity="day",
                    product_uv=10 * i,
                    transaction_amount=str(100 * i),
                    transaction_products=i,
                    transaction_orders=i,
                    transaction_users=i,
                    favorite_product_users=0,
                )
            )
            ads.append(
                dict(
                    id=f"{i}-{day}",
                    shop_id="663599807",
                    spu_id=str(i),
                    dt=day,
                    start_dt=day,
                    time_granularity="day",
                    total_cost=str(i),
                    transaction_amount=str(2 * i),
                    impression_count=3000,
                    click_count=100,
                    transaction_count=10,
                )
            )
    raw = dict(product_daily=product, product_promotion=ads, profile_records=profiles)
    return end, {code: normalize(code, rows, end) for code, rows in raw.items()}


def test_published_trial_is_atomic_idempotent_and_keeps_unknown_category(sales_pg, tmp_path):
    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: inputs())
    first = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    second = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert first["versions"] == second["versions"]
    with snapshot(sales_pg) as repo:
        page = score_cards(repo, BoardQuery(repo.start, repo.end, size=100))
        assert page["total"] == 13
        assert page["groups"] == dict(complete=0, pending=12, unscored=1)
        assert {item["decision"] for item in page["items"]} == {"pending_validation", "unscored"}
        assert all(item["complete_score"] is None for item in page["items"])
        assert all(int(item["pending_weight"]) >= 14 for item in page["items"])
        assert page["items"][-1]["product_id"] == "13"
        assert str(page["items"][0]["score"]) == "76"
        assert "归因窗口" not in page["items"][0]["reason"]
        assert "完整日" in page["items"][0]["reason"]
        from app.api.product_board_schemas import ScoresData
        from app.modules.product_board.repository import wire

        ScoresData.model_validate(wire(page))
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM product_scores")).scalar_one() == 13
        assert c.execute(sa.text("SELECT count(*) FROM product_score_items")).scalar_one() == 130
        assert c.execute(sa.text("SELECT count(*) FROM score_standard_items")).scalar_one() == 130
        assert (
            c.execute(
                sa.text("SELECT count(*) FROM score_standards WHERE basis_kind='proposed'")
            ).scalar_one()
            == 13
        )


def test_scheduler_runs_source_and_real_scorer_with_bounded_runtime_pool(sales_pg, tmp_path):
    from app.jobs.project_sync import run_project
    from app.jobs.sync_shop_daily import sync_engine

    engine = sync_engine(sales_pg.url)
    try:
        result = run_project(
            engine,
            "integrated-source-score",
            {
                "products": lambda: ProductBoardStore(engine, tmp_path / "source").run(
                    lambda hb: inputs()
                ),
                "product_scores": lambda: publish_scores(engine, archive_root=tmp_path / "scores"),
            },
            tmp_path / "scheduler",
            depends_on={"product_scores": ("products",)},
        )
        assert result["status"] == "succeeded", result
        with snapshot(engine) as repo:
            assert score_cards(repo, BoardQuery(repo.start, repo.end))["status"] == "trial_active"
    finally:
        engine.dispose()


def test_failed_publication_preserves_previous_pointer(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_scoring import publisher

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: inputs())
    first = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    original = publisher.validate_bundle

    def fail(bundle):
        original(bundle)
        raise ValueError("INJECTED_PUBLICATION_FAILURE")

    monkeypatch.setattr(publisher, "validate_bundle", fail)
    with pytest.raises(ValueError, match="INJECTED_PUBLICATION_FAILURE"):
        publish_scores(sales_pg, archive_root=tmp_path / "scores")
    with sales_pg.connect() as c:
        actual = dict(
            c.execute(
                sa.text(
                    "SELECT dataset_code,version_id FROM current_datasets "
                    "WHERE dataset_code IN ('score_standards','product_scores')"
                )
            ).all()
        )
        assert actual == first["versions"]


def test_source_refresh_hides_old_scores_until_compatible_republication(sales_pg, tmp_path):
    from decimal import Decimal

    store = ProductBoardStore(sales_pg, tmp_path / "source")
    store.run(lambda hb: inputs())
    first = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    end, rows = inputs()
    rows["product_daily"][0]["product_revenue"] += Decimal("1")
    store.run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        stale = score_cards(repo, BoardQuery(repo.start, repo.end, size=100))
        assert stale["status"] == "pending_recompute"
        assert all(item["score"] is None for item in stale["items"])
        assert stale["total"] == 13
    second = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert first["versions"] != second["versions"]
    with snapshot(sales_pg) as repo:
        assert score_cards(repo, BoardQuery(repo.start, repo.end))["status"] == "trial_active"


def test_mid_transaction_failure_rolls_back_both_outputs(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_scoring import publisher

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: inputs())
    original = publisher._seal

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("MID_TRANSACTION_FAILURE")

    monkeypatch.setattr(publisher, "_seal", fail)
    with pytest.raises(ValueError, match="MID_TRANSACTION_FAILURE"):
        publish_scores(sales_pg, archive_root=tmp_path / "scores")
    with sales_pg.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM score_standards")).scalar_one() == 0
        assert c.execute(sa.text("SELECT count(*) FROM product_scores")).scalar_one() == 0
        assert (
            c.execute(
                sa.text(
                    "SELECT count(*) FROM current_datasets "
                    "WHERE dataset_code IN ('score_standards','product_scores')"
                )
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(
                sa.text(
                    "SELECT count(*) FROM task_runs WHERE job_code='score_product_board' "
                    "AND status='failed'"
                )
            ).scalar_one()
            == 1
        )


def test_scoring_does_not_overlap_source_reception(sales_pg, tmp_path):
    with sales_pg.connect() as other:
        other.execute(sa.text("SELECT pg_advisory_lock(hashtext('m1-product-board'))"))
        other.commit()
        try:
            with pytest.raises(ValueError, match="M1_SYNC_OR_SCORING_RUNNING"):
                publish_scores(sales_pg, archive_root=tmp_path / "scores")
        finally:
            other.execute(sa.text("SELECT pg_advisory_unlock(hashtext('m1-product-board'))"))
            other.commit()


def test_real_writer_can_lock_parent_with_column_grant_and_cannot_mutate_sealed(sales_pg, tmp_path):
    from app.jobs.sync_shop_daily import runtime_engine

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: inputs())
    configured = runtime_engine()
    writer = sa.create_engine(
        configured.url.set(database=sales_pg.url.database), hide_parameters=True
    )
    configured.dispose()
    try:
        result = publish_scores(writer, archive_root=tmp_path / "writer")
        with sales_pg.connect() as c:
            for table in ("score_standards", "product_scores"):
                assert c.execute(
                    sa.text("SELECT has_column_privilege('pdd_ingest',:t,'id','UPDATE')"),
                    dict(t=table),
                ).scalar_one()
                assert not c.execute(
                    sa.text("SELECT has_table_privilege('pdd_ingest',:t,'UPDATE')"), dict(t=table)
                ).scalar_one()
        with writer.begin() as c, pytest.raises(sa.exc.IntegrityError):
            c.execute(
                sa.text("UPDATE score_standards SET id=id WHERE dataset_version_id=:v"),
                dict(v=result["versions"]["score_standards"]),
            )
    finally:
        writer.dispose()
