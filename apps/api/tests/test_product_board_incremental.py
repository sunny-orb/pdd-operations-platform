"""商品两段窗口与不可变归档的真实PostgreSQL回归。"""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.modules.product_board.source import TABLES, normalize, windows
from app.modules.product_board.storage import ProductBoardStore

pytest_plugins = ["tests.sales_pg_support"]


class Source:
    """只替代外部数仓；规范化、归档、事务和发布都走真实实现。"""

    def __init__(self, end):
        self.end, self.calls = end, []
        self.raw = {
            "product_daily": [],
            "product_promotion": [],
            "profile_records": [dict(__id="p", spigf="123")],
        }
        for offset in range(550):
            day = end - timedelta(days=offset)
            self.raw["product_daily"].append(
                dict(
                    z_id=str(day),
                    shop_id="663599807",
                    spu_id="123",
                    dt=str(day),
                    time_granularity="day",
                    transaction_amount="12.34",
                )
            )
            self.raw["product_promotion"].append(
                dict(
                    id=str(day),
                    shop_id="663599807",
                    spu_id="123",
                    dt=str(day),
                    start_dt=str(day),
                    time_granularity="day",
                    total_cost="1.23",
                )
            )

    def latest(self, selected=None):
        return self.end

    def read_stable(self, selected=None, *, periods=None, end=None):
        end = end or self.end
        result = {}
        for code in TABLES if selected is None else selected:
            spans = (
                None
                if code == "profile_records"
                else (periods[code] if periods is not None else windows(end, code))
            )
            self.calls.append((code, spans))
            raw = (
                self.raw[code]
                if spans is None
                else [
                    r
                    for r in self.raw[code]
                    if any(a <= date.fromisoformat(r["dt"]) <= b for a, b in spans)
                ]
            )
            result[code] = normalize(code, raw, end)
        return end, result


def receive(store, source, selected=None, full_sources=()):
    from app.modules.product_board.incremental import receive_product_board

    def callback(hb):
        return receive_product_board(store, source, selected, full_sources, hb)

    return store.run(callback) if selected is None else store.run_selected(selected, callback)


def heads(engine):
    with engine.connect() as c:
        return dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        )


def test_unchanged_receipt_reuses_evidence_without_empty_archive(sales_pg, tmp_path):
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = store.run(lambda hb: source.read_stable())
    before = {
        str(p.relative_to(tmp_path)): p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()
    }
    second = store.run(lambda hb: source.read_stable())
    assert first["versions"] == second["versions"]
    assert before == {
        str(p.relative_to(tmp_path)): p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize(
    "old_end,new_end",
    [
        (date(2026, 11, 11), date(2026, 11, 12)),
        (date(2026, 12, 31), date(2027, 1, 1)),
        (date(2024, 2, 28), date(2024, 3, 1)),
        (date(2025, 2, 28), date(2025, 3, 1)),
        (date(2026, 8, 1), date(2026, 11, 12)),
        (date(2026, 1, 1), date(2027, 2, 1)),
    ],
)
@pytest.mark.parametrize("shared", [False, True])
def test_window_merge_equals_full_and_keeps_old_version(
    sales_pg, tmp_path, old_end, new_end, monkeypatch, shared
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(shared).lower())
    source = Source(new_end)
    source.end = old_end
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    source.end = new_end
    source.calls.clear()
    second = receive(store, source)
    bounded_calls = list(source.calls)
    _, expected = source.read_stable()
    with sales_pg.connect() as c:
        for code, table, amount in [
            ("product_daily", "product_daily_facts", "product_revenue"),
            ("product_promotion", "product_promotion_records", "spend"),
        ]:
            actual = c.execute(
                sa.text(
                    f"SELECT source_row_key,{amount} FROM read_{table} WHERE dataset_version_id=:v"
                ),
                {"v": second["versions"][code]},
            ).all()
            assert dict(actual) == {r["source_row_key"]: r[amount] for r in expected[code]}
            assert (
                c.scalar(
                    sa.text(f"SELECT count(*) FROM read_{table} WHERE dataset_version_id=:v"),
                    {"v": first["versions"][code]},
                )
                > 0
            )
        assert bool(c.scalar(sa.text("SELECT count(*) FROM fact_content_blocks"))) == shared
    if new_end - old_end == timedelta(days=1):
        for code, spans in bounded_calls:
            if code != "profile_records":
                assert spans == (
                    (
                        date(2024, 2, 29)
                        if new_end == date(2025, 3, 1)
                        else new_end.replace(year=new_end.year - 1),
                        new_end.replace(year=new_end.year - 1),
                    ),
                    (new_end, new_end),
                )
    before = len(list(tmp_path.iterdir()))
    assert receive(store, source)["versions"] == second["versions"]
    assert len(list(tmp_path.iterdir())) == before


@pytest.mark.parametrize("mutation", ["missing", "shop", "product", "day"])
def test_unconfirmed_missing_or_moved_keys_keep_heads(sales_pg, tmp_path, mutation):
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    row = source.raw["product_daily"][0]
    if mutation == "missing":
        source.raw["product_daily"].remove(row)
    elif mutation == "shop":
        row["shop_id"] = "191467219"
    elif mutation == "product":
        row["spu_id"] = "456"
    else:
        row["dt"] = "2026-10-01"
    with pytest.raises(ValueError):
        receive(store, source)
    assert heads(sales_pg) == first["versions"]


def test_empty_ad_window_preserves_old_history_and_date_regression_fails(sales_pg, tmp_path):
    source = Source(date(2026, 11, 12))
    source.raw["product_promotion"] = [
        r for r in source.raw["product_promotion"] if r["dt"] < "2026-10-01"
    ]
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    assert receive(store, source)["versions"] == first["versions"]
    source.end -= timedelta(days=1)
    with pytest.raises(ValueError, match="BASELINE.*SCOPE"):
        receive(store, source)
    assert heads(sales_pg) == first["versions"]


def test_archive_mismatch_even_full_reconciliation_keeps_heads(sales_pg, tmp_path):
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    from app.modules.product_board.source import fingerprint

    archive = next(p for p in tmp_path.iterdir() if p.is_dir())
    payload = archive / "product_daily.json"
    changed = json.loads(payload.read_text())
    changed[0]["product_revenue"] = "100.00"
    payload.write_text(json.dumps(changed))
    payload.with_suffix(".json.sha256").write_text(hashlib.sha256(payload.read_bytes()).hexdigest())
    manifest = archive / "manifest.json"
    policy = json.loads(manifest.read_text())
    for name in ("product_daily.json", "product_daily.json.sha256"):
        policy["files"][name] = fingerprint((archive / name).read_text())
    manifest.write_text(json.dumps(policy))
    manifest.with_suffix(".json.sha256").write_text(
        hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="BASELINE_MISMATCH"):
        receive(store, source, full_sources=("product_daily",))
    assert heads(sales_pg) == first["versions"]


def test_final_baseline_fence_keeps_competing_head(sales_pg, tmp_path, monkeypatch):
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    source.raw["product_daily"][0]["transaction_amount"] = "20.00"
    second = receive(store, source)
    source.raw["product_daily"][0]["transaction_amount"] = "30.00"
    stage = store._stage

    def race(*args, **kwargs):
        result = stage(*args, **kwargs)
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "UPDATE current_datasets SET version_id=:id WHERE dataset_code='product_daily'"
                ),
                dict(id=first["versions"]["product_daily"]),
            )
        return result

    monkeypatch.setattr(store, "_stage", race)
    with pytest.raises(ValueError, match="SOURCE_INPUT_CHANGED"):
        receive(store, source)
    actual = heads(sales_pg)
    assert actual["product_daily"] == first["versions"]["product_daily"]
    assert actual["profile_links"] == second["versions"]["profile_links"]


def test_full_historical_repair_does_not_relax_other_incremental_source(sales_pg, tmp_path):
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    old = next(r for r in source.raw["product_daily"] if r["dt"] == "2026-09-01")
    old["transaction_amount"] = "45.67"
    assert receive(store, source)["versions"] == first["versions"]
    source.raw["product_promotion"].pop(0)
    with pytest.raises(ValueError, match="DELETE_UNCONFIRMED"):
        receive(store, source, full_sources=("product_daily",))
    assert heads(sales_pg) == first["versions"]
    second = receive(store, source, full_sources=("product_daily", "product_promotion"))
    assert second["versions"]["product_daily"] != first["versions"]["product_daily"]
    with sales_pg.connect() as c:
        assert (
            str(
                c.scalar(
                    sa.text(
                        "SELECT product_revenue FROM product_daily_facts "
                        "WHERE dataset_version_id=:id AND business_date='2026-09-01'"
                    ),
                    dict(id=second["versions"]["product_daily"]),
                )
            )
            == "45.67"
        )


@pytest.mark.parametrize(
    "code,raw_amount,column,table",
    [
        ("product_daily", "transaction_amount", "product_revenue", "product_daily_facts"),
        ("product_promotion", "total_cost", "spend", "product_promotion_records"),
    ],
)
def test_single_day_revision_and_weekly_repair_preserve_old_values(
    sales_pg, tmp_path, monkeypatch, code, raw_amount, column, table
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    source.raw[code][0][raw_amount] = "30.00"
    source.raw[code][1][raw_amount] = "20.00"
    source.calls.clear()
    daily = receive(store, source, selected=(code,))
    assert source.calls == [(code, ((date(2026, 11, 12), date(2026, 11, 12)),))]

    def amounts(version):
        with sales_pg.connect() as c:
            return dict(
                c.execute(
                    sa.text(
                        f"SELECT source_row_key,{column} FROM read_{table} "
                        "WHERE dataset_version_id=:v"
                    ),
                    {"v": version},
                ).all()
            )

    original = amounts(first["versions"][code])
    partial = amounts(daily["versions"][code])
    assert partial == {**original, "2026-11-12": 30}
    before = {
        str(p.relative_to(tmp_path)): p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()
    }
    assert receive(store, source, selected=(code,))["versions"] == daily["versions"]
    assert before == {
        str(p.relative_to(tmp_path)): p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()
    }
    full = receive(store, source, selected=(code,), full_sources=(code,))
    assert amounts(full["versions"][code]) == {**partial, "2026-11-11": 20}
    assert amounts(first["versions"][code]) == original
    # 只约束其他上游来源；派生资料关系须重新绑定本次相容输入版本。
    for other in TABLES:
        if other != code:
            assert full["versions"][other] == first["versions"][other]


def test_producer_change_creates_one_version_then_noop(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import storage
    from app.modules.product_board.source import fingerprint

    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    old_sources = storage.producer_sources()
    old_sources.pop("incremental.py")
    old_sources["storage.py"] += "\n# historical producer\n"
    with monkeypatch.context() as old:
        old.setattr(storage, "producer_sources", lambda: old_sources)
        old.setattr(storage, "LOADED_PRODUCER_HASH", fingerprint(old_sources))
        first = store.run(lambda hb: source.read_stable())
    second = receive(store, source)
    assert second["versions"] != first["versions"]
    before = len(list(tmp_path.iterdir()))
    assert receive(store, source)["versions"] == second["versions"]
    assert len(list(tmp_path.iterdir())) == before


def test_failed_stage_does_not_publish(sales_pg, tmp_path, monkeypatch):
    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    source.raw["product_daily"][0]["transaction_amount"] = "99.00"
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise ValueError("TEST_STAGE_FAILURE")

        patch.setattr(store, "_stage", fail)
        with pytest.raises(ValueError, match="TEST_STAGE_FAILURE"):
            receive(store, source)
    assert heads(sales_pg) == first["versions"]


def test_changed_profile_relationship_is_not_false_noop(sales_pg, tmp_path, monkeypatch):
    from app.modules.product_board import storage

    source = Source(date(2026, 11, 12))
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    monkeypatch.setattr(storage, "parse_ids", lambda value: ("456",) if value else ())
    second = receive(store, source)
    assert first["versions"]["profile_links"] != second["versions"]["profile_links"]
    for code in TABLES:
        assert first["versions"][code] == second["versions"][code]
    before = len(list(tmp_path.iterdir()))
    assert receive(store, source)["versions"] == second["versions"]
    assert len(list(tmp_path.iterdir())) == before


def test_slower_promotion_keeps_coverage_and_defers_older_correction_to_full(sales_pg, tmp_path):
    source = Source(date(2026, 11, 12))
    source.raw["product_promotion"] = [
        r for r in source.raw["product_promotion"] if r["dt"] <= "2026-11-10"
    ]
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    source.end = date(2026, 11, 10)
    source.raw["product_promotion"][0]["total_cost"] = "9.99"
    second = receive(store, source, selected=("product_promotion",))
    assert second["end"] == "2026-11-12"
    assert second["versions"] == first["versions"]
    second = receive(
        store, source, selected=("product_promotion",), full_sources=("product_promotion",)
    )
    assert second["versions"]["product_daily"] == first["versions"]["product_daily"]
    with sales_pg.connect() as c:
        assert (
            str(
                c.scalar(
                    sa.text(
                        "SELECT spend FROM product_promotion_records WHERE dataset_version_id=:id "
                        "AND period_start='2026-11-10'"
                    ),
                    dict(id=second["versions"]["product_promotion"]),
                )
            )
            == "9.99"
        )


def test_bounded_reader_double_reads_only_requested_days():
    from app.connectors.product_board import ProductBoardReader
    from app.connectors.retry import RetryBudget

    source = Source(date(2026, 11, 12))
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            queries.append((sql, params))
            self.params = params

        def fetchall(self):
            start, stop = self.params[-4:-2]
            return [
                r
                for r in source.raw["product_daily"]
                if start <= date.fromisoformat(r["dt"]) < stop
            ]

    class Connection:
        def cursor(self):
            return Cursor()

    reader = ProductBoardReader.__new__(ProductBoardReader)
    reader.connection = Connection()
    reader.heartbeat = lambda: None
    reader.retry_budget = RetryBudget()
    periods = ((date(2025, 11, 12), date(2025, 11, 12)), (date(2026, 10, 14), date(2026, 11, 12)))
    end, rows = reader.read_stable(
        ("product_daily",), periods={"product_daily": periods}, end=source.end
    )
    assert end == source.end
    assert len(rows["product_daily"]) == 31
    assert {r["business_date"].year for r in rows["product_daily"]} == {2025, 2026}
    assert [(params[-4], params[-3]) for _, params in queries] == [
        (date(2025, 11, 12), date(2025, 11, 13)),
        (date(2026, 10, 14), date(2026, 11, 13)),
    ] * 2
    assert all("AND dt>=%s AND dt<%s" in sql for sql, _ in queries)
    with pytest.raises(ValueError, match="SOURCE_DATE_SCOPE_INVALID"):
        reader.read_stable(
            ("product_daily",),
            periods={"product_daily": ((date(2020, 1, 1), source.end),)},
            end=source.end,
        )


@pytest.mark.parametrize(
    "code,key,amount,table,day_field",
    [
        ("product_daily", "z_id", "transaction_amount", "product_daily_facts", "business_date"),
        ("product_promotion", "id", "total_cost", "product_promotion_records", "period_start"),
    ],
)
@pytest.mark.parametrize("shared", [False, True])
def test_repair_partial_shop_day_and_repeat(
    sales_pg, tmp_path, monkeypatch, code, key, amount, table, day_field, shared
):
    from app.modules.product_board.incremental import (
        missing_product_shop_days,
        receive_product_board,
    )
    from app.modules.sales.source_policy import SHOPS

    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(shared).lower())
    end = date(2026, 11, 23)
    day = date(2026, 11, 21)
    source = Source(end)
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    with sales_pg.connect() as c:
        assert missing_product_shop_days(c, code, [day]) == {
            day: tuple(s for s in SHOPS if s != "663599807")
        }
    original = next(r for r in source.raw[code] if r["dt"] == str(day))
    source.raw[code].remove(original)
    late = {**original, key: "late", "shop_id": "191467219", amount: "0.00"}
    source.raw[code].append(late)
    source.calls.clear()

    def exact():
        return store.run_selected(
            [code],
            lambda hb: receive_product_board(
                store, source, [code], heartbeat=hb, repair_days={code: [day]}, repair_only=True
            ),
        )

    second = exact()
    assert source.calls == [(code, ((day, day),))]
    old_id, new_id = first["versions"][code], second["versions"][code]
    assert old_id != new_id
    assert all(first["versions"][k] == second["versions"][k] for k in TABLES if k != code)
    with sales_pg.connect() as c:
        statement = sa.text(
            f"SELECT source_row_key FROM read_{table} "
            f"WHERE dataset_version_id=:v AND {day_field}=:d"
        )
        assert set(c.scalars(statement, dict(v=new_id, d=day))) == {str(day), "late"}
        assert set(c.scalars(statement, dict(v=old_id, d=day))) == {str(day)}
        assert missing_product_shop_days(c, code, [day]) == {
            day: tuple(s for s in SHOPS if s not in ("663599807", "191467219"))
        }
    before = set(tmp_path.rglob("*"))
    source.raw[code].remove(late)
    assert exact()["versions"] == second["versions"]
    assert set(tmp_path.rglob("*")) == before


def test_regular_and_gap_share_read_and_publication(sales_pg, tmp_path):
    from app.modules.product_board.incremental import receive_product_board

    end = date(2026, 11, 23)
    code = "product_daily"
    day = end - timedelta(days=2)
    source = Source(end)
    store = ProductBoardStore(sales_pg, tmp_path)
    first = receive(store, source)
    old = next(r for r in source.raw[code] if r["dt"] == str(day))
    source.raw[code].append({**old, "z_id": "late", "shop_id": "191467219"})
    source.raw[code][0]["transaction_amount"] = "80.00"
    source.calls.clear()
    second = store.run_selected(
        [code],
        lambda hb: receive_product_board(
            store, source, [code], heartbeat=hb, repair_days={code: [day]}
        ),
    )
    assert source.calls == [(code, ((day, day), (end, end)))]
    assert len(list(tmp_path.iterdir())) == 2
    with sales_pg.connect() as c:
        assert set(
            c.execute(
                sa.text(
                    "SELECT source_row_key, product_revenue FROM read_product_daily_facts "
                    "WHERE dataset_version_id=:v AND source_row_key IN ('late', '2026-11-23')"
                ),
                dict(v=second["versions"][code]),
            )
        ) == {("late", Decimal("12.34")), (str(end), 80)}
    assert second["versions"][code] != first["versions"][code]


def test_normal_advance_skips_gap_that_aged_out_of_approved_window(sales_pg, tmp_path):
    from app.modules.product_board.incremental import receive_product_board

    end = date(2026, 11, 23)
    old_end = end - timedelta(days=3)
    source = Source(end)
    store = ProductBoardStore(sales_pg, tmp_path)
    source.end = old_end
    receive(store, source)
    code = "product_promotion"
    day = windows(old_end, code)[0][0]
    source.end = end
    source.calls.clear()
    updated = store.run_selected(
        [code],
        lambda hb: receive_product_board(
            store, source, [code], heartbeat=hb, repair_days={code: [day]}
        ),
    )
    assert updated["end"] == str(end)
    assert all(not (lo <= day <= hi) for _, spans in source.calls for lo, hi in spans)


def test_repair_only_rejects_outside_day_before_read(sales_pg, tmp_path):
    from app.modules.product_board.incremental import receive_product_board

    end = date(2026, 11, 23)
    code = "product_daily"
    store = ProductBoardStore(sales_pg, tmp_path)
    source = Source(end)
    first = receive(store, source)
    source.calls.clear()
    with pytest.raises(ValueError, match="SOURCE_DATE_SCOPE_INVALID"):
        store.run_selected(
            [code],
            lambda hb: receive_product_board(
                store,
                source,
                [code],
                heartbeat=hb,
                repair_days={code: [end + timedelta(days=1)]},
                repair_only=True,
            ),
        )
    assert source.calls == []
    assert heads(sales_pg) == first["versions"]


def test_empty_gap_repair_keeps_historical_producer_and_ordinary_receipt_still_upgrades(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.product_board import storage
    from app.modules.product_board.incremental import receive_product_board
    from app.modules.product_board.source import fingerprint

    code = "product_promotion"
    source = Source(date(2026, 11, 23))
    day = date(2026, 11, 21)
    store = ProductBoardStore(sales_pg, tmp_path)
    old_sources = storage.producer_sources()
    old_sources["incremental.py"] += "\n# historical producer\n"
    with monkeypatch.context() as old:
        old.setattr(storage, "producer_sources", lambda: old_sources)
        old.setattr(storage, "LOADED_PRODUCER_HASH", fingerprint(old_sources))
        first = receive(store, source)
    original = next(r for r in source.raw[code] if r["dt"] == str(day))
    source.raw[code].remove(original)
    before = set(tmp_path.rglob("*"))
    empty = store.run_selected(
        [code],
        lambda hb: receive_product_board(
            store, source, [code], heartbeat=hb, repair_days={code: [day]}, repair_only=True
        ),
    )
    assert empty["versions"] == first["versions"]
    assert set(tmp_path.rglob("*")) == before
    ordinary = receive(store, source, [code])
    assert ordinary["versions"][code] != first["versions"][code]
