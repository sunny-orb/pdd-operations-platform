"""来源分页与历史规模分离；替代外部连接，运行真实读取和合并实现。"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.connectors import product_board, sales_sources, shop_daily
from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.source_policy import SHOPS


def test_sales_producer_tracks_read_window_policy(monkeypatch):
    from pathlib import Path

    from app.modules.sales.storage import producer_sources

    name = "modules/source_read_policy.py"
    before = producer_sources()
    assert name in before
    original = Path.read_text

    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return value + "\n# changed read policy\n" if path.as_posix().endswith(name) else value

    monkeypatch.setattr(Path, "read_text", changed)
    assert producer_sources()[name] != before[name]


def connection_for(module, monkeypatch, rows, calls):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    def execute(_, query, params, *args, **kwargs):
        start, stop, size, offset = params[-4:]
        calls.append((start, stop, size, offset))
        selected = [r for r in rows if start <= date.fromisoformat(r["dt"]) < stop]
        cursor.fetchall.return_value = [dict(r) for r in selected[offset : offset + size]]

    monkeypatch.setattr(module, "execute_read", execute)
    return connection


@pytest.mark.parametrize(
    "code",
    ["shop_daily", *(code for code, spec in SPECS.items() if spec.source_system == "warehouse")],
)
def test_sales_read_budget_resets_by_seven_day_window_not_page(monkeypatch, code):
    module = shop_daily if code == "shop_daily" else sales_sources
    if code == "shop_daily":
        monkeypatch.setattr(module, "MAX_ROWS", 2)
        monkeypatch.setattr(module, "PAGE_SIZE", 2)
        reader = module.ShopDailyReader.for_test(None)
    else:
        monkeypatch.setattr(module, "SOURCE_MAX_ROWS", 2)
        monkeypatch.setattr(module, "PRODUCT_REGION_SOURCE_MAX_ROWS", 2)
        monkeypatch.setattr(module, "SOURCE_PAGE_SIZE", 2)
        reader = module.SalesSourceReader.for_test(None)
    rows = [
        dict(id=str(i), dt=day, json_data=json.dumps({}))
        for i, day in enumerate(["2026-11-01"] * 2 + ["2026-11-08"] * 2)
    ]
    calls = []
    reader.connection = connection_for(module, monkeypatch, rows, calls)
    reader.retry_budget = None
    reader.heartbeat = lambda: None
    window = ReadWindow(date(2026, 11, 1), date(2026, 11, 14), tuple(SHOPS))

    def scan():
        return reader._read(window) if code == "shop_daily" else reader._scan(SPECS[code], window)

    assert len(scan()) == 4
    assert {(start, stop) for start, stop, _, _ in calls} == {
        (date(2026, 11, 1), date(2026, 11, 8)),
        (date(2026, 11, 8), date(2026, 11, 15)),
    }
    rows.append(dict(id="overflow", dt="2026-11-08", json_data="{}"))
    with pytest.raises(SalesError, match="SOURCE_ROW_LIMIT"):
        scan()


@pytest.mark.parametrize("code", ["product_daily", "product_promotion"])
def test_product_reader_splits_current_and_comparison_periods_with_local_budget(monkeypatch, code):
    monkeypatch.setattr(product_board, "SOURCE_READ_MAX_ROWS", 2, raising=False)
    end = date(2026, 11, 14)
    rows = [
        dict(
            z_id=str(i),
            id=str(i),
            shop_id="663599807",
            spu_id=str(i + 1),
            dt=day,
            start_dt=day,
            time_granularity="day",
        )
        for i, day in enumerate(["2026-11-01"] * 2 + ["2026-11-08"] * 2)
    ]
    calls = []
    reader = product_board.ProductBoardReader.__new__(product_board.ProductBoardReader)
    reader.connection = connection_for(product_board, monkeypatch, rows, calls)
    reader.retry_budget, reader.heartbeat = None, lambda: None
    periods = ((date(2026, 11, 1), end),)
    assert len(reader.projection(code, end, periods=periods)) == 4
    assert all(stop - start <= timedelta(days=7) for start, stop, _, _ in calls)
    rows.append({**rows[-1], "id": "overflow", "z_id": "overflow", "spu_id": "999"})
    with pytest.raises(ValueError, match="SOURCE_ROW_LIMIT"):
        reader.projection(code, end, periods=periods)


@pytest.mark.parametrize(
    "code,field",
    [
        ("product_daily", "business_date"),
        ("product_promotion", "period_start"),
    ],
)
def test_product_merge_can_grow_beyond_old_whole_candidate_limit(code, field):
    from app.modules.product_board.incremental import _merge

    end = date(2026, 11, 12)
    historic = [
        dict(
            source_row_key=str(i),
            source_shop_id="663599807",
            source_product_id=str(i),
            **{field: date(2026, 11, 1)},
        )
        for i in range(300000)
    ]
    new = dict(
        source_row_key="new", source_shop_id="663599807", source_product_id="new", **{field: end}
    )
    result = _merge(code, historic, [new], ((end, end),), end)
    assert len(result) == 300001
    assert result[-1] == new


@pytest.mark.parametrize("code", ["product_daily", "product_promotion"])
def test_chunked_read_still_compares_two_complete_projections(monkeypatch, code):
    rows = [
        dict(
            z_id=str(i),
            id=str(i),
            shop_id="663599807",
            spu_id=str(i + 1),
            dt=day,
            start_dt=day,
            time_granularity="day",
        )
        for i, day in enumerate(["2026-11-01", "2026-11-08"])
    ]
    reader = product_board.ProductBoardReader.__new__(product_board.ProductBoardReader)
    calls = []
    reader.connection = connection_for(product_board, monkeypatch, rows, calls)
    execute = product_board.execute_read
    generations = []

    def changing(cursor, sql, params, *args, **kwargs):
        if params[-4] == date(2026, 11, 1) and params[-1] == 0:
            generations.append(len(generations) + 1)
            rows[0]["transaction_amount" if code == "product_daily" else "total_cost"] = str(
                generations[-1]
            )
        execute(cursor, sql, params, *args, **kwargs)

    monkeypatch.setattr(product_board, "execute_read", changing)
    reader.retry_budget, reader.heartbeat = None, lambda: None
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_READ"):
        reader.read_stable(
            (code,),
            periods={code: ((date(2026, 11, 1), date(2026, 11, 14)),)},
            end=date(2026, 11, 14),
        )
    assert generations == [1, 2, 3, 4]
