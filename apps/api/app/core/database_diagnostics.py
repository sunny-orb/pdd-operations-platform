"""只根据异常类型/SQLSTATE分类，按请求记录SQL实测耗时，不读取异常文本。"""

from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter

import psycopg
from sqlalchemy import Engine, event
from sqlalchemy.exc import DBAPIError, SQLAlchemyError, TimeoutError

request_database_metrics: ContextVar[dict | None] = ContextVar(
    "request_database_metrics", default=None
)


@dataclass(frozen=True)
class DatabaseFailure:
    code: str
    status: int
    stage: str
    exception_types: str


def classify_database_failure(error: Exception) -> DatabaseFailure | None:
    """遍历封装cause和DBAPI原异常；非数据库错误返回None，不能按错误文本猜连接故障。"""
    chain, pending, visited = [], [error], set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        chain.append(current)
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        if cause is not None:
            pending.append(cause)
        if isinstance(current, DBAPIError):
            pending.append(current.orig)
    types = ",".join(type(item).__name__ for item in chain)
    if any(isinstance(item, TimeoutError) for item in chain):
        return DatabaseFailure("DATABASE_BUSY", 503, "pool_checkout", types)
    states = {item.sqlstate for item in chain if isinstance(item, psycopg.Error)}
    if "57014" in states:
        return DatabaseFailure("QUERY_TIMEOUT", 503, "sql_execute", types)
    if any(
        state and (state.startswith("08") or state in {"57P01", "57P02", "57P03", "53300"})
        for state in states
    ) or any(
        isinstance(item, DBAPIError)
        and item.connection_invalidated
        or isinstance(item, (psycopg.OperationalError, psycopg.InterfaceError))
        and item.sqlstate is None
        for item in chain
    ):
        return DatabaseFailure("DATABASE_UNAVAILABLE", 503, "connection", types)
    if any(isinstance(item, (SQLAlchemyError, psycopg.Error)) for item in chain):
        return DatabaseFailure("DATABASE_ERROR", 500, "sql_execute", types)
    return None


def _before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
    if request_database_metrics.get() is not None:
        context._pdd_api_sql_started = perf_counter()


def _record_sql_duration(context):
    metrics = request_database_metrics.get()
    started = getattr(context, "_pdd_api_sql_started", None)
    if metrics is not None and started is not None:
        elapsed = (perf_counter() - started) * 1000
        metrics["sql_elapsed_ms"] = elapsed
        metrics["sql_total_ms"] = metrics.get("sql_total_ms", 0) + elapsed
        del context._pdd_api_sql_started


def _after_cursor_execute(connection, cursor, statement, parameters, context, executemany):
    _record_sql_duration(context)


def _handle_error(exception_context):
    _record_sql_duration(exception_context.execution_context)


def install_sql_timing():
    """幂等安装引擎事件；仅请求上下文记录耗时，不缓存SQL、绑定参数或连接信息。"""
    for name, handler in (
        ("before_cursor_execute", _before_cursor_execute),
        ("after_cursor_execute", _after_cursor_execute),
        ("handle_error", _handle_error),
    ):
        if not event.contains(Engine, name, handler):
            event.listen(Engine, name, handler)
