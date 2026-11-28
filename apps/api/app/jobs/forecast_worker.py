"""在已有同步容器内提供有界的文档读取和正式预测核验。"""

from secrets import compare_digest
from threading import Thread
from typing import Annotated, Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.connectors.forecast_sop import (
    FeishuForecastSopReader,
    FeishuSopReader,
    ForecastSourceError,
)
from app.jobs.forecast_sop import REQUEST_MARKER, run_check
from app.jobs.sync_shop_daily import runtime_engine
from app.modules.knowledge.forecast_access import company_forecast_database
from app.modules.knowledge.public_sops import registered_entry
from app.modules.knowledge.sop_transport import MAX_RESPONSE, PATH, PORT, worker_token


class SopOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["revision", "markdown", "forecast"]
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,47}$")
    revision: int | None = Field(default=None, gt=0, le=2**63 - 1, strict=True)


def worker_app(token: str) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.post(PATH)
    def operation(body: SopOperation, authorization: Annotated[str, Header()] = ""):
        if not token or not compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(403)
        try:
            if body.action == "forecast":
                if body.key is not None or body.revision is not None:
                    raise HTTPException(422)
                engine = runtime_engine()
                try:
                    if not company_forecast_database(engine):
                        raise ForecastSourceError("temporary")
                    return run_check(
                        engine, FeishuForecastSopReader(), request_marker=REQUEST_MARKER
                    )
                finally:
                    engine.dispose()
            entry = registered_entry(body.key)
            if entry is None:
                raise HTTPException(404, "not_found")
            reader = FeishuSopReader(entry.document_id)
            if body.action == "revision" and body.revision is None:
                return {"revision": reader.revision()}
            if body.action != "markdown" or body.revision is None:
                raise HTTPException(422)
            content = reader.markdown(body.revision)
            if len(content.encode()) > MAX_RESPONSE // 6 - 256:
                raise ForecastSourceError("temporary")
            return {"content": content}
        except ForecastSourceError as exc:
            raise HTTPException(503, exc.reason) from None
        except HTTPException:
            raise
        except Exception:  # 不把数据库错误、文档及凭据写入运行日志。
            raise HTTPException(503, "temporary") from None

    return app


def start_worker_api() -> uvicorn.Server | None:
    token = worker_token()
    if not token:
        return None
    server = uvicorn.Server(
        uvicorn.Config(
            worker_app(token),
            host="0.0.0.0",
            port=PORT,
            access_log=False,
            limit_concurrency=6,
            timeout_keep_alive=2,
            timeout_graceful_shutdown=10,
        )
    )
    Thread(target=server.run, daemon=True, name="forecast-worker").start()
    return server
