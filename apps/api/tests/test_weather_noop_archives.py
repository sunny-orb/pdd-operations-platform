"""真实PG验证天气无变化不增档，真实修订与失败状态不丢失。"""

from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.jobs.sync_weather import sync_weather
from app.modules.weather.storage import LOCK_KEY
from tests.test_weather import sources
from tests.test_weather_pg import client_for

pytest_plugins = ["tests.sales_pg_support"]


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def test_unchanged_reuses_verified_legacy_archive_and_refreshes_success(sales_pg, tmp_path):
    first = sync_weather(sales_pg, sources, tmp_path)
    before = snapshot(tmp_path)
    with client_for(sales_pg) as client:
        token = client.get("/api/v1/weather/meta").json()["data"]["data_version"]
        for _ in range(3):
            result = sync_weather(sales_pg, sources, tmp_path)
            assert result["reused"] and result["publication_id"] == first["publication_id"]
            assert snapshot(tmp_path) == before
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert meta["data_version"] == token
        assert meta["last_attempt_status"] == "succeeded"
    with sales_pg.connect() as c:
        rows = (
            c.execute(
                sa.text(
                    "SELECT evidence_ref, result_version_id, finished_at FROM public.task_runs "
                    "WHERE job_code='sync_weather' ORDER BY queued_at"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 4
        assert len({row["evidence_ref"] for row in rows}) == 1
        assert all(Path(row["evidence_ref"]).is_file() for row in rows)
        assert rows[-1]["finished_at"] > rows[0]["finished_at"]
        assert c.scalar(sa.text("SELECT count(*) FROM public.weather_publications")) == 1


def test_busy_lock_does_not_create_empty_archive(sales_pg, tmp_path):
    with sales_pg.connect() as c:
        c.execute(sa.text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": LOCK_KEY})
        try:
            with pytest.raises(ValueError, match="SYNC_ALREADY_RUNNING"):
                sync_weather(sales_pg, sources, tmp_path)
            assert snapshot(tmp_path) == {}
        finally:
            c.execute(sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": LOCK_KEY})


def test_receive_failure_has_status_without_duplicate_archive(sales_pg, tmp_path):
    first = sync_weather(sales_pg, sources, tmp_path)
    before = snapshot(tmp_path)

    def fail():
        raise RuntimeError("private details")

    with pytest.raises(ValueError, match="SOURCE_RECEIVE_FAILED"):
        sync_weather(sales_pg, fail, tmp_path)
    assert snapshot(tmp_path) == before
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert meta["default_publication_id"] == first["publication_id"]
        assert meta["last_attempt_status"] == "failed"
    assert sync_weather(sales_pg, sources, tmp_path)["reused"]
    assert snapshot(tmp_path) == before


def test_archive_write_failure_records_failure_and_keeps_published_data(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.weather import storage

    first = sync_weather(sales_pg, sources, tmp_path)
    before = snapshot(tmp_path)

    def fail_write(*_args):
        raise OSError("private disk details")

    monkeypatch.setattr(storage, "_write", fail_write)
    with pytest.raises(ValueError, match="WEATHER_PUBLICATION_FAILED"):
        sync_weather(sales_pg, lambda: sources(day="29"), tmp_path)
    after = snapshot(tmp_path)
    assert all(after[path] == content for path, content in before.items())
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert meta["default_publication_id"] == first["publication_id"]
        assert meta["last_attempt_status"] == "failed"
    with sales_pg.connect() as c:
        task = (
            c.execute(
                sa.text(
                    "SELECT evidence_ref, error_summary FROM public.task_runs "
                    "WHERE job_code='sync_weather' ORDER BY queued_at DESC LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
        assert task["evidence_ref"] is None and "private" not in task["error_summary"]


@pytest.mark.parametrize(
    "file,damage",
    [
        ("document.json", "missing"),
        ("document.json", "corrupt"),
        ("manifest.json", "missing"),
        ("manifest.json", "corrupt"),
    ],
)
def test_missing_or_corrupt_archive_cannot_be_reused(sales_pg, tmp_path, file, damage):
    first = sync_weather(sales_pg, sources, tmp_path)
    newer = sync_weather(sales_pg, lambda: sources(day="29"), tmp_path)
    with sales_pg.connect() as c:
        reference = c.scalar(
            sa.text("SELECT retained_input_ref FROM public.dataset_versions WHERE id=:id"),
            {"id": first["publication_id"]},
        )
    path = Path(reference).parent / file
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{}")
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="WEATHER_PUBLICATION_FAILED"):
        sync_weather(sales_pg, sources, tmp_path)
    assert snapshot(tmp_path) == before
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert meta["default_publication_id"] == newer["publication_id"]
        assert meta["last_attempt_status"] == "failed"


@pytest.mark.parametrize("change", ["correction", "issue_time", "medium", "next_day"])
def test_real_weather_updates_publish_and_keep_history(sales_pg, tmp_path, change):
    first = sync_weather(sales_pg, sources, tmp_path)
    before = snapshot(tmp_path)
    values = sources()
    if change == "correction":
        values = sources(extra="\n补充：浙江局地暴雨。")
    elif change == "issue_time":
        values[0] = replace(values[0], issue_time="2026-10-28T11:00:00+08:00")
    elif change == "medium":
        values[1] = replace(values[1], text=values[1].text + "\n降水修订。")
    else:
        values = sources(day="29")
    new = sync_weather(sales_pg, lambda: values, tmp_path)
    assert not new["reused"] and new["publication_id"] != first["publication_id"]
    after = snapshot(tmp_path)
    assert all(after[path] == content for path, content in before.items())
    assert sync_weather(sales_pg, lambda: values, tmp_path)["reused"]
    assert snapshot(tmp_path) == after
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert len(meta["publications"]) == (2 if change == "next_day" else 1)
        old = client.get(
            "/api/v1/weather/publication",
            params={
                "publication_id": first["publication_id"],
                "data_version": meta["data_version"],
            },
        )
        assert old.status_code == 200 and old.json()["data"]["revision_no"] == 1
