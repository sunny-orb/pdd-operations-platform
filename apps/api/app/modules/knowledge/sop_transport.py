"""问答只通过固定内网地址请求同步容器的已登记 SOP。"""

import json
import os
import re
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from app.connectors.forecast_sop import ForecastSourceError

PORT = 8710
PATH = "/internal/sop"
MAX_RESPONSE = 1_048_576


def worker_token() -> str:
    value = os.environ.get("PDD_SOP_WORKER_TOKEN", "")
    return value if re.fullmatch(r"[a-f0-9]{64}", value) else ""


def request_worker(action: str, *, key: str | None = None, revision: int | None = None) -> dict:
    token = worker_token()
    if not token:
        raise ForecastSourceError("temporary")
    request = Request(
        f"http://worker:{PORT}{PATH}",
        data=json.dumps(dict(action=action, key=key, revision=revision)).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({})).open(
            request, timeout=75 if action == "forecast" else 22
        ) as response:
            payload = json.loads(response.read(MAX_RESPONSE))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096)).get("detail")
        except (OSError, ValueError, AttributeError):
            detail = None
        raise ForecastSourceError(detail if isinstance(detail, str) else "temporary") from None
    except (OSError, ValueError):
        raise ForecastSourceError("temporary") from None
    if not isinstance(payload, dict):
        raise ForecastSourceError("temporary")
    return payload


class WorkerSopReader:
    def __init__(self, key: str):
        self.key = key

    def revision(self) -> int:
        value = request_worker("revision", key=self.key).get("revision")
        if type(value) is not int or not 0 < value <= 2**63 - 1:
            raise ForecastSourceError("temporary")
        return value

    def markdown(self, revision: int) -> str:
        value = request_worker("markdown", key=self.key, revision=revision).get("content")
        if not isinstance(value, str):
            raise ForecastSourceError("temporary")
        return value
