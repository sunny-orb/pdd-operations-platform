"""问答按需核对正式预测修订；API 只用查询身份读取与本次核验回执一致的版本。"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.connectors.forecast_sop import DOCUMENT_ID

from .forecast_store import read_forecast_sop, withheld_forecast_sop
from .interpretation import effective_forecast_sop

_API_DIR = Path(__file__).resolve().parents[3]
_USAGE_KEYS = ("requests", "input_tokens", "output_tokens")


def local_forecast_database(engine, role):
    """两端分别固定本机项目库及各自角色；公司部署和其他项目不能驱动本机同步。"""
    return (
        os.environ.get("APP_ENV") != "production"
        and engine is not None
        and engine.url.drivername == "postgresql+psycopg"
        and not engine.url.query
        and engine.url.host == "127.0.0.1"
        and engine.url.port == 55432
        and engine.url.database == "pdd_operations"
        and engine.url.username == role
    )


def _request_refresh():
    # 唯一参数为固定问答模式；同步身份和来源只在子进程内解析。
    process = subprocess.run(
        [sys.executable, "-m", "app.jobs.forecast_sop", "--for-question"],
        cwd=_API_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=80,
        check=True,
    )
    return json.loads(process.stdout)


def ensure_forecast_current(engine, *, refresh=None) -> dict:
    """取本题核过的修订和本次解读用量。在调用者打开经营 RR 事务之前通过独立连接读取。"""
    if refresh is None and not local_forecast_database(engine, "pdd_app"):
        return {
            **withheld_forecast_sop(state="unavailable", reason="local_only"),
            "review_usage": dict.fromkeys(_USAGE_KEYS, 0),
        }
    hidden = withheld_forecast_sop(reason="temporary")
    usage = dict.fromkeys(_USAGE_KEYS)
    try:
        receipt = (refresh or _request_refresh)()
        recorded = {key: receipt["review_usage"][key] for key in _USAGE_KEYS}
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in recorded.values()
        ):
            raise ValueError("FORECAST_USAGE_RECEIPT")
        usage = recorded
        revision = receipt["revision"]
        doc = receipt.get("document_id") or DOCUMENT_ID
        state = receipt["status"]
        hidden = withheld_forecast_sop(
            doc=doc,
            revision=revision,
            state=state if state in {"unavailable", "expired"} else "pending",
            reason=receipt.get("reason") or "revision_changed",
        )
        if state in ("active", "unsupported"):
            verified_at = datetime.fromisoformat(receipt["checked_at"])
            with engine.connect().execution_options(isolation_level="READ COMMITTED") as c:
                source = read_forecast_sop(c)
                if (
                    source["document_id"] == doc
                    and source["revision"] == revision
                    and source["state"] in ("active", "unsupported")
                    and source["checked_at"] == source["verified_at"]
                    and source["verified_at"] is not None
                    and source["verified_at"] >= verified_at
                ):
                    return {**effective_forecast_sop(c, source=source), "review_usage": usage}
                if source["state"] in ("unavailable", "expired"):
                    hidden = withheld_forecast_sop(
                        doc=doc, state=source["state"], reason=source["reason"]
                    )
    except (OSError, subprocess.SubprocessError, SQLAlchemyError, ValueError, TypeError, KeyError):
        # 子进程中断、无回执或视图无法读取时均不能借用此前的成功公式。
        pass
    return {**hidden, "review_usage": usage}
