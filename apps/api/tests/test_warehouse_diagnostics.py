import json
import sys
from datetime import date

import pymysql
import pytest
from pymysql.err import ProgrammingError

from app.connectors import retry
from app.connectors.product_board import ProductBoardReader
from app.jobs import sync_product_board

PRIVATE_TEXT = "SELECT sensitive_row FROM private_table host=internal password=secret-token"


class WarehouseCursor:
    """只替代远端游标，连接器仍执行真实查询及退避路径。"""

    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, parameters):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def cursor(self):
        return self

    def close(self):
        pass


@pytest.mark.parametrize(
    ("message", "category", "waits"),
    [
        (
            f"Exceed concurrency limit: 3 backend {PRIVATE_TEXT}",
            "warehouse_concurrency_limit",
            [2, 6, 15],
        ),
        (
            f"syntax error near Exceed concurrency limit: {PRIVATE_TEXT}",
            "warehouse_query_rejected",
            [],
        ),
    ],
    ids=("resource_limit", "ordinary_1064"),
)
def test_1064_uses_exact_prefix_and_reports_only_safe_fields(message, category, waits):
    error = ProgrammingError(1064, message)
    delays = []
    cursor = WarehouseCursor(*[error] * (len(waits) + 1))

    with pytest.raises(ProgrammingError) as raised:
        retry.execute_read(cursor, PRIVATE_TEXT, (PRIVATE_TEXT,), retry.RetryBudget(delays.append))

    assert raised.value is error
    assert raised.value.args == (1064, message)
    assert delays == waits
    assert retry.safe_diagnostic(raised.value) == {
        "category": category,
        "retries_consumed": len(waits),
    }


def test_query_diagnostic_excludes_unrecognized_stage_and_source():
    cursor = WarehouseCursor(ProgrammingError(1064, PRIVATE_TEXT))
    with pytest.raises(ProgrammingError) as raised:
        retry.execute_read(cursor, "SELECT 1", (), stage=PRIVATE_TEXT, source=PRIVATE_TEXT)

    assert retry.safe_diagnostic(raised.value) == {
        "category": "warehouse_query_rejected",
        "retries_consumed": 0,
    }
    assert retry.safe_diagnostic(ValueError(PRIVATE_TEXT)) == {"category": "unknown"}


def test_reused_product_reader_reports_source_stage_and_retries_for_each_query(monkeypatch):
    limited = ProgrammingError(1064, f"Exceed concurrency limit: {PRIVATE_TEXT}")
    rejected = ProgrammingError(1064, PRIVATE_TEXT)
    cursor = WarehouseCursor(limited, limited, limited, limited, rejected)
    monkeypatch.setattr(pymysql, "connect", lambda **_: cursor)
    reader = ProductBoardReader(password="test-only")
    waits = []
    reader.retry_budget.sleeper = waits.append

    with pytest.raises(ProgrammingError) as latest:
        reader.latest(("product_daily",))
    with pytest.raises(ProgrammingError) as projection:
        reader.projection("profile_records", date(2026, 11, 10))

    assert waits == [2, 6, 15]
    assert retry.safe_diagnostic(latest.value) == {
        "category": "warehouse_concurrency_limit",
        "stage": "latest",
        "source": "product_daily",
        "retries_consumed": 3,
    }
    assert retry.safe_diagnostic(projection.value) == {
        "category": "warehouse_query_rejected",
        "stage": "projection",
        "source": "profile_records",
        "retries_consumed": 0,
    }


def test_product_sync_cli_failure_excludes_raw_driver_message(monkeypatch, capsys):
    cursor = WarehouseCursor(ProgrammingError(1064, PRIVATE_TEXT))
    monkeypatch.setattr(pymysql, "connect", lambda **_: cursor)
    monkeypatch.setattr(sync_product_board.getpass, "getpass", lambda _: "test-only")
    monkeypatch.setattr(sys, "argv", ["sync_product_board", "--verify-only"])

    assert sync_product_board._main() == 1
    output = capsys.readouterr().out
    assert PRIVATE_TEXT not in output
    result = json.loads(output)
    assert result["status"] == "failed"
    assert result["source_code"] == 1064
    assert result["diagnostic"] == {
        "category": "warehouse_query_rejected",
        "stage": "latest",
        "source": "product_daily",
        "retries_consumed": 0,
    }
