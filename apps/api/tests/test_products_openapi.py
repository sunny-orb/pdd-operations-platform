"""M1接口文档必须描述实际成功和错误结构，不仅列出路由。"""

from fastapi.testclient import TestClient
from test_m1_ingestion import pg, repo  # noqa: F401
from test_product_scores_api import scored_db  # noqa: F401
from test_products_api import client_db  # noqa: F401

from app.core.config import Settings
from app.main import create_app


def test_products_openapi_documents_typed_responses_and_common_errors():
    with TestClient(create_app(Settings(database_url="", app_env="test"))) as client:
        spec = client.get("/openapi.json").json()
        for path, entry in spec["paths"].items():
            if not path.startswith("/api/v1/products/"):
                continue
            get = entry["get"]
            schema = get["responses"]["200"]["content"]["application/json"]["schema"]
            assert "$ref" in schema, path
            for status in ("409", "422", "503"):
                error = get["responses"][status]["content"]["application/json"]["schema"]
                assert error["$ref"].endswith("/ErrorEnvelope")
        models = spec["components"]["schemas"]
        assert models["OperatingMetric"]["properties"]["value"]["anyOf"][0]["type"] == "string"
        assert "signals" in models["ScoreDetail"]["properties"]
        assert "score_version" in models["PublicationMeta"]["properties"]


def test_documented_models_validate_real_postgresql_operating_responses(client_db):  # noqa: F811
    from app.api.product_schemas import RESPONSE_MODELS

    client, _, _, _ = client_db
    for name in ("filters", "overview", "trend", "days", "brief", "alerts", "diagnosis"):
        response = client.get(
            "/api/v1/products/" + name, params={"date_from": "2026-10-21", "date_to": "2026-10-22"}
        )
        assert response.status_code == 200
        RESPONSE_MODELS[name].model_validate(response.json())


def test_documented_models_validate_real_postgresql_score_responses(scored_db):  # noqa: F811
    from app.api.product_schemas import RESPONSE_MODELS

    client, scores, _, _, _, _ = scored_db
    # 未生成状态与已生成状态都必须符合文档，而非只检查健康响应。
    RESPONSE_MODELS["scores"].model_validate(client.get("/api/v1/products/scores").json())
    scores.run()
    listing = client.get("/api/v1/products/scores").json()
    RESPONSE_MODELS["scores"].model_validate(listing)
    RESPONSE_MODELS["filters"].model_validate(
        client.get("/api/v1/products/filters?scope=scores").json()
    )
    identity = listing["data"]["items"][0]["score_id"]
    RESPONSE_MODELS["score_detail"].model_validate(
        client.get("/api/v1/products/scores/" + identity).json()
    )
