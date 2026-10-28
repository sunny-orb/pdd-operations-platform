"""阶段无关候选标准与Decimal评分；不连接数据源，不发布正式评分。"""

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

VERSION = "m1-stage-independent-candidate-20261028-v2-daily-favorite-additions"
# 来源viral_tiers.py固定十项权重；新规则只改变参照范围及回退，不改变权重。
FEATURES = {
    "uv_7": 12,
    "gmv_7": 18,
    "items_7": 12,
    "fav_7": 10,
    "cvr_7": 8,
    "uv_value_7": 10,
    "ad_roi_7": 8,
    "ad_ctr_7": 4,
    "ad_cvr_7": 4,
    "gmv_growth_7": 14,
}
AD_FEATURES = {"ad_roi_7", "ad_ctr_7", "ad_cvr_7"}


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value):
    return format(value.normalize(), "f") if value else "0"


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def _key(row):
    key = tuple(row.get(field) for field in ("platform_code", "shop_id", "product_id"))
    if any(not isinstance(value, str) or not value for value in key):
        raise ValueError("完整身份必须是非空字符串")
    return key


def _quantile(values, fraction):
    """线性插值分位；每个输入值必须已代表一个K的中位数。"""
    values = sorted(values)
    position = Decimal(len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _eligible(row, feature, favorites_additive):
    if row.get("complete_ad_7d" if feature in AD_FEATURES else "complete_7d") is not True:
        return False
    value = _number(row.get(feature))
    if value is None:
        return False
    if feature == "fav_7" and not favorites_additive:
        return False
    if feature in AD_FEATURES:
        impressions = _number(row.get("ad_impressions_7"))
        quality = {
            "ad_roi_7": "roi_invalid_7",
            "ad_ctr_7": "ctr_invalid_7",
            "ad_cvr_7": "ad_cvr_invalid_7",
        }[feature]
        if impressions is None or impressions < 2000 or row.get(quality) != 0:
            return False
    if feature == "gmv_growth_7":
        previous, orders = (
            _number(row.get("gmv_prev7")),
            _number(row.get("orders_prev7")),
        )
        if (
            row.get("complete_14d") is not True
            or row.get("source_complete_14d") is not True
            or previous is None
            or previous <= 0
            or orders is None
            or orders < 5
        ):
            return False
    return True


def build_calibration(observations, *, as_of_date, authorized_ranges, favorites_additive=False):
    """输入完整K的已校验7日观察行，输出可JSON保存的新候选参照，不使用未来标签。

    仅纳入批准范围内且窗口结束≤T−14的观察；每K先中位数，品类≥12，
    否则全品类≥24硬回退。来源完整性未知时增长项不得参与。
    """
    as_of_date = _day(as_of_date)
    cutoff = as_of_date - timedelta(days=14)
    ranges = [(_day(start), _day(end)) for start, end in authorized_ranges]
    if not ranges or any(end < start for start, end in ranges):
        raise ValueError("必须给出有效批准范围")
    rows = list(observations)
    pools = defaultdict(lambda: defaultdict(list))
    floor_pools = defaultdict(lambda: defaultdict(list))
    categories = set()
    seen = set()
    context_by_key = {}
    accepted_windows = 0
    with localcontext() as context:
        context.prec = 50
        for row in rows:
            key = _key(row)
            end = _day(row["window_end"])
            identity = key + (end,)
            if identity in seen:
                raise ValueError("重复的店铺商品观察窗口")
            seen.add(identity)
            category = str(row.get("category") or "")
            categories.add(category)
            previous_category = context_by_key.setdefault(key, category)
            if previous_category != category:
                raise ValueError("同K模型品类冲突")
            if (
                not category
                or end > cutoff
                or row.get("complete_7d") is not True
                or not any(
                    start <= end - timedelta(days=6) <= end <= last for start, last in ranges
                )
            ):
                continue
            accepted_windows += 1
            for feature in FEATURES:
                if not _eligible(row, feature, favorites_additive):
                    continue
                if feature == "gmv_growth_7" and not any(
                    start <= end - timedelta(days=13) <= end <= last for start, last in ranges
                ):
                    continue
                value = _number(row[feature])
                pools[(category, feature)][key].append(value)
                if feature == "gmv_growth_7":
                    floor_pools[category][key].append(_number(row["gmv_prev7"]))

        def make_spec(category, feature, source):
            if not category:
                return None
            local = source.get((category, feature), {})
            if len(local) >= 12:
                selected, reference = local, category
            else:
                selected = {}
                for (_group_category, group_feature), values in source.items():
                    if group_feature == feature:
                        selected.update(values)
                reference = "全品类"
                if len(selected) < 24:
                    return None
            medians = [_quantile(values, Decimal("0.5")) for values in selected.values()]
            quantiles = (
                [Decimal("0.25")]
                if feature == "growth_floor"
                else [Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("0.9")]
            )
            return {
                "bands": [_text(_quantile(medians, q)) for q in quantiles],
                "n": len(medians),
                "reference": reference,
            }

        standards = {
            category: {feature: make_spec(category, feature, pools) for feature in FEATURES}
            for category in sorted(categories)
        }
        floor_source = {
            (category, "growth_floor"): values for category, values in floor_pools.items()
        }
        floors = {
            category: make_spec(category, "growth_floor", floor_source)
            for category in sorted(categories)
        }
    output = {
        "algorithm_version": VERSION,
        "state": "candidate",
        "as_of_date": as_of_date.isoformat(),
        "history_cutoff": cutoff.isoformat(),
        "authorized_ranges": [[a.isoformat(), b.isoformat()] for a, b in ranges],
        "sample_unit": "platform_code/shop_id/product_id先取窗口中位数",
        "quantile_method": "linear",
        "minimum_category_samples": 12,
        "minimum_global_samples": 24,
        "fallback": "hard",
        "favorites_additive": favorites_additive,
        "favorites_definition": "daily_additions_without_cancellation_deduction"
        if favorites_additive
        else "unconfirmed",
        "accepted_windows": accepted_windows,
        "standards": standards,
        "growth_floors": floors,
        "limitations": [
            "阶段无关分布参照，不是历史爆款标签模型",
            "总分与放量结论未校准",
            "来源完整性和归因仍独立门控",
        ],
    }
    payload = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    output["calibration_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    return output


def score_observation(row, calibration):
    """用新候选四线逐项Decimal计分；输出试算与缺项，永久禁止正式放量标识。"""
    key = _key(row)
    if _day(row["window_end"]).isoformat() != calibration["as_of_date"]:
        raise ValueError("观察评分日与标准评分日不相容")
    category = str(row.get("category") or "")
    standards = calibration["standards"].get(category, {})
    floor = calibration["growth_floors"].get(category)
    total, pending = Decimal(0), 0
    signals = {}
    with localcontext() as context:
        context.prec = 50
        for feature, weight in FEATURES.items():
            spec = standards.get(feature)
            value = _number(row.get(feature))
            reason = None
            if not _eligible(row, feature, calibration["favorites_additive"]):
                reason = (
                    "收藏定义待核"
                    if feature == "fav_7" and not calibration["favorites_additive"]
                    else "输入/完整窗口/分母或质量条件未满足"
                )
            elif spec is None:
                reason = "品类不足12款且全品类不足24款，没有适用试行标准"
            elif feature == "gmv_growth_7" and (
                floor is None or _number(row.get("gmv_prev7")) < Decimal(floor["bands"][0])
            ):
                reason = "增长基数线缺失或前期金额不足"
            tier = None
            points = None
            if reason:
                pending += weight
            else:
                tier = sum(value >= Decimal(band) for band in spec["bands"]) if value > 0 else 0
                if feature == "gmv_growth_7" and value <= 1:
                    tier = 0
                points = Decimal(weight) * tier / 4
                total += points
            signals[feature] = {
                "current_value": _text(value) if value is not None else None,
                "weight": weight,
                "points": _text(points) if points is not None else None,
                "tier": tier,
                "reason": reason,
                "standard": spec,
            }
    weight = 100 - pending
    return {
        "platform_code": key[0],
        "shop_id": key[1],
        "product_id": key[2],
        "as_of_date": calibration["as_of_date"],
        "calibration_sha256": calibration["calibration_sha256"],
        "score_status": "complete" if pending == 0 else "partial" if weight else "unscored",
        "verified_score": _text(total) if weight else None,
        "verified_weight": weight,
        "pending_weight": pending,
        "complete_score": _text(total) if pending == 0 else None,
        "standard_status": "candidate",
        "formal_scaling_eligible": False,
        "signals": signals,
    }
