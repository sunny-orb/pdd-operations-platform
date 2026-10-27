"""V6 trial: historical tiers, fixed weights, explicit unverified evidence."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

VERSION = "2026-09-24-v6-four-tiers-trial"
FEATURES = {"uv_7": 12, "gmv_7": 18, "items_7": 12, "fav_7": 10,
            "cvr_7": 8, "uv_value_7": 10, "ad_roi_7": 8,
            "ad_ctr_7": 4, "ad_cvr_7": 4, "gmv_growth_7": 14}
AD_FEATURES = {"ad_roi_7", "ad_ctr_7", "ad_cvr_7"}
QUANTILES = [0.25, 0.50, 0.75, 0.90]
GRADES = ["未达基础", "基础", "良好", "优秀", "突出"]
OVERRIDES = {"冲锋衣": "软壳衣"}
PATH = Path(__file__).resolve().parents[1] / "data/new_product_tiers.json"
EXTRA_COLUMNS = ["verified_score", "pending_weight", "verified_weight", "complete_score", "score_status",
                 "active_signals", "pending_signals", "growth_base_floor", "gmv_prev7", "orders_prev7"]
for _feature in FEATURES:
    EXTRA_COLUMNS += [f"tier_{i}_{_feature}" for i in range(1, 5)]
    EXTRA_COLUMNS += [f"{p}_{_feature}" for p in ("points", "grade", "active", "reason", "reference", "reference_n")]


def key(values):
    return "|".join(map(str, values))


def number(value):
    try:
        value = float(value)
        return value if np.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


def tables(frame, feature, qs=QUANTILES):
    result = {}
    for level, groups in [("cell", ["category", "season", "lifecycle"]),
                          ("parent", ["category", "lifecycle"]), ("fallback", ["lifecycle"])]:
        result[level] = {}
        # Each product contributes once per context, not one vote per weekly observation.
        values = frame.groupby(groups + ["product_id"], observed=True)[feature].median().reset_index()
        for group, part in values.groupby(groups, observed=True):
            group = group if isinstance(group, tuple) else (group,)
            s = pd.to_numeric(part[feature], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            if len(s):
                result[level][key(group)] = {"bands": s.quantile(qs).tolist(), "n": len(s)}
    s = frame.groupby("product_id")[feature].median().replace([np.inf, -np.inf], np.nan).dropna()
    result["global"] = {"bands": s.quantile(qs).tolist(), "n": len(s)} if len(s) else None
    return result


def lookup(table, category, season, lifecycle):
    base = table["fallback"].get(str(lifecycle), table["global"])
    if not base or base["n"] < 12:
        return None, "历史有效样本不足", 0
    value = np.array(base["bands"], dtype=float)
    reference = f"全品类 × {lifecycle}回退"
    n = base["n"]
    parent = table["parent"].get(key([category, lifecycle]))
    if parent:
        w = min(parent["n"] / 24, 1)
        value = w * np.array(parent["bands"]) + (1 - w) * value
        reference = f"{category} × {lifecycle}" + ("（与全品类回退加权）" if w < 1 else "")
        n = parent["n"]
    cell = table["cell"].get(key([category, season, lifecycle]))
    if cell:
        w = min(cell["n"] / 12, 1)
        value = w * np.array(cell["bands"]) + (1 - w) * value
        reference = f"{category} × {season} × {lifecycle}" + ("（小样本加权回退）" if w < 1 else "")
        n = cell["n"]
    return np.maximum.accumulate(value), reference, n


def fit(train):
    positive = train[train.hit & train.future_gmv_28.gt(0) & train.future_gmv_28_threshold.gt(0)].copy()
    calibration = {"version": VERSION, "quantiles": QUANTILES, "weights": FEATURES,
                   "growth_min_previous_orders": 5, "minimum_impressions": 2000,
                   "roi_category_overrides": OVERRIDES, "tables": {},
                   "train_observation_end": str(train.date.max().date()),
                   "train_result_end": "2025-12-31", "positive_products": int(positive.product_id.nunique()),
                   "policy": "四档取历史爆款样本P25/P50/P75/P90，同组每款先取中位数；基础以下0分，各档25/50/75/100%权重。完整总分评级未校准，不生成放量结论。"}
    calibration["growth_floor"] = tables(positive[positive.gmv_prev7.gt(0)], "gmv_prev7", [0.25])
    for feature in FEATURES:
        pool = positive.copy()
        if feature in AD_FEATURES:
            pool = pool[pool.ad_impressions_7.ge(2000)]
        pool = pool[pd.to_numeric(pool[feature], errors="coerce").gt(0)]
        if feature == "gmv_growth_7":
            valid = []
            for _, row in pool.iterrows():
                floor, _, _ = lookup(calibration["growth_floor"], row.category, row.season, row.lifecycle)
                valid.append(floor is not None and row.orders_prev7 >= 5 and row.gmv_prev7 >= floor[0] and row.gmv_growth_7 > 1)
            pool = pool.loc[valid]
        calibration["tables"][feature] = tables(pool, feature)
    return calibration


def score_frame(frame, calibration=None):
    calibration = calibration or json.loads(PATH.read_text(encoding="utf-8"))
    output = []
    for _, row in frame.iterrows():
        record = row.to_dict()
        cat, season, life = str(row.category), str(row.season), str(row.lifecycle)
        floor, _, _ = lookup(calibration["growth_floor"], cat, season, life)
        record["growth_base_floor"] = float(floor[0]) if floor is not None else np.nan
        score, pending, count, met = 0.0, 0, 0, 0
        for feature, weight in FEATURES.items():
            source_cat = OVERRIDES.get(cat, cat) if feature == "ad_roi_7" else cat
            bands, reference, n = lookup(calibration["tables"][feature], source_cat, season, life)
            value = number(row.get(feature))
            reason = ""
            if feature in AD_FEATURES and not (number(row.get("ad_impressions_7")) >= 2000):
                reason = "近7日曝光不足2,000，待推广验证"
            elif bands is None:
                reason = "历史有效样本不足，暂不定档"
            elif not np.isfinite(value):
                reason = "指标或分母缺失，待验证"
            elif feature == "ad_roi_7" and number(row.get("roi_invalid_7", 0)) > 0:
                reason = "窗口存在花费或成交额缺失，待核对"
            elif feature == "ad_ctr_7" and number(row.get("ctr_invalid_7", 0)) > 0:
                reason = "点击或曝光字段不完整，待核对"
            elif feature == "ad_cvr_7" and number(row.get("ad_cvr_invalid_7", 0)) > 0:
                reason = "点击或成交笔数字段不完整，待核对"
            elif feature == "gmv_growth_7":
                if not (number(row.get("age_days")) >= 14):
                    reason = "不足两个完整7天周期"
                elif not (number(row.get("orders_prev7")) >= 5 and floor is not None and number(row.get("gmv_prev7")) >= floor[0]):
                    reason = "前7日订单不足5单或成交额低于历史基数线"
            active = not reason
            tier = 0
            if active and value > 0:
                tier = sum(value >= float(band) for band in bands)
                if feature == "gmv_growth_7" and value <= 1:
                    tier = 0
                    reason = "成交未增长，不计增长分"
            points = weight * tier / 4 if active else 0.0
            score += points
            pending += 0 if active else weight
            count += int(active)
            met += int(active and tier > 0)
            record.update({f"points_{feature}": points if active else np.nan,
                f"grade_{feature}": GRADES[tier] if active else "待验证",
                f"active_{feature}": int(active), f"reason_{feature}": reason,
                f"reference_{feature}": reference, f"reference_n_{feature}": n,
                f"threshold_{feature}": bands[0] if bands is not None else np.nan})
            for i in range(4):
                record[f"tier_{i+1}_{feature}"] = bands[i] if bands is not None else np.nan
        record.update({"score": score, "verified_score": score, "verified_weight": 100-pending,
            "pending_weight": pending, "complete_score": score if pending == 0 else np.nan,
            "score_status": "complete" if pending == 0 else "pending",
            "active_signals": count, "pending_signals": 10-count, "met_signals": met,
            "position": "完整评分（试行）" if pending == 0 else "待推广验证" if number(row.get("ad_impressions_7")) < 2000 else "待补验证",
            "roi_threshold_category": OVERRIDES.get(cat, cat),
            "ad_status": "曝光达标（≥2,000）" if number(row.get("ad_impressions_7")) >= 2000 else "曝光不足2,000，推广待验证"})
        output.append(record)
    return pd.DataFrame(output)
