"""派生结果幂等发布必须保留证据且不重复写归档；真实PG隔离验证。"""

from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.product_board.storage import ProductBoardStore
from app.modules.product_scoring.publisher import publish_scores
from tests.test_product_board_storage import candidate

pytest_plugins = ["tests.sales_pg_support"]


def archive_files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def heads(engine):
    with engine.connect() as c:
        return dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        )


def versions(engine):
    with engine.connect() as c:
        return c.execute(
            sa.text("SELECT id,retained_input_ref,received_at FROM dataset_versions ORDER BY id")
        ).all()


def test_profile_rebuild_noop_preserves_archives_versions_and_receipt(sales_pg, tmp_path):
    store = ProductBoardStore(sales_pg, tmp_path)
    first = store.run(lambda hb: candidate())
    files, before = archive_files(tmp_path), versions(sales_pg)
    for _ in range(2):
        result = store.rebuild_profiles()
        assert result["versions"] == first["versions"]
        assert result["rows"] == first["rows"]
        assert result["end"] == first["end"]
    after_files = archive_files(tmp_path)
    print(
        f"profile archive bytes: {sum(map(len, files.values()))} -> "
        f"{sum(map(len, after_files.values()))}"
    )
    assert after_files == files
    assert versions(sales_pg) == before
    with sales_pg.connect() as c:
        tasks = c.execute(
            sa.text(
                "SELECT status,lease_expires_at,result_version_id,evidence_ref FROM task_runs "
                "WHERE job_code='rebuild_product_profiles'"
            )
        ).all()
    assert len(tasks) == 2
    assert all(
        status == "succeeded"
        and lease is None
        and vid == first["versions"]["profile_links"]
        and Path(evidence).is_file()
        for status, lease, vid, evidence in tasks
    )


def test_score_noop_preserves_archives_versions_and_validates(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_scoring import publisher

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: candidate())
    first = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    files, before = archive_files(tmp_path), versions(sales_pg)
    for _ in range(2):
        assert publish_scores(sales_pg, archive_root=tmp_path / "scores") == first
    after_files = archive_files(tmp_path)
    print(
        f"score archive bytes: {sum(map(len, files.values()))} -> "
        f"{sum(map(len, after_files.values()))}"
    )
    assert after_files == files
    assert versions(sales_pg) == before
    with sales_pg.connect() as c:
        tasks = c.execute(
            sa.text(
                "SELECT status,lease_expires_at,result_version_id,evidence_ref FROM task_runs "
                "WHERE job_code='score_product_board'"
            )
        ).all()
    assert len(tasks) == 3
    assert len({t.evidence_ref for t in tasks}) == 1
    assert all(
        t.status == "succeeded"
        and t.lease_expires_at is None
        and t.result_version_id == first["versions"]["product_scores"]
        for t in tasks
    )

    def reject(bundle):
        raise ValueError("VALIDATION_STILL_REQUIRED")

    monkeypatch.setattr(publisher, "validate_bundle", reject)
    with pytest.raises(ValueError, match="VALIDATION_STILL_REQUIRED"):
        publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert versions(sales_pg) == before


@pytest.mark.parametrize("code", ["product_daily", "profile_links", "product_scores"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_score_reuse_rejects_damaged_archives(sales_pg, tmp_path, code, damage):
    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: candidate())
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    expected = heads(sales_pg)
    with sales_pg.connect() as c:
        ref = Path(
            c.scalar(
                sa.text("SELECT retained_input_ref FROM dataset_versions WHERE id=:id"),
                {"id": expected[code]},
            )
        )
    target = ref / "manifest.json" if ref.is_dir() else ref
    if damage == "missing":
        target.unlink()
    else:
        target.write_text("corrupt")
    files = archive_files(tmp_path)
    with pytest.raises(ValueError, match="ARCHIVE_"):
        publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert heads(sales_pg) == expected
    assert archive_files(tmp_path) == files


def test_changed_profile_rule_publishes_once_then_reuses(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import storage

    store = ProductBoardStore(sales_pg, tmp_path)
    first = store.run(lambda hb: candidate())
    monkeypatch.setattr(storage, "parse_ids", lambda value: ())
    rebuilt = store.rebuild_profiles()
    assert rebuilt["versions"]["profile_links"] != first["versions"]["profile_links"]
    assert all(rebuilt["versions"][code] == first["versions"][code] for code in storage.TABLES)
    files, before = archive_files(tmp_path), versions(sales_pg)
    assert store.rebuild_profiles() == rebuilt
    assert archive_files(tmp_path) == files
    assert versions(sales_pg) == before


@pytest.mark.parametrize("code", ["product_daily", "profile_links"])
def test_profile_rebuild_missing_archive_preserves_heads(sales_pg, tmp_path, code):
    store = ProductBoardStore(sales_pg, tmp_path)
    store.run(lambda hb: candidate())
    expected = heads(sales_pg)
    with sales_pg.connect() as c:
        ref = Path(
            c.scalar(
                sa.text("SELECT retained_input_ref FROM dataset_versions WHERE id=:id"),
                {"id": expected[code]},
            )
        )
    (ref / "manifest.json" if ref.is_dir() else ref).unlink()
    files = archive_files(tmp_path)
    with pytest.raises(ValueError, match="ARCHIVE_"):
        store.rebuild_profiles()
    assert heads(sales_pg) == expected
    assert archive_files(tmp_path) == files


def test_changed_score_result_with_same_inputs_writes_real_publication(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.product_scoring import publisher
    from tests.test_product_scoring_publication import inputs

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: inputs())
    first = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    before = versions(sales_pg)
    build = publisher.build_bundle

    def revised_result(repo):
        bundle = build(repo)
        bundle["scores"][0]["raw_features"]["visitors_7d"] += 1
        return bundle

    monkeypatch.setattr(publisher, "build_bundle", revised_result)
    second = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert second["versions"]["score_standards"] == first["versions"]["score_standards"]
    assert second["versions"]["product_scores"] != first["versions"]["product_scores"]
    assert len(list((tmp_path / "scores").glob("*/publication.json"))) == 2
    assert all(row in versions(sales_pg) for row in before)
    files = archive_files(tmp_path)
    assert publish_scores(sales_pg, archive_root=tmp_path / "scores") == second
    assert archive_files(tmp_path) == files


def test_changed_inputs_and_producer_each_publish_once(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_scoring import publisher, service

    store = ProductBoardStore(sales_pg, tmp_path / "source")
    store.run(lambda hb: candidate())
    first = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    store.run(lambda hb: candidate(amount="45.67"))
    second = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert all(first["versions"][k] != second["versions"][k] for k in first["versions"])
    monkeypatch.setattr(publisher, "producer_hash", lambda: "new-producer")
    monkeypatch.setattr(service, "producer_hash", lambda: "new-producer")
    third = publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert all(second["versions"][k] != third["versions"][k] for k in second["versions"])
    files, before = archive_files(tmp_path), versions(sales_pg)
    assert publish_scores(sales_pg, archive_root=tmp_path / "scores") == third
    assert archive_files(tmp_path) == files
    assert versions(sales_pg) == before


def test_score_noop_expired_lease_preserves_heads_and_archive(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_scoring import publisher

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: candidate())
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    expected, files = heads(sales_pg), archive_files(tmp_path)
    build = publisher.build_bundle

    def expired(repo):
        result = build(repo)
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "UPDATE task_runs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE job_code='score_product_board' AND status='running'"
                )
            )
        return result

    monkeypatch.setattr(publisher, "build_bundle", expired)
    with pytest.raises(ValueError, match="SCORING_LEASE_EXPIRED"):
        publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert heads(sales_pg) == expected
    assert archive_files(tmp_path) == files


def test_profile_noop_rechecks_final_source_baseline(sales_pg, tmp_path, monkeypatch):
    store = ProductBoardStore(sales_pg, tmp_path)
    older = store.run(lambda hb: candidate())
    latest = store.run(lambda hb: candidate(amount="45.67"))
    files = archive_files(tmp_path)
    unchanged = store._unchanged

    def moved_pointer(c, rows, end, reused):
        result = unchanged(c, rows, end, reused)
        c.execute(
            sa.text(
                "UPDATE current_datasets SET version_id=:id WHERE dataset_code='product_daily'"
            ),
            {"id": older["versions"]["product_daily"]},
        )
        return result

    monkeypatch.setattr(store, "_unchanged", moved_pointer)
    with pytest.raises(ValueError, match="SOURCE_INPUT_CHANGED"):
        store.rebuild_profiles()
    assert heads(sales_pg) == latest["versions"]
    assert archive_files(tmp_path) == files


@pytest.mark.parametrize("lost", ["expired", "reassigned"])
def test_score_archive_verification_lost_lease_cannot_succeed(
    sales_pg, tmp_path, monkeypatch, lost
):
    from app.modules.product_scoring import publisher

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: candidate())
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    expected, files = heads(sales_pg), archive_files(tmp_path)
    verify = publisher._reusable_evidence

    def expired_during_verification(c, tables, bundle):
        evidence = verify(c, tables, bundle)
        change = (
            "lease_expires_at=clock_timestamp()-interval '1 second'"
            if lost == "expired"
            else "worker_ref='replacement-worker'"
        )
        c.execute(
            sa.text(
                f"UPDATE task_runs SET {change} "
                "WHERE job_code='score_product_board' AND status='running'"
            )
        )
        return evidence

    monkeypatch.setattr(publisher, "_reusable_evidence", expired_during_verification)
    with pytest.raises(ValueError, match="SCORING_LEASE_EXPIRED"):
        publish_scores(sales_pg, archive_root=tmp_path / "scores")
    assert heads(sales_pg) == expected
    assert archive_files(tmp_path) == files
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text(
                    "SELECT count(*) FROM task_runs WHERE job_code='score_product_board' "
                    "AND status='failed'"
                )
            )
            == 1
        )
