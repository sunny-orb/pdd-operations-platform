from fastapi.testclient import TestClient

from app.jobs import forecast_worker
from app.modules.knowledge import public_sops
from app.modules.knowledge.sop_transport import PATH, worker_token


def test_production_sop_only_exposes_registered_reads_to_authenticated_api(monkeypatch):
    token = "a" * 64
    headers = {"Authorization": f"Bearer {token}"}
    calls = []

    class Reader:
        def __init__(self, document_id):
            calls.append(document_id)

        def revision(self):
            return 4

        def markdown(self, revision):
            return f"正文，第 {revision} 版"

    monkeypatch.setattr(forecast_worker, "FeishuSopReader", Reader)
    with TestClient(forecast_worker.worker_app(token)) as client:
        query = {"action": "revision", "key": "monthly_gmv"}
        assert client.post(PATH, json=query).status_code == 403
        assert (
            client.post(PATH, headers=headers, json={**query, "key": "arbitrary_doc"}).status_code
            == 404
        )
        assert not calls
        assert client.post(PATH, headers=headers, json=query).json() == {"revision": 4}
        query = {"action": "markdown", "key": "monthly_gmv", "revision": 4}
        assert client.post(PATH, headers=headers, json=query).json() == {"content": "正文，第 4 版"}
        assert (
            client.post(PATH, headers=headers, json={**query, "doc_url": "external"}).status_code
            == 422
        )
    assert calls == [public_sops.registered_entry("monthly_gmv").document_id] * 2


def test_production_catalog_and_worker_fail_closed_without_valid_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PDD_SOP_WORKER_TOKEN", raising=False)
    assert not worker_token() and public_sops.catalog_entries() == []
    monkeypatch.setenv("PDD_SOP_WORKER_TOKEN", "invalid")
    assert not worker_token() and public_sops.catalog_entries() == []
    monkeypatch.setenv("PDD_SOP_WORKER_TOKEN", "0" * 64)
    assert worker_token() and public_sops.catalog_entries()[0]["key"] == "monthly_gmv"
