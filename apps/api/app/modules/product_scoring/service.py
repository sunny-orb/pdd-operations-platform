"""从固定RR四输入生成当期60日试行标准和评分，不接上游或写库。"""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

from .features import observations
from .model import FEATURES, build_calibration, score_observation
from .rules import parameters

METRIC_CODES = dict(
    zip(
        FEATURES,
        (
            "visitors_7d",
            "product_revenue_7d",
            "units_7d",
            "favorites_7d",
            "score_conversion_rate_7d",
            "revenue_per_visitor_7d",
            "ad_roi_7d",
            "ad_click_through_rate_7d",
            "ad_click_conversion_rate_7d",
            "revenue_multiple_7d",
        ),
        strict=True,
    )
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def producer_hash():
    return digest(
        {
            name: Path(__file__).with_name(name).read_text()
            for name in ("model.py", "features.py", "service.py", "rules.py", "publisher.py")
        }
    )


def build_bundle(repo):
    """输入固定且相容的四版本Repository，输出全量新品及独立标准；不读取去年训练。"""
    start, end = repo.end - timedelta(days=59), repo.end
    profiles = repo.profiles()
    rows = observations(
        repo.rows(start, end), repo.rows(start, end, ads=True), profiles, [(start, end)]
    )
    calibration = build_calibration(rows, as_of_date=end, authorized_ranges=[(start, end)])
    current = sorted(
        (r for r in rows if r["window_end"] == end and r["level"] == "新品"),
        key=lambda r: r["listing_id"],
    )
    scores = [
        dict(
            score_observation(row, calibration),
            listing_id=row["listing_id"],
            raw_features=row["raw_features"],
            category=row["category"],
        )
        for row in current
    ]
    return dict(
        inputs={
            code: repo.params[code]
            for code in ("product_daily", "product_promotion", "profile_records", "profile_links")
        },
        start=start,
        end=end,
        calibration=calibration,
        scores=scores,
        parameters=parameters(),
        producer=producer_hash(),
    )


def validate_bundle(bundle):
    """封版前校验完整K/十项/权重/候选门控；失败不得推进当前入口。"""
    if bundle["parameters"] != parameters():
        raise ValueError("SCORING_RULE_PACKAGE_INVALID")
    seen = set()
    for row in bundle["scores"]:
        key = (row["platform_code"], row["shop_id"], row["product_id"])
        if key in seen or set(row["signals"]) != set(FEATURES):
            raise ValueError("SCORE_IDENTITY_OR_SIGNALS_INVALID")
        seen.add(key)
        if (
            row["pending_weight"] < 24
            or row["complete_score"] is not None
            or row["formal_scaling_eligible"]
        ):
            raise ValueError("TRIAL_GATES_BYPASSED")
        if sum(r["weight"] for r in row["signals"].values()) != 100:
            raise ValueError("SCORE_WEIGHTS_INVALID")
