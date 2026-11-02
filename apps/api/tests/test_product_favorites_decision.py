"""M1-01反例：累计日新增不扣取消、不跨日去重；缺日/NULL仍阻断比较和评分。"""

from datetime import date, timedelta
from decimal import Decimal as D

import sqlalchemy as sa

from app.modules.product_board.query import BoardQuery, overview, score_cards
from app.modules.product_board.repository import snapshot
from app.modules.product_board.source import normalize
from app.modules.product_board.storage import ProductBoardStore
from app.modules.product_scoring.publisher import publish_scores
from tests.test_product_scoring_publication import inputs

pytest_plugins = ["tests.sales_pg_support"]


def test_pg_daily_additions_compare_without_cancellation_or_cross_day_dedup(sales_pg, tmp_path):
    end, rows = inputs()
    raw = []
    for pid in ("1", "2", "3", "4"):
        for offset, value in enumerate((3, 3, 2, 2)):
            if pid == "2" and offset == 1:
                continue
            raw.append(
                dict(
                    z_id=f"{pid}-{offset}",
                    shop_id="663599807",
                    spu_id=pid,
                    dt=str(end - timedelta(days=offset)),
                    time_granularity="day",
                    favorite_product_users=None
                    if pid == "3" and offset == 1
                    else 0
                    if pid == "4"
                    else value,
                    # 来源日新增已含同日取消；取消量不属于获准公式，重复日值也不能去重。
                    favorite_cancel_users=2,
                )
            )
        for offset in (0, 1):
            raw.append(
                dict(
                    z_id=f"{pid}-year-{offset}",
                    shop_id="663599807",
                    spu_id=pid,
                    dt=str(date(2025, 10, 26) - timedelta(days=offset)),
                    time_granularity="day",
                    favorite_product_users=1,
                )
            )
    rows["product_daily"] = normalize("product_daily", raw, end)
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, rows))
    with snapshot(sales_pg) as repo:
        ids = {r["source_product_id"]: r["listing_id"] for r in repo.rows(end, end)}

        def query(pid):
            return overview(repo, BoardQuery(end - timedelta(days=1), end, products=(ids[pid],)))

        metric = query("1")["metrics"]["favorites"]
        assert metric["value"] == D("6")
        assert metric["previous"] == D("2")
        assert metric["previous_period"] == D("4")
        assert metric["yoy"] == D("2") and metric["mom"] == D(".5")
        assert metric["status"] == "observed"
        assert query("4")["metrics"]["favorites"]["value"] == 0
        for pid in ("2", "3"):
            missing = query(pid)["metrics"]["favorites"]
            assert missing["value"] == D("3")
            assert missing["status"] == "partial"
            if pid == "2":
                # 无记录的日期两边一起排除；仍比较共同日期的新增收藏。
                assert missing["yoy"] == D("2") and missing["mom"] == D(".5")
            else:
                # 有记录但收藏值为空，不能把该行当0或静默丢掉。
                assert missing["yoy"] is None and missing["mom"] is None


def test_pg_trial_rebuild_uses_same_daily_additions_and_keeps_other_gates(sales_pg, tmp_path):
    end, rows = inputs()
    for row in rows["product_daily"]:
        pid = row["source_product_id"]
        row["favorites"] = 0 if pid == "1" else int(pid)
        if pid == "2" and row["business_date"] == end:
            row["favorites"] = None
    rows["product_daily"] = [
        r
        for r in rows["product_daily"]
        if not (r["source_product_id"] == "3" and r["business_date"] == end - timedelta(days=1))
    ]
    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: (end, rows))
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    with snapshot(sales_pg) as repo:
        cards = score_cards(repo, BoardQuery(repo.start, repo.end, size=100))
        bypid = {item["product_id"]: item for item in cards["items"]}

        def favorite(pid):
            return next(s for s in bypid[pid]["signals"] if s["code"] == "favorites_7d")

        assert favorite("4")["value"] is not None
        assert D(favorite("4")["value"]) == D("28")
        assert favorite("4")["status"] == "trial_valid"
        assert favorite("4")["points"] is not None
        assert favorite("4")["sample_count"] == "12"
        assert favorite("1")["points"] == D("0")
        for pid in ("2", "3"):
            assert favorite(pid)["value"] is None
            assert favorite(pid)["points"] is None
        assert bypid["4"]["pending_weight"] == "14"
        assert bypid["4"]["complete_score"] is None
        assert bypid["4"]["decision"] == "pending_validation"
    with sales_pg.connect() as c:
        assert (
            c.execute(
                sa.text(
                    "SELECT count(*) FROM product_score_items "
                    "WHERE metric_code='favorites_7d' AND signal_status='valid'"
                )
            ).scalar_one()
            == 10
        )


def test_pg_changed_scoring_definition_hides_old_result_until_rebuilt(
    sales_pg, tmp_path, monkeypatch
):
    from app.modules.product_scoring import publisher, service

    ProductBoardStore(sales_pg, tmp_path / "source").run(lambda hb: inputs())
    current = service.producer_hash
    monkeypatch.setattr(service, "producer_hash", lambda: "legacy-favorite-definition")
    monkeypatch.setattr(publisher, "producer_hash", lambda: "legacy-favorite-definition")
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    monkeypatch.setattr(service, "producer_hash", current)
    monkeypatch.setattr(publisher, "producer_hash", current)
    with snapshot(sales_pg) as repo:
        page = score_cards(repo, BoardQuery(repo.start, repo.end))
        assert page["status"] == "pending_recompute"
        assert all(item["score"] is None for item in page["items"])
    publish_scores(sales_pg, archive_root=tmp_path / "scores")
    with snapshot(sales_pg) as repo:
        assert score_cards(repo, BoardQuery(repo.start, repo.end))["status"] == "trial_active"
