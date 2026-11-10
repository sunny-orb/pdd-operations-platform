"""数据库故障分类使用实际异常类型；响应及诊断日志都不能包含SQL/凭据。"""

import logging
import re

import psycopg
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.product_board.repository import BoardError
from app.modules.sales.contracts import SalesError

pytest_plugins = ["tests.sales_pg_support"]

SECRET = "private-password-secret-query-parameter"


@pytest.mark.parametrize("wrapper", [BoardError, SalesError])
@pytest.mark.parametrize(
    "cause,status,code,stage",
    [
        (sa.exc.TimeoutError(SECRET), 503, "DATABASE_BUSY", "pool_checkout"),
        (
            sa.exc.OperationalError(
                "SELECT " + SECRET, {"secret": SECRET}, psycopg.errors.QueryCanceled(SECRET)
            ),
            503,
            "QUERY_TIMEOUT",
            "sql_execute",
        ),
        (
            sa.exc.OperationalError(
                "SELECT " + SECRET, {"secret": SECRET}, psycopg.errors.ConnectionFailure(SECRET)
            ),
            503,
            "DATABASE_UNAVAILABLE",
            "connection",
        ),
        (
            sa.exc.OperationalError(
                "SELECT " + SECRET, {"secret": SECRET}, psycopg.OperationalError(SECRET)
            ),
            503,
            "DATABASE_UNAVAILABLE",
            "connection",
        ),
        (
            sa.exc.OperationalError(
                "SELECT " + SECRET, {"secret": SECRET}, psycopg.errors.TooManyConnections(SECRET)
            ),
            503,
            "DATABASE_UNAVAILABLE",
            "connection",
        ),
        (psycopg.errors.TooManyConnections(SECRET), 503, "DATABASE_UNAVAILABLE", "connection"),
        (
            sa.exc.ProgrammingError(
                "SELECT " + SECRET, {"secret": SECRET}, psycopg.errors.UndefinedColumn(SECRET)
            ),
            500,
            "DATABASE_ERROR",
            "sql_execute",
        ),
        (
            sa.exc.IntegrityError(
                "SELECT " + SECRET, {"secret": SECRET}, psycopg.errors.UniqueViolation(SECRET)
            ),
            500,
            "DATABASE_ERROR",
            "sql_execute",
        ),
    ],
)
def test_wrapped_database_errors_are_classified_without_sensitive_details(
    wrapper, cause, status, code, stage, caplog
):
    app = create_app(Settings(_env_file=None, database_url=""))

    @app.get("/diagnostic-probe/{object_id}")
    def probe(object_id: str):
        raise wrapper("DATABASE_UNAVAILABLE", 503) from cause

    with TestClient(app) as client, caplog.at_level(logging.WARNING, logger="app.main"):
        response = client.get(f"/diagnostic-probe/{SECRET}", params={"token": SECRET})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert SECRET not in response.text
    logs = [r for r in caplog.records if r.name == "app.main" and r.levelno >= logging.WARNING]
    assert len(logs) == 1
    message = logs[0].getMessage()
    assert SECRET not in message
    assert "/diagnostic-probe/{object_id}" in message
    assert f"stage={stage}" in message
    assert "request_elapsed_ms=" in message
    assert response.json()["error"]["request_id"] in message
    assert logs[0].exc_info is None


@pytest.mark.parametrize("wrapper", [BoardError, SalesError])
@pytest.mark.parametrize("cause", [None, sa.exc.TimeoutError(SECRET)])
def test_business_error_codes_remain_unchanged(wrapper, cause):
    app = create_app(Settings(_env_file=None, database_url=""))

    @app.get("/business-probe")
    def probe():
        raise wrapper("DATA_VERSION_CHANGED", 409) from cause

    with TestClient(app) as client:
        response = client.get("/business-probe")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DATA_VERSION_CHANGED"


@pytest.mark.parametrize(
    "error",
    [
        sa.exc.ProgrammingError("SELECT " + SECRET, {}, psycopg.errors.UndefinedColumn(SECRET)),
        psycopg.errors.UndefinedColumn(SECRET),
    ],
)
def test_raw_database_failure_is_handled_without_rethrowing_to_server_logs(error, caplog):
    app = create_app(Settings(_env_file=None, database_url=""))

    @app.get("/raw-database-probe")
    def probe():
        raise error

    with TestClient(app) as client, caplog.at_level(logging.WARNING, logger="app.main"):
        response = client.get("/raw-database-probe")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert SECRET not in response.text
    assert all(
        SECRET not in record.getMessage() and record.exc_info is None
        for record in caplog.records
        if record.name == "app.main"
    )


def test_real_postgres_pool_exhaustion_is_busy_and_recovers(sales_pg, caplog):
    engine = sa.create_engine(
        sales_pg.url, pool_size=1, max_overflow=0, pool_timeout=0.05, hide_parameters=True
    )
    app = create_app(Settings(_env_file=None, database_url=""))
    try:
        with TestClient(app) as client:
            app.state.engine = engine
            with engine.connect(), caplog.at_level(logging.WARNING, logger="app.main"):
                for path in ("products/meta", "sales/filters", "published-versions"):
                    response = client.get("/api/v1/" + path)
                    assert response.status_code == 503
                    assert response.json()["error"]["code"] == "DATABASE_BUSY"
            recovered = client.get("/api/v1/published-versions")
            assert recovered.status_code == 200
    finally:
        engine.dispose()


def test_real_postgres_statement_timeout_is_not_a_connection_failure(sales_pg, caplog):
    app = create_app(Settings(_env_file=None, database_url=""))

    @app.get("/query-timeout-probe")
    def probe():
        try:
            with sales_pg.begin() as c:
                c.execute(sa.text("SET LOCAL statement_timeout='10ms'"))
                c.execute(sa.text("SELECT pg_sleep(0.1)"))
        except sa.exc.SQLAlchemyError as exc:
            raise BoardError("DATABASE_UNAVAILABLE", 503) from exc

    with TestClient(app) as client, caplog.at_level(logging.WARNING, logger="app.main"):
        app.state.engine = sales_pg
        response = client.get("/query-timeout-probe")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUERY_TIMEOUT"
    logs = [r.getMessage() for r in caplog.records if r.name == "app.main"]
    timings = [re.search(r"sql_elapsed_ms=([0-9.]+)", message) for message in logs]
    assert any(match and float(match[1]) >= 5 for match in timings)
