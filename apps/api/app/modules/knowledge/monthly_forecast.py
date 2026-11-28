"""把同一数据库快照内的生效规则、六店实绩和上次真实运行交给纯计算函数。"""

import hashlib
import json
from datetime import date, timedelta

from app.modules.product_board.repository import wire


def _input_version(month, source, inputs):
    """只给本次实际使用的数值与规则指纹，其他月份补数或一次读操作不产生新预测。"""
    value = dict(
        month=month,
        document=source["document_id"],
        revision=source["revision"],
        formula={k: v for k, v in source["rule"].items() if k != "evidence"},
        cutoff=inputs["cutoff"],
        daily=sorted(inputs["daily"].items()),
        target=inputs["target"],
        promotion=sorted((inputs.get("promotion") or {}).items()),
        comparable_start=inputs.get("comparable_start"),
    )
    normalized = json.dumps(wire(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()


def evaluate(connection, source, month: date, as_of: date):
    from app.modules.sales.forecast_inputs import read_forecast_inputs

    from .forecast_calculation import calculate_forecast
    from .forecast_store import read_previous_forecast

    inputs = read_forecast_inputs(connection, month=month, as_of=as_of)
    version = _input_version(month, source, inputs)
    prior = read_previous_forecast(connection, month, version, cutoff=inputs["cutoff"])
    if prior and (not inputs["cutoff"] or prior["cutoff"] > inputs["cutoff"].isoformat()):
        prior = None
    result = calculate_forecast(
        source["rule"],
        month=month,
        cutoff=inputs["cutoff"],
        daily=inputs["daily"],
        target=inputs["target"],
        promotion=inputs.get("promotion"),
        comparable_start=inputs.get("comparable_start"),
        previous=prior,
    )
    return result, inputs, version


def record_month(connection, source, month: date, today: date):
    """独立任务按月留存真实结果。按指纹幂等；读接口从不写快照。"""
    from .forecast_store import save_forecast_snapshot

    if source["state"] != "active":
        return False
    result, inputs, version = evaluate(connection, source, month, today)
    if result["center"] is None or not result["cutoff"]:
        return False
    payload = wire(
        {
            **{
                key: result[key]
                for key in (
                    "month",
                    "cutoff",
                    "actual",
                    "target",
                    "center_daily",
                    "center",
                    "remaining_days",
                    "complete_days",
                    "windows",
                    "scenarios",
                    "status",
                )
            },
            "rule_revision": source["revision"],
            "sop_verified_at": source["verified_at"].isoformat(),
            "formula": {k: v for k, v in source["rule"].items() if k != "evidence"},
            "source_versions": inputs["versions"],
            "daily": sorted(inputs["daily"].items()),
            "coverage": inputs["coverage"],
        }
    )
    return save_forecast_snapshot(
        connection, month, version, payload, reinterpreted=source.get("reinterpreted", False)
    )


def snapshot_months(today):
    current = today.replace(day=1)
    return [(current - timedelta(days=1)).replace(day=1), current]
