"""单篇预测 SOP 的本机按需核验和已核数据的本地日快照。"""

import argparse
import json
import os
from datetime import datetime, time
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError

from app.jobs.sync_shop_daily import ROOT, runtime_engine
from app.modules.knowledge.forecast_access import local_forecast_database
from app.modules.knowledge.forecast_store import read_forecast_sop, sync_forecast_sop
from app.modules.knowledge.monthly_forecast import record_month, snapshot_months
from app.modules.sales.forecast_inputs import read_forecast_publications

REQUEST_MARKER = ROOT / "var/project-sync/forecast-sop-request.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _read_marker(path):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _write_marker(path, record):
    """本机问答与定时器共享少量版本/时刻；读者只看到完整旧回执或完整新回执。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as pending:
        temporary = Path(pending.name)
        try:
            json.dump(record, pending)
            pending.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def run_check(engine, reader, *, compiler=None, request_marker=None):
    """独立任务读取指定来源并用同步身份落版。回执只含本题核验状态和模型用量。"""
    usage = dict(requests=0, input_tokens=0, output_tokens=0)
    marker_error = False

    def review(content):
        from app.modules.knowledge.forecast_model import compile_with_deepseek

        return compile_with_deepseek(content, usage=usage)

    def record_request(current):
        nonlocal marker_error
        if current["rule"].get("interpretation"):
            try:
                _write_marker(
                    request_marker,
                    {
                        "document_id": current["document_id"],
                        "revision": current["revision"],
                        "verified_at": current["verified_at"].isoformat(),
                    },
                )
            except OSError:
                marker_error = True

    source = sync_forecast_sop(
        engine,
        reader,
        compiler=compiler or review,
        on_verified=record_request if request_marker is not None else None,
    )
    state = source["state"]
    # 原始视图仍可供运维识别旧版本；本次没有证实的正文及公式不可交给问答。
    if state == "stale" or (
        state == "unsupported" and source["verified_at"] != source["checked_at"]
    ):
        state = "pending"
    receipt = {
        "status": state,
        "document_id": source["document_id"],
        "revision": source["revision"],
        "checked_at": source["checked_at"].isoformat() if source["checked_at"] else None,
        "reason": source["reason"],
        "review_usage": usage,
    }
    if marker_error:
        receipt["snapshot_request"] = "unavailable"
    return receipt


def save_requested_snapshots(engine, request_marker, *, now=None):
    """10:30 起仅用今日提问核过的同版规则。没有提问回执时不查数据库或来源。"""
    now = now or datetime.now(SHANGHAI)
    idle = {"status": "not_due", "saved": 0}
    requested = _read_marker(request_marker)
    try:
        verified_at = datetime.fromisoformat(requested["verified_at"])
    except (ValueError, TypeError, KeyError):
        return idle
    if (
        verified_at.tzinfo is None
        or verified_at.astimezone(SHANGHAI).date() != now.date()
        or verified_at > now
        or now.time() < time(10, 30)
    ):
        return idle
    checkpoint = request_marker.with_suffix(".snapshot.json")
    previous = _read_marker(checkpoint)
    for attempt in range(2):
        try:
            with (
                engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
                c.begin(),
            ):
                publications = read_forecast_publications(c)
                inputs = {
                    code: [str(row["id"]), row["status"], row["error_code"]]
                    for code, row in publications.items()
                }
                key = dict(
                    day=now.date().isoformat(), revision=requested.get("revision"), inputs=inputs
                )
                if key == previous:
                    return idle
                source = read_forecast_sop(c)
                if (
                    source["state"] != "active"
                    or source["document_id"] != requested.get("document_id")
                    or source["revision"] != requested.get("revision")
                    or source["verified_at"] != source["checked_at"]
                    or source["verified_at"] < verified_at
                    or not source["rule"].get("interpretation")
                ):
                    return idle
                # 快照记录问答实际核验的时刻，即使后来有一次人工运维复核。
                source = dict(source, verified_at=verified_at)
                saved = sum(
                    record_month(c, source, month, now.date())
                    for month in snapshot_months(now.date())
                )
            _write_marker(checkpoint, key)
            return {"status": "checked", "saved": saved}
        except DBAPIError as exc:
            if attempt or getattr(exc.orig, "sqlstate", None) != "40001":
                raise


def run_local(engine, *, for_question=False):
    from app.connectors.forecast_sop import FeishuForecastSopReader

    if not local_forecast_database(engine, "pdd_ingest"):
        raise ValueError("FORECAST_LOCAL_ONLY")
    return run_check(
        engine,
        FeishuForecastSopReader(identity="user"),
        request_marker=REQUEST_MARKER if for_question else None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--for-question", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--enable-local", action="store_true", help="兼容旧命令，执行一次手动核验")
    args = parser.parse_args()
    if os.environ.get("APP_ENV") == "production":
        return 1
    engine = None
    try:
        engine = runtime_engine()
        result = run_local(engine, for_question=args.for_question)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if args.for_question or result["status"] == "active" else 1
    except Exception:  # CLI 边界不能回显私密连接配置、CLI 响应或原文。
        print(json.dumps({"status": "failed", "reason": "forecast_check_failed"}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
