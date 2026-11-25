"""真实隔离PostgreSQL：不可变发布、最小权限、切日与失败保旧。"""

from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from tests.test_weather import sources

pytest_plugins = ["tests.sales_pg_support"]


@contextmanager
def client_for(engine):
    from app.main import create_app

    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app) as client:
        app.state.engine = engine
        yield client


def test_permissions_and_immutability(sales_pg, tmp_path):
    from app.jobs.sync_weather import sync_weather

    first = sync_weather(sales_pg, sources, tmp_path)
    with sales_pg.connect() as c:
        assert c.scalar(
            sa.text("SELECT has_table_privilege('pdd_app','public.weather_publications','SELECT')")
        )
        for role in ("pdd_app", "pdd_ingest"):
            assert not c.scalar(
                sa.text("SELECT has_table_privilege(:r,'public.weather_publications','UPDATE')"),
                {"r": role},
            )
            assert not c.scalar(
                sa.text("SELECT has_table_privilege(:r,'public.weather_publications','DELETE')"),
                {"r": role},
            )
    with pytest.raises(DBAPIError), sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE public.weather_publications SET revision_no=2 WHERE id=:id"),
            {"id": first["publication_id"]},
        )


def test_repeat_revision_failure_and_historical_api(sales_pg, tmp_path):
    from app.jobs.sync_weather import sync_weather

    def receive():
        with sales_pg.connect() as c:
            assert (
                c.scalar(
                    sa.text(
                        "SELECT count(*) FROM public.task_runs "
                        "WHERE job_code='sync_weather' AND status='running'"
                    )
                )
                == 1
            )
        return sources()

    first = sync_weather(sales_pg, receive, tmp_path)
    repeated = sync_weather(sales_pg, receive, tmp_path)
    assert repeated["publication_id"] == first["publication_id"]
    assert repeated["reused"] is True
    revised = sync_weather(sales_pg, lambda: sources(extra="\n补充：浙江局地暴雨。"), tmp_path)
    assert revised["publication_id"] != first["publication_id"]
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert len(meta["publications"]) == 1
        assert meta["publications"][0]["revision_no"] == 2
        token = meta["data_version"]
        old = client.get(
            "/api/v1/weather/publication",
            params={"publication_id": first["publication_id"], "data_version": token},
        )
        assert old.status_code == 200
        assert old.json()["data"]["revision_no"] == 1
        assert client.get("/api/v1/weather/publication?publication_id=missing").status_code == 422
        assert client.get("/api/v1/weather/publication?data_version=old").status_code == 409

    def fail():
        raise RuntimeError("private raw body must never become error_summary")

    with pytest.raises(ValueError, match="SOURCE_RECEIVE_FAILED"):
        sync_weather(sales_pg, fail, tmp_path)
    with client_for(sales_pg) as client:
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert meta["default_publication_id"] == revised["publication_id"]
        assert meta["data_version"] == token
        assert meta["last_attempt_status"] == "failed"
        assert meta["last_success_at"] is not None
        assert meta["error_code"] == "SOURCE_RECEIVE_FAILED"
    with sales_pg.connect() as c:
        rows = (
            c.execute(
                sa.text(
                    "SELECT * FROM public.task_runs WHERE job_code='sync_weather' "
                    "ORDER BY queued_at"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 4
        assert rows[1]["finished_at"] > rows[0]["finished_at"]
        assert all(r["result_version_id"] is not None for r in rows[:3])
        assert rows[-1]["result_version_id"] is None
        assert "private" not in rows[-1]["error_summary"]


def test_empty_meta_and_switch_day_changes_vector(sales_pg, tmp_path):
    from app.jobs.sync_weather import sync_weather

    with client_for(sales_pg) as client:
        empty = client.get("/api/v1/weather/meta").json()["data"]
        assert empty["status"] == "source_unavailable"
        assert client.get("/api/v1/weather/publication").json()["data"] is None
        first = sync_weather(sales_pg, sources, tmp_path)
        next_day = sync_weather(sales_pg, lambda: sources(day="29"), tmp_path)
        meta = client.get("/api/v1/weather/meta").json()["data"]
        assert meta["default_publication_id"] == next_day["publication_id"]
        assert meta["data_version"] != empty["data_version"]
        assert len(meta["publications"]) == 2
        assert (
            client.get(
                "/api/v1/weather/publication", params={"publication_id": first["publication_id"]}
            ).json()["data"]["issue_date"]
            == "2026-10-28"
        )
        with sales_pg.connect() as c:
            assert c.scalar(sa.text("SELECT count(*) FROM public.task_runs")) == 2


def test_ingest_role_can_publish_and_app_role_only_reads(sales_pg, tmp_path):
    from sqlalchemy import event

    from app.jobs.sync_weather import sync_weather

    writer = sa.create_engine(sales_pg.url, hide_parameters=True)

    @event.listens_for(writer, "connect")
    def set_role(connection, _record):
        connection.execute("SET ROLE pdd_ingest")
        connection.commit()

    try:
        result = sync_weather(writer, sources, tmp_path)
    finally:
        writer.dispose()
    with sales_pg.begin() as c:
        c.execute(sa.text("SET LOCAL ROLE pdd_app"))
        assert c.scalar(sa.text("SELECT count(*) FROM public.weather_publications")) == 1
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT state FROM public.dataset_versions WHERE id=:id"),
                {"id": result["version_id"]},
            )
            == "published"
        )
        assert (
            c.scalar(
                sa.text(
                    "SELECT version_id FROM public.current_datasets "
                    "WHERE dataset_code='weather_publication'"
                )
            )
            == result["version_id"]
        )


def test_advisory_lock_rejects_concurrent_receive(sales_pg, tmp_path):
    from app.jobs.sync_weather import sync_weather

    with sales_pg.connect() as lock:
        lock.execute(sa.text("SELECT pg_advisory_lock(hashtext('nmc-weather-publication'))"))
        lock.commit()
        try:
            with pytest.raises(ValueError, match="SYNC_ALREADY_RUNNING"):
                sync_weather(sales_pg, sources, tmp_path)
            assert lock.scalar(sa.text("SELECT count(*) FROM public.task_runs")) == 0
        finally:
            lock.execute(sa.text("SELECT pg_advisory_unlock(hashtext('nmc-weather-publication'))"))
            lock.commit()


def test_weather_parent_cannot_seal_without_document(sales_pg):
    with pytest.raises(DBAPIError), sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO public.dataset_versions
          (id,dataset_code,origin_kind,state,source_system,source_locator,row_schema_revision,
           interpretation_ref,producer_revision,period_start,period_end)
          VALUES ('weather-without-child','weather_publication','source','draft','nmc_official',
                  'synthetic://official-pair','test','synthetic://evidence','test','2026-10-28','2026-11-03')
        """)
        )
        c.execute(
            sa.text("""UPDATE public.dataset_versions SET state='validated',
         content_fingerprint='synthetic:missing-child',validation_evidence_ref='synthetic:bad',
         received_at=clock_timestamp() WHERE id='weather-without-child'""")
        )


def test_expired_receive_worker_cannot_publish_or_advance_pointer(sales_pg, tmp_path):
    from app.jobs.sync_weather import sync_weather

    first = sync_weather(sales_pg, sources, tmp_path)

    def expire():
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "UPDATE public.task_runs SET lease_expires_at=clock_timestamp() "
                    "- interval '1 second' WHERE job_code='sync_weather' "
                    "AND status='running'"
                )
            )
        return sources(extra="\n到期worker不得发布补充。")

    with pytest.raises(ValueError, match="TASK_LEASE_LOST"):
        sync_weather(sales_pg, expire, tmp_path)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM public.weather_publications")) == 1
        assert (
            c.scalar(
                sa.text(
                    "SELECT version_id FROM public.current_datasets "
                    "WHERE dataset_code='weather_publication'"
                )
            )
            == first["version_id"]
        )


def test_weather_truncate_cannot_bypass_immutable_archive(sales_pg, tmp_path):
    from app.jobs.sync_weather import sync_weather

    sync_weather(sales_pg, sources, tmp_path)
    with pytest.raises(DBAPIError), sales_pg.begin() as c:
        c.execute(sa.text("TRUNCATE public.weather_publications"))


def test_archived_weather_is_projected_without_rewriting(sales_pg, tmp_path, monkeypatch):
    from app.jobs.sync_weather import sync_weather
    from app.modules.weather import storage
    from app.modules.weather.contracts import WeatherBlock
    from app.modules.weather.parser import build_document
    from tests.test_weather import compact_sources

    day, legacy = build_document(compact_sources())
    legacy.today_rain = WeatherBlock(regions=[], detail="旧版未识别", status="unidentified")
    legacy.today_temperature = WeatherBlock(regions=[], detail="旧版未识别", status="unidentified")
    legacy.next_7_days = []
    with monkeypatch.context() as patch:
        patch.setattr(storage, "build_document", lambda _sources: (day, legacy))
        result = sync_weather(sales_pg, compact_sources, tmp_path)

    with sales_pg.connect() as c:
        before = c.scalar(
            sa.text("SELECT document FROM weather_publications WHERE id=:id"),
            {"id": result["publication_id"]},
        )
    with client_for(sales_pg) as client:
        response = client.get("/api/v1/weather/publication").json()
        assert response["data"]["id"] == result["publication_id"]
        assert response["data"]["document"]["today_rain"]["status"] == "parsed"
        assert response["data"]["document"]["today_rain"]["regions"] == ["江苏", "浙江"]
        assert response["data"]["document"]["today_temperature"]["status"] == "not_mentioned"
        assert any(
            item["regions"] == ["江苏", "浙江", "河北"]
            for item in response["data"]["document"]["next_7_days"]
        )
        assert response["meta"]["status"] == "ready"
    with sales_pg.connect() as c:
        after = c.scalar(
            sa.text("SELECT document FROM weather_publications WHERE id=:id"),
            {"id": result["publication_id"]},
        )
    assert before == after
