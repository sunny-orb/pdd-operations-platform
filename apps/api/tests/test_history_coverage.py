"""近30天覆盖真实PG；无上游请求，合成数据只进入随机隔离库。"""

from datetime import date, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.modules.sales.contracts import ReadWindow
from app.modules.sales.normalization import normalize_shop_daily
from app.modules.sales.source_policy import SHOPS
from app.modules.sales.storage import ShopDailyStore
from tests.test_sales_source import raw_row

pytest_plugins = ["tests.sales_pg_support"]
NOW = datetime.fromisoformat("2026-11-10T10:00:00+08:00")
START, END = date(2026, 10, 11), date(2026, 11, 9)


def publish(engine, path, missing=True):
    window = ReadWindow(START, END, tuple(SHOPS))
    rows = [
        raw_row(
            id=f"{sid}-{i}",
            dt=str(START + timedelta(days=i)),
            shop_id=sid,
            shop_name=value[1],
            metrics={
                "成交金额": "0",
                "店铺访客数": "0",
                "成交买家数": "0",
                "成交订单数": "0",
                "商品浏览量": None,
            },
        )
        for sid, value in SHOPS.items()
        for i in range(30)
        if not (missing and sid == "663599807" and i in (10, 29))
    ]
    return ShopDailyStore(engine, archive_root=path, approved=window).publish_candidate(
        normalize_shop_daily(rows, window)
    )


def seed_checkpoint(engine):
    with engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO source_checkpoints"
                "(source_id,checked_at,check_status,observed_fingerprint) "
                "VALUES ('shop_daily',:now,'succeeded','fixture')"
            ),
            {"now": NOW},
        )


def test_historical_holes_reuse_sales_and_recover_only_after_recheck(sales_pg, tmp_path):
    from app.modules.data_status.history_coverage import history_status, refresh_history_coverage
    from app.modules.data_status.repository import versions
    from app.modules.sales.query import aggregate

    publish(sales_pg, tmp_path)
    seed_checkpoint(sales_pg)
    with sales_pg.connect() as c:
        refresh_history_coverage(c, now=NOW)
        saved = c.scalar(sa.text("SELECT platform_snapshot->'history' FROM source_checkpoints"))
        heads = versions(c)
        rows = list(c.execute(sa.text("SELECT * FROM shop_daily_facts")).mappings())
        missing = aggregate(rows, START, END, [v[0] for v in SHOPS.values()])["coverage"][
            "missing_dates"
        ]
        assert saved["missing_count"] == len(missing) == 2
        assert saved["groups"] == [
            {"shop_id": "spigf", "shop_name": "官方店", "dates": ["2026-10-21", "2026-11-09"]}
        ]
        assert history_status("shop_daily", saved, heads, NOW)["state"] == "gaps"
        assert (
            history_status("shop_daily", saved, heads, NOW + timedelta(days=1))["state"] == "stale"
        )
        c.rollback()
        # 同版本同窗口不重新扫经营事实。
        scanned = []

        def track(conn, cursor, statement, parameters, context, executemany):
            if "FROM public.shop_daily_facts" in statement:
                scanned.append(statement)

        sa.event.listen(sales_pg, "before_cursor_execute", track)
        refresh_history_coverage(c, now=NOW)
        sa.event.remove(sales_pg, "before_cursor_execute", track)
        assert not scanned
    publish(sales_pg, tmp_path, missing=False)
    with sales_pg.connect() as c:
        assert history_status("shop_daily", saved, versions(c), NOW)["state"] == "stale"
        c.rollback()
        refresh_history_coverage(c, now=NOW)
        complete = c.scalar(sa.text("SELECT platform_snapshot->'history' FROM source_checkpoints"))
        assert complete["state"] == "observed"
        assert complete["missing_count"] == 0  # NULL和真实0均是存在的记录。


def test_check_failure_keeps_previous_summary_and_does_not_fail_sync(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.data_status import history_coverage as h

    publish(sales_pg, tmp_path)
    seed_checkpoint(sales_pg)
    with sales_pg.connect() as c:
        h.refresh_history_coverage(c, now=NOW)

        def broken(*args, **kwargs):
            raise RuntimeError("private details must not escape")

        monkeypatch.setattr(h, "collect_coverage", broken)
        h.refresh_history_coverage(c, now=NOW + timedelta(days=1))
        row = c.execute(sa.text("SELECT * FROM source_checkpoints")).mappings().one()
        assert row["check_status"] == "succeeded"
        assert row["platform_snapshot"]["history"]["state"] == "failed"
        assert row["platform_snapshot"]["history"]["missing_count"] == 2
        assert "private" not in str(row)


def test_unknown_sources_are_not_reported_complete_and_get_does_not_scan(sales_pg):
    from app.modules.data_status.history_coverage import history_status
    from app.modules.data_status.repository import read_status

    assert history_status("jst_returns", None, {}, NOW)["state"] == "unverified"
    assert history_status("profile_records", None, {}, NOW)["state"] == "not_applicable"
    assert history_status("shop_daily", None, {}, NOW)["state"] == "unavailable"
    statements = []

    def track(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(sales_pg, "before_cursor_execute", track)
    data = read_status(sales_pg)["data"]
    sa.event.remove(sales_pg, "before_cursor_execute", track)
    assert "history" in data["sources"][0]
    assert not any("shop_daily_facts" in s for s in statements)


@pytest.mark.parametrize("shared", [False, True])
def test_extended_sources_reuse_scopes_and_product_region_exclusions(
    sales_pg, tmp_path, monkeypatch, shared
):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(shared).lower())
    from app.modules.data_status.history_coverage import collect_coverage
    from app.modules.data_status.repository import versions
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from tests.test_sales_extended_sources import sample, window

    now = datetime.fromisoformat("2026-10-27T10:00:00+08:00")
    for code in (
        "shop_ratings",
        "shop_regions",
        "product_regions",
        "shop_refunds",
        "shop_channel_product_promotion",
        "shop_channel_star_shop",
    ):
        rows = [sample(code)]
        if code == "product_regions":
            rows.append(sample(code, id="excluded", dt="2026-10-25", product_category="包材"))
        store = ShopDailyStore(
            sales_pg, archive_root=tmp_path / code, approved=window(), spec=SPECS[code]
        )
        store.publish_candidate(normalize_source(code, rows, window()))
        with sales_pg.connect() as c:
            summary = collect_coverage(c, code, versions(c), now)
            assert summary["missing_count"] == (30 if code == "shop_channel_star_shop" else 180) - 1
            if code == "shop_channel_star_shop":
                assert [g["shop_id"] for g in summary["groups"]] == ["spigf"]


def test_product_scope_is_shop_day_not_each_product(sales_pg, tmp_path):
    from app.modules.data_status.history_coverage import collect_coverage
    from app.modules.data_status.repository import versions
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_scoring_publication import inputs

    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: inputs())
    with sales_pg.connect() as c:
        result = collect_coverage(
            c, "product_daily", versions(c), datetime.fromisoformat("2026-10-27T10:00:00+08:00")
        )
        observed = c.scalar(
            sa.text(
                "SELECT count(DISTINCT (source_shop_id,business_date)) FROM product_daily_facts"
            )
        )
        assert result["missing_count"] == 180 - observed


def test_effective_history_and_zero_declarations_count_as_received(sales_pg, tmp_path):
    from app.modules.data_status.history_coverage import collect_coverage, history_status
    from app.modules.data_status.repository import versions
    from app.modules.sales.datasets import HISTORY_SPEC
    from app.modules.sales.promotion_history import normalize_history
    from tests.test_promotion_history import payload

    candidate = normalize_history(payload())
    ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=candidate.window, spec=HISTORY_SPEC
    ).publish_candidate(candidate)
    now = datetime.fromisoformat("2026-08-24T10:00:00+08:00")
    with sales_pg.connect() as c:
        heads = versions(c)
        result = collect_coverage(c, "shop_channel_product_promotion", heads, now)
        count = sum(
            date(2026, 7, 25) <= r["business_date"] <= date(2026, 8, 23) for r in candidate.rows
        )
        assert result["missing_count"] == 180 - count
        assert result["inputs"].keys() == {HISTORY_SPEC.code}
        assert history_status("shop_channel_product_promotion", result, {}, now)["state"] == "stale"


def test_summary_write_failure_is_bounded_and_does_not_escape(sales_pg, tmp_path):
    from app.modules.data_status.history_coverage import refresh_history_coverage

    publish(sales_pg, tmp_path)
    seed_checkpoint(sales_pg)

    def fail_save(conn, cursor, statement, parameters, context, executemany):
        if "jsonb_set" in statement:
            raise RuntimeError("save unavailable")

    sa.event.listen(sales_pg, "before_cursor_execute", fail_save)
    try:
        with sales_pg.connect() as c:
            refresh_history_coverage(c, now=NOW)
            assert not c.in_transaction()
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail_save)


def test_legacy_monitor_does_not_overwrite_sources_outside_its_scope(sales_pg, tmp_path):
    from app.jobs.source_monitor import run_monitor
    from app.modules.data_status.history_coverage import refresh_history_coverage

    publish(sales_pg, tmp_path)
    seed_checkpoint(sales_pg)
    with sales_pg.connect() as c:
        refresh_history_coverage(c, now=NOW, warehouse_times=("13:10",))
    run_monitor(sales_pg, {}, lambda groups, c: {}, now=NOW + timedelta(days=1))
    with sales_pg.connect() as c:
        saved = c.scalar(sa.text("SELECT platform_snapshot->'history' FROM source_checkpoints"))
        assert saved["checked_through"] == "2026-11-09"


def test_product_history_coverage_preserves_shared_version_rows(sales_pg, tmp_path, monkeypatch):
    from app.modules.data_status.history_coverage import collect_coverage
    from app.modules.data_status.repository import versions
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_board_storage import candidate

    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: candidate())
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM product_daily_facts")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_daily_facts")) == 1
        coverage = collect_coverage(c, "product_daily", versions(c), NOW)
    assert coverage["missing_count"] == len(SHOPS) * 30 - 1
    official = next(group for group in coverage["groups"] if group["shop_id"] == "spigf")
    assert "2026-10-26" not in official["dates"]
    assert len(official["dates"]) == 29


@pytest.mark.parametrize("state", ["gaps", "stale", "failed"])
def test_saved_history_names_follow_shop_identity_without_rewriting_snapshot(sales_pg, state):
    import copy
    import json

    from app.modules.data_status.history_coverage import binding, history_status
    from app.modules.data_status.repository import read_status

    saved = {
        **binding("shop_daily", {}, NOW),
        "state": state,
        "checked_at": NOW.isoformat(),
        "missing_count": 3,
        "groups": [
            {"shop_id": "spitztx", "shop_name": "童装童鞋店", "dates": ["2026-11-09"]},
            {"shop_id": "spigf", "shop_name": "官方店", "dates": ["2026-11-09"]},
            {"shop_id": "unknown", "shop_name": "未知店铺", "dates": ["2026-11-09"]},
        ],
    }
    if state == "stale":
        saved["window_end"] = "2026-11-08"
    original = copy.deepcopy(saved)
    result = history_status("shop_daily", saved, {}, NOW)
    assert result["groups"][0]["shop_name"] == "青少年服饰店"
    expected = copy.deepcopy(original)
    expected["groups"][0]["shop_name"] = "青少年服饰店"
    assert result == expected
    assert saved == original

    seed_checkpoint(sales_pg)
    with sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE source_checkpoints SET platform_snapshot=CAST(:snapshot AS jsonb)"),
            {"snapshot": json.dumps({"history": saved})},
        )
    data = read_status(sales_pg)["data"]
    source = next(row for row in data["sources"] if row["id"] == "shop_daily")
    assert source["history"]["groups"][0]["shop_name"] == "青少年服饰店"
    assert source["history"]["checked_at"] == saved["checked_at"]
    assert source["history"]["missing_count"] == 3
    with sales_pg.connect() as c:
        stored = c.scalar(sa.text("SELECT platform_snapshot->'history' FROM source_checkpoints"))
    assert stored == original
