"""读取当前修订的已核结果。21/22 接收的旧格式按既有本机算法兼容。"""

from functools import lru_cache

from .forecast_rules import ForecastRuleError, compile_forecast_rule
from .forecast_store import read_forecast_sop


@lru_cache(maxsize=4)
def _current_rule(content):
    try:
        return compile_forecast_rule(content)
    except ForecastRuleError:
        return None


def effective_forecast_sop(connection, *, source=None):
    if source is None:
        source = read_forecast_sop(connection)
    if source["state"] not in ("active", "stale", "unsupported"):
        return source
    if source.get("reason") == "interpretation_rejected" or (
        source["rule"] and "interpretation" in source["rule"]
    ):
        return source
    # 仅旧格式需要复核。权限、修订和时效始终以每次读取的数据库视图为准。
    rule = _current_rule(source["content"])
    if rule is None:
        return dict(source, rule=None, state="unsupported", reason="unsupported_rule")
    if source["state"] == "unsupported":
        # 原文仍有效；程序升级可能已支持该算法。保留首次提取，按当时解释留运行快照。
        fresh = source["checked_at"] == source["verified_at"]
        return dict(
            source,
            rule=rule,
            state="active" if fresh else "stale",
            reason=None if fresh else "temporary",
            reinterpreted=True,
        )
    return dict(source, rule=rule)
