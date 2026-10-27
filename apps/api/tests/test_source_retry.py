import pytest
from pymysql.err import ProgrammingError

from app.connectors.retry import RetryBudget, execute_read


def test_only_backend_concurrency_is_retried_with_shared_bounded_budget():
    waits, heartbeats = [], []
    budget = RetryBudget(sleeper=waits.append)

    class Cursor:
        calls = 0

        def execute(self, sql, values):
            self.calls += 1
            if self.calls < 3:
                raise ProgrammingError(1064, "Exceed concurrency limit: 3 backend [host=example]")
            return 4

    c = Cursor()
    assert execute_read(c, "approved query", (), budget, lambda: heartbeats.append(1)) == 4
    assert waits == [2, 6]
    assert len(heartbeats) == 3

    class Always:
        def execute(self, *_):
            raise ProgrammingError(1064, "Exceed concurrency limit: 3 backend")

    with pytest.raises(ProgrammingError):
        execute_read(Always(), "query", (), budget)
    assert waits == [2, 6, 15]

    class Syntax:
        def execute(self, *_):
            raise ProgrammingError(1064, "syntax error")

    with pytest.raises(ProgrammingError):
        execute_read(Syntax(), "query", (), RetryBudget(sleeper=waits.append))
    assert waits == [2, 6, 15]
