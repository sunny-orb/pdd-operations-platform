"""首链接收反例；不读取上游或业务库。"""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from app.connectors.shop_daily import ShopDailyReader
from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_policy import SHOPS


def raw_row(**changes):
    row = dict(
        id="0001",
        dt="2026-10-26 00:00:00",
        time_granularity="day",
        shop_id="663599807",
        shop_name="kocotree官方旗舰店",
        updated_at="2026-10-27 06:00:00",
        metrics={"成交金额": "10.20", "店铺访客数": "10", "成交买家数": "2", "成交订单数": "3"},
    )
    row.update(changes)
    return row


def window():
    return ReadWindow(date(2026, 10, 26), date(2026, 10, 27), tuple(SHOPS))


def test_exact_amount_zero_and_unknown_source_timezone():
    candidate = normalize_shop_daily(
        [
            raw_row(
                metrics={
                    "成交金额": "0.00",
                    "店铺访客数": "0",
                    "成交买家数": "0",
                    "成交订单数": "0",
                }
            )
        ],
        window(),
    )
    row = candidate.rows[0]
    assert row["shop_revenue"] == Decimal("0.00")
    assert row["source_record_id"] == "0001"
    assert row["source_updated_at"] is None
    assert row["source_updated_time_raw"] == "2026-10-27 06:00:00"
    assert row["product_visitors"] is None


@pytest.mark.parametrize(
    "changes",
    [
        dict(shop_id="unknown"),
        dict(time_granularity="recent_7d"),
        dict(dt="2026-10-26 12:00:00"),
        dict(id=""),
        dict(metrics={"成交金额": "1.001"}),
        dict(metrics={"成交金额": "NaN"}),
    ],
)
def test_invalid_core_or_identity_blocks_candidate(changes):
    with pytest.raises(SalesError):
        normalize_shop_daily([raw_row(**changes)], window())


def test_duplicate_id_and_business_day_block_candidate():
    for second in [raw_row(), raw_row(id="0002")]:
        with pytest.raises(SalesError):
            normalize_shop_daily([raw_row(), second], window())


def test_same_row_count_with_changed_amount_is_not_stable():
    first = [raw_row()]
    changed = deepcopy(first)
    changed[0]["metrics"]["成交金额"] = "20.20"
    reads = iter([first, changed, first, changed])
    reader = ShopDailyReader.for_test(lambda _: next(reads))
    with pytest.raises(SalesError, match="SOURCE_CHANGED"):
        reader.read_stable(window())


def test_stable_reader_has_finite_attempts_and_preserves_projection():
    reader = ShopDailyReader.for_test(lambda _: [raw_row()])
    candidate = reader.read_stable(window())
    assert candidate.rows[0]["shop_revenue"] == Decimal("10.20")
    assert candidate.raw_rows[0]["metrics"]["成交金额"] == "10.20"


def test_unapproved_shop_window_and_empty_batch_are_rejected():
    with pytest.raises(SalesError):
        ReadWindow(date(2026, 10, 27), date(2026, 10, 26), tuple(SHOPS))
    with pytest.raises(SalesError):
        normalize_shop_daily([], window())


def test_optional_source_missing_marker_is_null_but_core_marker_is_rejected():
    row = raw_row()
    row["metrics"]["客单价"] = "-"
    candidate = normalize_shop_daily([row], window())
    assert candidate.rows[0]["source_aov"] is None
    assert candidate.raw_rows[0]["metrics"]["客单价"] == "-"
    row["metrics"]["成交金额"] = "-"
    with pytest.raises(SalesError):
        normalize_shop_daily([row], window())


def test_future_verify_window_is_rejected_before_source_connection(monkeypatch, capsys):
    from app.jobs import sync_shop_daily
    from app.modules.sales import source_policy

    monkeypatch.setattr(source_policy, "calendar_through", lambda: date(2026, 10, 27))
    monkeypatch.setattr(
        "sys.argv", ["sync", "--from", "2026-10-28", "--to", "2027-01-31", "--verify-only"]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("越界窗口不能读取凭据或创建来源连接")

    monkeypatch.setattr(sync_shop_daily.getpass, "getpass", forbidden)
    monkeypatch.setattr(sync_shop_daily, "ShopDailyReader", forbidden)
    assert sync_shop_daily.main() == 1
    assert "SOURCE_WINDOW_NOT_APPROVED" in capsys.readouterr().out
    reader = ShopDailyReader.for_test(forbidden)
    with pytest.raises(SalesError, match="SOURCE_WINDOW_NOT_APPROVED"):
        reader.read_stable(ReadWindow(date(2026, 10, 28), date(2027, 1, 31), tuple(SHOPS)))
