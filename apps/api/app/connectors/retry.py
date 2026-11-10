"""只对已观察的数仓backend并发拒绝做有预算的串行退避，不吞掉语法/权限错误。"""

from dataclasses import dataclass
from time import sleep

from pymysql.err import ProgrammingError

_CONCURRENCY_LIMIT = "warehouse_concurrency_limit"
_QUERY_STAGES = ("latest", "projection")
_QUERY_SOURCES = ("product_daily", "product_promotion", "profile_records")


@dataclass
class RetryBudget:
    sleeper: object = sleep
    used: int = 0
    delays: tuple[int, ...] = (2, 6, 15)


def _query_category(exc):
    if not isinstance(exc, ProgrammingError) or not exc.args or exc.args[0] != 1064:
        return "unknown"
    if len(exc.args) > 1 and str(exc.args[1]).startswith("Exceed concurrency limit:"):
        return _CONCURRENCY_LIMIT
    return "warehouse_query_rejected"


def safe_diagnostic(exc: BaseException) -> dict[str, str | int]:
    """返回可写入运行日志的白名单诊断，驱动原文仅用于判别类别。"""
    diagnostic: dict[str, str | int] = {"category": _query_category(exc)}
    if isinstance(exc, ProgrammingError):
        context = getattr(exc, "_warehouse_query_context", None)
        if context is not None:
            stage, source, retries = context
            if stage is not None:
                diagnostic["stage"] = stage
            if source is not None:
                diagnostic["source"] = source
            diagnostic["retries_consumed"] = retries
    return diagnostic


def execute_read(
    cursor, query, parameters, budget=None, heartbeat=lambda: None, *, stage=None, source=None
):
    """一个reader全部查询共享最多3次额外尝试；只重试明确的资源并发拒绝。"""
    budget = budget or RetryBudget()
    used_before = budget.used
    stage = stage if type(stage) is str and stage in _QUERY_STAGES else None
    source = source if type(source) is str and source in _QUERY_SOURCES else None
    while True:
        heartbeat()
        try:
            return cursor.execute(query, parameters)
        except ProgrammingError as exc:
            limited = _query_category(exc) == _CONCURRENCY_LIMIT
            if not limited or budget.used >= len(budget.delays):
                exc._warehouse_query_context = (stage, source, budget.used - used_before)
                raise
            delay = budget.delays[budget.used]
            budget.used += 1
            budget.sleeper(delay)
