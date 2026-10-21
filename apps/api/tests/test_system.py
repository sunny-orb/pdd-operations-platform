"""初始化状态必须真实，敏感配置不得通过状态接口泄露。"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.main import create_app


def test_missing_database_is_not_reported_ready():
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        assert client.get("/health/live").json()["status"] == "ok"
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["database"] == "not_configured"
        status = client.get("/api/v1/system/status")
        assert status.json()["data"]["business_data"] == "not_integrated"
        assert status.json()["meta"]["data_complete_through"] is None
        assert status.headers["X-Request-ID"] == status.json()["meta"]["request_id"]


def test_database_failure_is_safe_and_does_not_claim_sync_success():
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql+psycopg://user:private-test-password@localhost/demo"),
    )
    with TestClient(create_app(settings)) as client:
        with patch("app.api.system.database_available") as probe:
            probe.side_effect = OperationalError("SELECT 1", {}, Exception("internal detail"))
            response = client.get("/health/ready")
        assert response.status_code == 503
        assert "private-test-password" not in response.text
        status = client.get("/api/v1/system/status")
        assert "private-test-password" not in status.text
        assert status.json()["meta"]["last_successful_sync_at"] is None


def test_database_type_cannot_silently_change_to_sqlite():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///local.db")


def test_unknown_route_has_consistent_error_shape():
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        response = client.get("/api/v1/products/daily")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "HTTP_ERROR"
        assert response.json()["error"]["request_id"]
