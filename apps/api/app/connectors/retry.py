"""只对已观察的数仓backend并发拒绝做有预算的串行退避，不吞掉语法/权限错误。"""

from dataclasses import dataclass
from time import sleep

from pymysql.err import ProgrammingError


@dataclass
class RetryBudget:
    sleeper: object = sleep
    used: int = 0
    delays: tuple[int, ...] = (2, 6, 15)


def execute_read(cursor, query, parameters, budget=None, heartbeat=lambda: None):
    """一个reader全部查询共享最多3次额外尝试；只重试明确的资源并发拒绝。"""
    budget = budget or RetryBudget()
    while True:
        heartbeat()
        try:
            return cursor.execute(query, parameters)
        except ProgrammingError as exc:
            limited = (
                len(exc.args) > 1
                and exc.args[0] == 1064
                and str(exc.args[1]).startswith("Exceed concurrency limit:")
            )
            if not limited or budget.used >= len(budget.delays):
                raise
            delay = budget.delays[budget.used]
            budget.used += 1
            budget.sleeper(delay)
