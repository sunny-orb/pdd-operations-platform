"""历史补齐：真实PG发布与跨来源选择，不接上游。"""

from datetime import date
from decimal import Decimal

import pytest

from app.modules.sales import promotion_history as history
from app.modules.sales.archive_replay import restore_candidate
from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import HISTORY_SPEC
from app.modules.sales.storage import ShopDailyStore

pytest_plugins = ["sales_pg_support"]


def payload():
    return [
        dict(
            shop_id=shop,
            date=day,
            source_table=table,
            record_id=f"rec-{i}" if table else None,
            spend="0" if not table else "1.25",
            revenue="0" if not table else "5.00",
            impressions="0" if not table else "100",
            clicks="0" if not table else "10",
        )
        for i, (shop, day, table) in enumerate(history.APPROVED)
    ]


def test_history_rejects_missing_duplicate_and_unapproved_rows():
    rows = payload()
    for bad in (rows[:-1], rows + [rows[0]], [dict(rows[0], date="2026-03-04"), *rows[1:]]):
        with pytest.raises(SalesError):
            history.normalize_history(bad)
    rows[-1]["spend"] = "1"
    with pytest.raises(SalesError):
        history.normalize_history(rows)


def test_warehouse_whole_row_wins_even_null_and_other_channels_survive():
    old = history.normalize_history(payload()).rows
    first = old[0]
    warehouse = [
        dict(first, spend=None, source_row_key="warehouse"),
        dict(first, spend=Decimal("3"), channel_code="star_shop", source_row_key="star"),
    ]
    effective = history.effective_rows(warehouse, old)
    assert len(effective) == 12
    same = [
        r
        for r in effective
        if r["shop_id"] == first["shop_id"] and r["business_date"] == first["business_date"]
    ]
    assert {r["source_row_key"] for r in same} == {"warehouse", "star"}
    assert next(r for r in same if r["source_row_key"] == "warehouse")["spend"] is None
    assert all(r["net_attributed_revenue"] is None and r["attributed_deals"] is None for r in old)


def test_history_pg_idempotence_replay_and_source_replacement(sales_pg, tmp_path):
    candidate = history.normalize_history(payload())
    store = ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=candidate.window, spec=HISTORY_SPEC
    )
    result = store.publish_candidate(candidate)
    assert store.publish_candidate(candidate).version_id == result.version_id
    assert restore_candidate(tmp_path / result.version_id).fingerprint == candidate.fingerprint
    from app.modules.promotion.repository import snapshot

    with snapshot(sales_pg, "store") as repo:
        assert len(repo.rows) == 11
        assert sum(r["spend"] for r in repo.rows) == Decimal("7.50")
        token = repo.token
        assert len(repo.metadata()["historical_supplements"]) == 11
        from app.modules.promotion.query import PromotionQuery, days

        query = PromotionQuery(date(2026, 2, 21), date(2026, 8, 23))
        labels = [r["source_label"] for r in days(repo, query)["items"]]
        assert labels.count("飞书") == 6
        assert labels.count("人工确认") == 5
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.source_policy import SHOPS

    row = payload()[0]
    raw = dict(
        id="warehouse-1",
        shop_id=row["shop_id"],
        shop_name=SHOPS[row["shop_id"]][1],
        dt=row["date"],
        time_granularity="day",
        metrics={"总花费(元)": "12.00"},
    )
    spec = SPECS["shop_channel_product_promotion"]
    warehouse = ShopDailyStore(
        sales_pg, archive_root=tmp_path / "warehouse", approved=candidate.window, spec=spec
    )
    warehouse.publish_candidate(normalize_source(spec.code, [raw], candidate.window))
    with snapshot(sales_pg, "store") as repo:
        assert repo.token != token
        assert len(repo.rows) == 11
        labels = [r["source_label"] for r in days(repo, query)["items"]]
        assert labels.count("数仓") == 1
        assert labels.count("飞书") + labels.count("人工确认") == 10
        assert sum(r["spend"] for r in repo.rows) == Decimal("18.25")
        assert (
            len(
                [r for r in repo.metadata()["historical_supplements"] if r["status"] == "warehouse"]
            )
            == 1
        )
        from types import SimpleNamespace

        from app.modules.sales.detail_queries import scoped_rows

        s = dict(connection=repo.c, versions={k: v["id"] for k, v in repo.versions.items()})
        rows = scoped_rows(
            s,
            spec.code,
            SimpleNamespace(date_from=date(2026, 2, 1), date_to=date(2026, 8, 31)),
            [v[0] for v in SHOPS.values()],
        )
        assert sum(r["spend"] for r in rows) == Decimal("18.25")


def test_history_status_dates_and_failed_revision_keep_previous(sales_pg, tmp_path):
    import sqlalchemy as sa

    from app.modules.promotion.repository import snapshot

    candidate = history.normalize_history(payload())
    store = ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=candidate.window, spec=HISTORY_SPEC
    )
    first = store.publish_candidate(candidate)
    rows = payload()
    rows[0]["spend"] = "2.25"
    changed = history.normalize_history(rows)

    def fail(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO public.shop_channel_promotion_daily"):
            raise RuntimeError("failed history insert")

    sa.event.listen(sales_pg, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="failed history insert"):
            store.publish_candidate(changed)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", fail)
    with snapshot(sales_pg, "store") as repo:
        assert repo.versions[HISTORY_SPEC.code]["id"] == first.version_id
        recent = history.supplement_audit([], repo.history, date(2026, 9, 1), date(2026, 10, 29))
        assert len(recent) == 11
        assert {r["status"] for r in recent} == {"outside_range"}
        one_shop = history.supplement_audit([], repo.history, shops=["spifs"])
        assert len([r for r in one_shop if r["status"] == "supplement"]) == 1


def test_promotion_history_audit_follows_date_shop_and_channel(sales_pg, tmp_path):
    from app.modules.promotion.query import PromotionQuery, overview
    from app.modules.promotion.repository import snapshot

    candidate = history.normalize_history(payload())
    ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=candidate.window, spec=HISTORY_SPEC
    ).publish_candidate(candidate)
    with snapshot(sales_pg, "store") as repo:
        q = PromotionQuery(date(2026, 8, 1), date(2026, 8, 23), shop="191467219")
        audit = overview(repo, q)["historical_supplements"]
        assert len(audit) == 11
        assert len([r for r in audit if r["status"] == "supplement"]) == 1
        from dataclasses import replace

        audit = overview(repo, replace(q, channel="star_shop"))["historical_supplements"]
        assert {r["status"] for r in audit} == {"outside_range"}
