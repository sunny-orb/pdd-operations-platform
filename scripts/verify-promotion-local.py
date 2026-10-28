"""M2只读本地独立对账：两模式/渠道/六店/日期，GET不增任务；不访问上游。"""

import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.core.config import Settings
from app.db.connection import build_engine
from app.modules.sales.source_policy import SHOPS


def main():
    engine = build_engine(Settings(_env_file=ROOT / "apps/api/.env"))
    assert engine.url.host in {"localhost", "127.0.0.1"} and engine.url.port == 55432
    assert engine.url.database == "pdd_operations"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path, params):
        query = urllib.parse.urlencode(params)
        with opener.open(
            f"http://127.0.0.1:8000/api/v1/promotion/{path}?{query}", timeout=30
        ) as r:
            return json.load(r)

    groups = 0
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
        c.begin(),
    ):
        c.execute(sa.text("SET TRANSACTION READ ONLY"))
        before = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
        heads = dict(
            c.execute(
                sa.text("SELECT dataset_code,version_id FROM current_datasets")
            ).all()
        )
        for mode in ("store", "product"):
            meta = get("meta", {"mode": mode})
            end = date.fromisoformat(meta["data"]["date_to"])
            table = (
                "shop_channel_promotion_daily"
                if mode == "store"
                else "product_promotion_records"
            )
            field = "business_date" if mode == "store" else "period_start"
            codes = (
                ("shop_channel_product_promotion", "shop_channel_star_shop")
                if mode == "store"
                else ("product_promotion",)
            )
            source_versions = {
                s["dataset"]: s["version"] for s in meta["data"]["sources"]
            }
            assert all(
                source_versions.get(code) == heads.get(code) for code in codes
            ), "INPUT_VERSION_CHANGED"
            ids = [heads[k] for k in codes if k in heads]
            for start in (end, end - timedelta(days=29)):
                for shop in ("", *SHOPS):
                    for channel in (
                        ("", "product_promotion", "star_shop")
                        if mode == "store"
                        else ("",)
                    ):
                        scope = {
                            "mode": mode,
                            "date_from": str(start),
                            "date_to": str(end),
                            "data_version": meta["meta"]["data_version"],
                            "page": 1,
                            "page_size": 25,
                        }
                        if shop:
                            scope["shop_id"] = shop
                        if channel:
                            scope["channel"] = channel
                        actual = get("overview", scope)["data"]
                        clause = f"dataset_version_id=ANY(:ids) AND {field} BETWEEN :a AND :b"
                        params = {"ids": ids, "a": start, "b": end}
                        if shop:
                            clause += " AND source_shop_id=:shop"
                            params["shop"] = shop
                        if channel:
                            clause += " AND channel_code=:channel"
                            params["channel"] = channel
                        raw = (
                            c.execute(
                                sa.text(
                                    f"SELECT sum(spend) spend,sum(attributed_revenue) revenue,"
                                    f"sum(impressions) impressions,sum(clicks) clicks,"
                                    f"count(*) record_count FROM {table} WHERE {clause}"
                                ),
                                params,
                            )
                            .mappings()
                            .one()
                        )
                        missing, combined = 0, False
                        if mode == "store":
                            applicable = {
                                (sid, code)
                                for sid in ([shop] if shop else SHOPS)
                                for code in (
                                    [channel]
                                    if channel
                                    else ("product_promotion", "star_shop")
                                )
                                if code != "star_shop" or sid == "663599807"
                            }
                            combined = not channel and any(
                                code == "star_shop" for _, code in applicable
                            )
                            observed = set(
                                c.execute(
                                    sa.text(
                                        f"SELECT business_date,source_shop_id,channel_code "
                                        f"FROM {table} WHERE {clause}"
                                    ),
                                    params,
                                ).all()
                            )
                            assert len(observed) == raw["record_count"], "DUPLICATE_SHOP_CHANNEL_DAY"
                            expected_keys = {
                                (start + timedelta(days=offset), sid, code)
                                for offset in range((end - start).days + 1)
                                for sid, code in applicable
                            }
                            missing = len(expected_keys - observed)
                            assert actual["coverage"]["missing_count"] == missing
                        # 两来源独立；即使缺渠道日，已知金额仍应与PG小计一致并标partial。
                        for key in ("spend", "impressions", "clicks", "revenue"):
                            value = actual["metrics"][key]["value"]
                            assert (
                                Decimal(value) if value is not None else None
                            ) == raw[key], (mode, key, shop, channel)
                            if missing and value is not None:
                                assert actual["metrics"][key]["status"] == "partial"
                        for key, numerator, denominator in (
                            ("ctr", "clicks", "impressions"),
                            ("cpc", "spend", "clicks"),
                            ("roi", "attributed_revenue", "spend"),
                            ("cvr", "attributed_deals", "clicks"),
                        ):
                            pair = c.execute(
                                sa.text(
                                    f"SELECT sum({numerator}),sum({denominator}),count(*) "
                                    f"FROM {table} WHERE {clause} AND {numerator} IS NOT NULL "
                                    f"AND {denominator} IS NOT NULL"
                                ),
                                params,
                            ).one()
                            with localcontext() as ctx:
                                ctx.prec = 50
                                expected = (
                                    pair[0] / pair[1]
                                    if pair[0] is not None and pair[1] and pair[1] > 0
                                    else None
                                )
                            if mode == "store" and (
                                pair[2] != raw["record_count"]
                                or combined
                                and missing
                                and key in ("roi", "cvr")
                            ):
                                expected = None
                            value = actual["metrics"][key]["value"]
                            assert (
                                Decimal(value) if value is not None else None
                            ) == expected, (mode, key, shop, channel)
                            if combined and missing and key in ("roi", "cvr"):
                                assert (
                                    actual["metrics"][key]["status"]
                                    == "incomplete_channel_coverage"
                                )
                        if combined:
                            assert actual["net"]["net_revenue"]["value"] is None
                            assert actual["net"]["net_roi"]["value"] is None
                        groups += 1
        after = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
        assert before == after
    engine.dispose()
    result = {"status": "passed", "groups": groups, "task_count_unchanged": True}
    path = ROOT / "var/project-sync/promotion-reconciliation.json"
    path.write_text(json.dumps(result))
    path.chmod(0o600)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
