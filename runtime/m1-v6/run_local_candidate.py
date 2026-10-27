"""只读当前本地M1四输入，生成私有候选标准与观察评分；不写数据库。"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

import sqlalchemy as sa
from app.core.config import Settings
from app.db.connection import build_engine
from app.modules.product_board.repository import CODES, snapshot
from app.modules.product_board.source import windows
from app.modules.product_scoring.features import observations
from trial_baseline import build_calibration, score_observation


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def save_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as handle:
        os.chmod(path, 0o600)
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-year-ago",
        action="store_true",
        help="额外离线比较候选：纳入已批准去年对应窗；不是默认拟合范围",
    )
    args = parser.parse_args()
    engine = build_engine(Settings(_env_file=ROOT / "apps/api/.env"))
    try:
        with snapshot(engine) as repo:
            role = repo.c.execute(sa.text("SELECT current_user")).scalar_one()
            if role != "pdd_app":
                raise ValueError("仅允许既有只读pdd_app身份")
            as_of = repo.end
            approved = windows(as_of)
            ranges = approved if args.include_year_ago else approved[:1]
            product_rows, ad_rows = [], []
            for start, end in ranges:
                product_rows.extend(
                    dict(r)
                    for r in repo.c.execute(
                        sa.text(
                            "SELECT listing_id,business_date,source_shop_id,source_product_id,visitors,product_revenue,units,orders,favorites,buyers "
                            "FROM public.product_daily_facts WHERE dataset_version_id=:v AND business_date BETWEEN :s AND :e "
                            "ORDER BY business_date,source_shop_id,source_product_id"
                        ),
                        {"v": repo.params["product_daily"], "s": start, "e": end},
                    ).mappings()
                )
                ad_rows.extend(
                    dict(r)
                    for r in repo.c.execute(
                        sa.text(
                            "SELECT listing_id,period_start,source_row_key,impressions,clicks,spend,attributed_revenue,attributed_deals "
                            "FROM public.product_promotion_records WHERE dataset_version_id=:v AND period_start BETWEEN :s AND :e "
                            "ORDER BY period_start,source_row_key"
                        ),
                        {"v": repo.params["product_promotion"], "s": start, "e": end},
                    ).mappings()
                )
            profiles = repo.profiles()
            vector = {code: repo.params[code] for code in CODES}
            token = repo.token
        data = observations(product_rows, ad_rows, profiles, ranges)
        calibration = build_calibration(
            data, as_of_date=as_of, authorized_ranges=ranges
        )
        current = [r for r in data if r["window_end"] == as_of and r["level"] == "新品"]
        scores = [
            dict(score_observation(r, calibration), listing_id=r["listing_id"])
            for r in current
        ]
        output = (
            Path(__file__).with_name("outputs") / calibration["calibration_sha256"][:16]
        )
        summary = {
            "state": "candidate_only",
            "database_write": False,
            "as_of_date": as_of,
            "history_scope": "current_and_year_ago_candidate"
            if args.include_year_ago
            else "current_60d_candidate",
            "input_vector": vector,
            "data_version": token,
            "input_projection_sha256": digest(
                [
                    product_rows,
                    ad_rows,
                    {
                        lid: {name: p.get(name) for name in ("category", "level")}
                        for lid, p in profiles.items()
                    },
                ]
            ),
            "authorized_ranges": ranges,
            "profile_basis": "按当前资料归属，阶段无关",
            "product_rows": len(product_rows),
            "promotion_rows": len(ad_rows),
            "candidate_observations": len(data),
            "accepted_history_windows": calibration["accepted_windows"],
            "current_new_products": len(current),
            "score_states": dict(Counter(r["score_status"] for r in scores)),
            "current_new_products_without_category": sum(
                not r["category"] for r in current
            ),
            "current_new_products_without_complete_product_7d": sum(
                not r["complete_7d"] for r in current
            ),
            "available_standard_signals": sum(
                spec is not None
                for row in calibration["standards"].values()
                for spec in row.values()
            ),
            "calibration_sha256": calibration["calibration_sha256"],
            "scores_sha256": digest(scores),
            "computed_at": datetime.now(UTC).isoformat(),
            "limitations": calibration["limitations"]
            + ["收藏10分与完整日未知增长14分不计", "非爆款监督训练，不输出正式放量"],
        }
        save_private(output / "calibration.json", calibration)
        save_private(output / "scores.json", scores)
        save_private(output / "manifest.json", summary)
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in (
                        "state",
                        "product_rows",
                        "promotion_rows",
                        "candidate_observations",
                        "accepted_history_windows",
                        "current_new_products",
                        "score_states",
                        "available_standard_signals",
                        "calibration_sha256",
                    )
                },
                default=str,
            )
        )
        print(f"private_output={output}")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
