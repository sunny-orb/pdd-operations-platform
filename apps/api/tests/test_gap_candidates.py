from datetime import date

from tests.test_history_coverage import NOW, publish, seed_checkpoint

pytest_plugins = ["tests.sales_pg_support"]


def test_confirm_only_missing_shop_and_past_day(sales_pg, tmp_path):
    from app.modules.data_status.gap_candidates import find_repair_dates

    publish(sales_pg, tmp_path)
    seed_checkpoint(sales_pg)

    class Probe:
        def available_gap_days(self, code, gaps):
            assert not c.in_transaction()
            assert code == "shop_daily"
            assert gaps == {date(2026, 10, 21): ("663599807",)}
            return tuple(gaps)

    with sales_pg.connect() as c:
        assert find_repair_dates(c, "shop_daily", NOW.date(), Probe()) == (date(2026, 10, 21),)
        assert find_repair_dates(c, "product_daily", NOW.date(), Probe()) is None
        publish(sales_pg, tmp_path, missing=False)
        assert find_repair_dates(c, "shop_daily", NOW.date(), None) == ()


def test_warehouse_evidence_requires_exact_store_and_day():
    from unittest.mock import MagicMock

    from app.connectors.source_probes import SourceProbes

    probe = SourceProbes(None, ".")
    probe.reader = MagicMock()
    cursor = probe.reader.connection.cursor.return_value.__enter__.return_value
    a, b = date(2026, 11, 20), date(2026, 11, 22)
    cursor.fetchall.return_value = [
        {"d": a, "shop_id": 799193231},
        {"d": b, "shop_id": 799193231},
    ]
    assert probe.available_gap_days("shop_daily", {a: ("663599807",), b: ("799193231",)}) == (b,)
    sql, params = cursor.execute.call_args.args
    assert "GROUP BY" in sql and "dt<%s" in sql and "time_granularity=%s" in sql
    assert a in params and date(2026, 11, 23) in params
