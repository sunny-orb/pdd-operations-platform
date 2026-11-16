"""销售五来源窗口接收等价、历史与空间验证；合成来源、真实 PostgreSQL。"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from test_sales_extended_sources import sample

from app.modules import fact_content
from app.modules.sales.archive_replay import restore_candidate
from app.modules.sales.contracts import PromotionReadWindow, ReadWindow, SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import PROMOTION_CODES, SHOPS
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]
CODES = ("shop_daily", "shop_ratings", *PROMOTION_CODES, "shop_regions")
FIELDS = dict(
    zip(
        CODES, ("shop_revenue", "source_score", "spend", "spend", "transaction_amount"), strict=True
    )
)


def scope(code, end="2026-11-12"):
    cls = PromotionReadWindow if code in PROMOTION_CODES else ReadWindow
    return cls(
        date(2025 if code in PROMOTION_CODES else 2026, 2, 1), date.fromisoformat(end), tuple(SHOPS)
    )


def row(code, key, day, amount="1.00", **changes):
    result = sample(code, id=key, dt=day)
    if code == "shop_daily":
        result["metrics"] = {
            "成交金额": amount,
            "店铺访客数": "5",
            "成交买家数": "2",
            "成交订单数": "3",
        }
    elif code == "shop_ratings":
        result["shop_rating_score"] = amount
    elif code == "shop_channel_product_promotion":
        result["metrics"]["总花费(元)"] = amount
    elif code == "shop_channel_star_shop":
        result.pop("updated_at", None)  # 实际明星源投影无 updated_at。
        result["cost"] = amount
    else:
        result["transaction_amount"] = amount
    return {**result, **changes}


def normalize(code, rows, window):
    return (
        normalize_shop_daily(rows, window)
        if code == "shop_daily"
        else normalize_source(code, rows, window)
    )


def store_for(engine, root, code, end="2026-11-12"):
    return ShopDailyStore(
        engine, archive_root=root, approved=scope(code, end), spec=SPECS.get(code)
    )


def publish(engine, root, code, rows, end="2026-11-12"):
    return store_for(engine, root, code, end).publish_candidate(
        normalize(code, rows, scope(code, end))
    )


def receive(engine, root, code, rows, *, full=False, windows=None):
    from app.modules.sales.window_incremental import receive_sales_window

    store = store_for(engine, root, code)

    def read(window):
        if windows is not None:
            windows.append(window)
        return normalize(code, rows, window)

    return store.run(lambda hb: receive_sales_window(store, read, heartbeat=hb, full=full))


def values(engine, code, version):
    table = SPECS[code].fact_table if code in SPECS else "shop_daily_facts"
    with engine.connect() as c:
        readable = fact_content.read_table(c, table)
        return dict(
            c.execute(
                sa.select(readable.c.source_row_key, readable.c[FIELDS[code]]).where(
                    readable.c.dataset_version_id == version
                )
            ).all()
        )


def archive_bytes(root):
    return {str(p.relative_to(root)): p.stat().st_size for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("code", CODES)
def test_single_day_corrects_latest_and_defers_older_revision_to_full(sales_pg, tmp_path, code):
    from app.modules.sales.window_incremental import receive_sales_window

    original = [row(code, "older", "2026-11-11"), row(code, "latest", "2026-11-12")]
    first = publish(sales_pg, tmp_path, code, original)
    upstream = [row(code, "older", "2026-11-11", "2"), row(code, "latest", "2026-11-12", "3")]
    windows = []
    store = store_for(sales_pg, tmp_path, code)

    def read(window):
        windows.append(window)
        return normalize(
            code,
            [r for r in upstream if window.start <= date.fromisoformat(r["dt"]) <= window.end],
            window,
        )

    daily = store.run(lambda hb: receive_sales_window(store, read, heartbeat=hb))
    assert windows[-1].start == windows[-1].end == date(2026, 11, 12)
    assert values(sales_pg, code, daily.version_id) == {
        "older": Decimal("1"),
        "latest": Decimal("3"),
    }
    before = archive_bytes(tmp_path)
    assert store.run(lambda hb: receive_sales_window(store, read, heartbeat=hb)).reused
    assert archive_bytes(tmp_path) == before
    full = store.run(lambda hb: receive_sales_window(store, read, heartbeat=hb, full=True))
    assert windows[-1] == scope(code)
    assert values(sales_pg, code, full.version_id) == {
        "older": Decimal("2"),
        "latest": Decimal("3"),
    }
    assert values(sales_pg, code, first.version_id) == {
        "older": Decimal("1"),
        "latest": Decimal("1"),
    }


@pytest.mark.parametrize("code", CODES)
def test_window_equals_full_preserves_history_and_repeat_does_not_grow(sales_pg, tmp_path, code):
    historic = "2025-10-20" if code in PROMOTION_CODES else "2026-08-24"
    old = [row(code, "old", historic), row(code, "recent", "2026-11-11")]
    initial = publish(sales_pg, tmp_path, code, old, "2026-11-11")
    delta = [row(code, "new", "2026-11-12", "3.00")]
    windows = []
    result = receive(sales_pg, tmp_path, code, delta, windows=windows)
    assert windows[0].start == date(2026, 11, 12)
    assert type(windows[0]) is type(scope(code))
    assert values(sales_pg, code, result.version_id) == {
        "old": Decimal("1"),
        "recent": Decimal("1"),
        "new": Decimal("3"),
    }
    assert values(sales_pg, code, initial.version_id) == {
        "old": Decimal("1"),
        "recent": Decimal("1"),
    }
    assert (
        restore_candidate(tmp_path / result.version_id).rows
        == normalize(code, [*old, *delta], scope(code)).rows
    )
    full = publish(sales_pg, tmp_path, code, [*old, *delta])
    assert full.version_id == result.version_id and full.reused
    before = archive_bytes(tmp_path)
    for _ in range(2):
        repeated = receive(sales_pg, tmp_path, code, delta)
        assert repeated.reused and repeated.version_id == result.version_id
    assert archive_bytes(tmp_path) == before
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT version_id FROM current_datasets WHERE dataset_code=:code"),
                {"code": code},
            )
            == result.version_id
        )
        assert (
            c.scalar(
                sa.text("SELECT count(*) FROM dataset_versions WHERE dataset_code=:code"),
                {"code": code},
            )
            == 2
        )


@pytest.mark.parametrize("code", CODES)
def test_bootstrap_gap_catchup_and_full_historical_repair(sales_pg, tmp_path, code):
    historic = "2025-10-20" if code in PROMOTION_CODES else "2026-08-01"
    old = [row(code, "old", historic), row(code, "gap", "2026-08-24")]
    windows = []
    receive(sales_pg, tmp_path, code, old, windows=windows)
    assert windows == [scope(code)]
    publish(sales_pg, tmp_path, code, old, "2026-09-01")
    windows.clear()
    receive(sales_pg, tmp_path, code, [row(code, "new", "2026-09-15")], windows=windows)
    assert windows[0].start == date(2026, 9, 2)
    repaired = [row(code, "old", historic, "9"), old[1], row(code, "new", "2026-09-15")]
    windows.clear()
    final = receive(sales_pg, tmp_path, code, repaired, full=True, windows=windows)
    assert windows == [scope(code)]
    assert values(sales_pg, code, final.version_id)["old"] == Decimal("9")
    with pytest.raises(SalesError, match="SOURCE_OLD_KEY_MISSING"):
        receive(sales_pg, tmp_path, code, repaired[1:], full=True)


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("change", ("missing", "day", "historical_move", "shop"))
def test_unexplained_missing_or_identity_moves_keep_current(sales_pg, tmp_path, code, change):
    old = [row(code, "old", "2026-08-24"), row(code, "recent", "2026-11-12")]
    initial = publish(sales_pg, tmp_path, code, old)
    if change == "missing":
        changed = [row(code, "new", "2026-11-12")]
    elif change == "day":
        changed = [row(code, "recent", "2026-10-21")]
    elif change == "historical_move":
        changed = [
            row(code, "old", "2026-11-12", shop_id="799193231", shop_name=SHOPS["799193231"][1]),
            old[1],
        ]
    else:
        changed = [
            row(code, "recent", "2026-11-12", shop_id="799193231", shop_name=SHOPS["799193231"][1])
        ]
    with pytest.raises(SalesError):
        receive(sales_pg, tmp_path, code, changed)
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT version_id FROM current_datasets WHERE dataset_code=:code"),
                {"code": code},
            )
            == initial.version_id
        )
    assert values(sales_pg, code, initial.version_id) == {
        "old": Decimal("1"),
        "recent": Decimal("1"),
    }


def test_shop_region_id_cannot_change_region(sales_pg, tmp_path):
    code = "shop_regions"
    publish(sales_pg, tmp_path, code, [row(code, "recent", "2026-11-12")])
    with pytest.raises(SalesError, match="SOURCE_KEY_MOVED"):
        receive(sales_pg, tmp_path, code, [row(code, "recent", "2026-11-12", province="江苏")])


@pytest.mark.parametrize("code", PROMOTION_CODES)
def test_2025_promotion_archive_replays(sales_pg, tmp_path, code):
    rows = [row(code, "historic", "2025-02-02")]
    result = publish(sales_pg, tmp_path, code, rows)
    assert restore_candidate(tmp_path / result.version_id) == normalize(code, rows, scope(code))


@pytest.mark.parametrize("code", CODES)
def test_stable_empty_window_preserves_history_and_requires_two_scans(sales_pg, tmp_path, code):
    from app.connectors.sales_sources import SalesSourceReader
    from app.connectors.shop_daily import ShopDailyReader
    from app.modules.sales.window_incremental import receive_sales_window

    historic = "2025-10-20" if code in PROMOTION_CODES else "2026-08-24"
    initial = publish(sales_pg, tmp_path, code, [row(code, "old", historic)])
    before = archive_bytes(tmp_path)
    scans = []

    def scan(*args):
        scans.append(args[-1])
        return []

    reader = (
        ShopDailyReader.for_test(scan) if code == "shop_daily" else SalesSourceReader.for_test(scan)
    )
    store = store_for(sales_pg, tmp_path, code)

    def read(window):
        return (
            reader.read_stable(window, allow_empty=True)
            if code == "shop_daily"
            else reader.read_stable(code, window, allow_empty=True)
        )

    result = store.run(lambda hb: receive_sales_window(store, read, heartbeat=hb))
    assert len(scans) == 2
    assert result.version_id == initial.version_id and result.reused
    assert archive_bytes(tmp_path) == before
    with pytest.raises(SalesError, match="SOURCE_NO_RECORDS"):
        store.run(lambda hb: receive_sales_window(store, read, heartbeat=hb, full=True))


@pytest.mark.parametrize("code", ("shop_daily", "shop_ratings"))
def test_reader_empty_policy_stability_and_default_rejection(code):
    from app.connectors.sales_sources import SalesSourceReader
    from app.connectors.shop_daily import ShopDailyReader

    def reader_for(scan):
        return (
            ShopDailyReader.for_test(scan)
            if code == "shop_daily"
            else SalesSourceReader.for_test(scan)
        )

    def read(reader, **kwargs):
        return (
            reader.read_stable(scope(code), **kwargs)
            if code == "shop_daily"
            else reader.read_stable(code, scope(code), **kwargs)
        )

    with pytest.raises(SalesError, match="SOURCE_NO_RECORDS"):
        read(reader_for(lambda *args: []))
    assert read(reader_for(lambda *args: []), allow_empty=True).rows == ()
    scans = iter(([], [row(code, "recent", "2026-11-12")]) * 2)
    with pytest.raises(SalesError, match="SOURCE_CHANGED_DURING_READ"):
        read(reader_for(lambda *args: next(scans)), allow_empty=True)


@pytest.mark.parametrize("code", CODES)
def test_project_runtime_ordinary_and_explicit_full_keep_approved_scope(
    sales_pg, tmp_path, monkeypatch, code
):
    from app.connectors import sales_sources, shop_daily
    from app.jobs.project_runtime import build_steps
    from app.modules.sales import source_policy
    from tests.test_project_sync_runtime import paths_for

    monkeypatch.setattr(source_policy, "approved_window", lambda code=None: scope(code))
    monkeypatch.setattr("app.modules.sales.storage.approved_window", lambda code=None: scope(code))
    paths = paths_for(tmp_path)
    root = paths.ingestion_root / code
    historic = "2025-10-20" if code in PROMOTION_CODES else "2026-08-24"
    rows = [row(code, "old", historic), row(code, "recent", "2026-11-12")]
    initial = publish(sales_pg, root, code, rows)
    windows = []

    class Reader:
        def __init__(self, **kwargs):
            self.hb = kwargs["heartbeat"]

        def close(self):
            pass

        def read_stable(self, *args, allow_empty=False):
            window = args[-1]
            windows.append(window)
            self.hb()
            return normalize(
                code,
                [r for r in rows if window.start <= date.fromisoformat(r["dt"]) <= window.end],
                window,
            )

    monkeypatch.setattr(sales_sources, "SalesSourceReader", Reader)
    monkeypatch.setattr(shop_daily, "ShopDailyReader", Reader)
    result = build_steps(sales_pg, paths, lambda: "test-only")[code]()
    assert result["version_id"] == initial.version_id
    assert windows[-1].start == date(2026, 11, 12)
    rows[0] = row(code, "old", historic, "8")
    full = build_steps(sales_pg, paths, lambda: "test-only", reconcile_sources=(code,))[code]()
    assert windows[-1] == scope(code)
    assert values(sales_pg, code, full["version_id"])["old"] == Decimal("8")
    assert values(sales_pg, code, initial.version_id)["old"] == Decimal("1")
