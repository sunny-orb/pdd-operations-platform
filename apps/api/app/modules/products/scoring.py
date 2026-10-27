"""确定性四档引擎：仅计算给定值与相容标准，不生成历史阈值或执行预算。"""

from decimal import Decimal, InvalidOperation, localcontext

RULE_VERSION = "m1-four-tiers-v1"
TIERS = ("basic", "good", "excellent", "outstanding")
# 中文名、权重、API量纲；所有判分使用未格式化Decimal。
METRICS = {
    "visitors_7d": ("商品访客数", 12, "observations"),
    "product_revenue_7d": ("交易额", 18, "yuan"),
    "units_7d": ("支付件数", 12, "units"),
    "favorites_7d": ("收藏数", 10, "observations"),
    "score_conversion_rate_7d": ("支付转化率", 8, "ratio"),
    "revenue_per_visitor_7d": ("UV价值", 10, "yuan_per_visitor"),
    "ad_roi_7d": ("投产比", 8, "multiple"),
    "ad_click_through_rate_7d": ("点击率", 4, "ratio"),
    "ad_click_conversion_rate_7d": ("点击转化率", 4, "ratio"),
    "revenue_multiple_7d": ("近7日交易倍数", 14, "multiple"),
}
LABELS = {
    "unscored": "暂未评分",
    "pending_validation": "先补验证 · 暂不放量",
    "optimize": "优化后再测 · 不加预算",
    "hold": "保持投放 · 暂不放量",
    "fix_key_metrics": "高分待优化 · 暂不放量",
    "small_scale": "建议小步放量",
}


def number(value):
    """只接受精确整数、十进制文本或Decimal；拒绝float及非有限值。"""
    if isinstance(value, (float, bool)) or value is None:
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def divide(numerator, denominator):
    """比率统一50位有效精度；缺项与非正分母返回空，不经二进制浮点。"""
    a, b = number(numerator), number(denominator)
    if a is None or b is None or a < 0 or b <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return a / b


def decide(score, pending, signals, values, evidence_reasons):
    """按固定顺序返回建议与全部短板；试算/来源未验优先不放量。"""
    reasons = list(evidence_reasons)
    note = None
    if score is None:
        code = "unscored"
    elif pending or reasons:
        code = "pending_validation"
        reasons += [s["metric_key"] for s in signals if s["points"] is None]
    elif score < 50:
        code, note = "optimize", "保留必要测试预算，下一完整7日周期复评。"
    elif score < 75:
        code, note = "hold", "观察3天后，按最新完整7日数据复评。"
    else:
        tiers = {s["metric_key"]: s["tier"] for s in signals}
        for key, minimum in (
            ("ad_roi_7d", 2),
            ("score_conversion_rate_7d", 2),
            ("ad_click_conversion_rate_7d", 1),
        ):
            rank = TIERS.index(tiers[key]) + 1 if tiers.get(key) in TIERS else 0
            if rank < minimum:
                reasons.append(key)
        multiple = number(values.get("revenue_multiple_7d"))
        if multiple is None or multiple <= 1:
            reasons.append("revenue_multiple_7d")
        code = "fix_key_metrics" if reasons else "small_scale"
        if code == "small_scale":
            note = (
                "先人工确认库存可售、投产高于保本线且近期转化未下降，"
                "再考虑每次加预算10%—20%，观察3天、每日复评；"
                "投产跌破良好线或保本线、转化下降或缺货时停止加量并回撤新增预算。"
            )
    return dict(code=code, label=LABELS[code], reasons=sorted(set(reasons)), execution_note=note)


def score_product(values, standard, *, blocked=None, evidence_reasons=()):
    """输入十项值/逐项阻断原因/标准，输出十项判分、真实缺权重及建议。"""
    blocked = blocked or {}
    standard = standard or {}
    signals = []
    for key, (label, weight, unit) in METRICS.items():
        value = number(values.get(key))
        reasons = list(blocked.get(key, []))
        if value is None:
            reasons.append("missing_or_invalid_value")
        elif value < 0:
            reasons.append("negative_input")
        reference = standard.get("metrics", {}).get(key, {})
        raw = reference.get("thresholds", {})
        lines = [number(raw.get(tier)) for tier in TIERS]
        if not reference:
            reasons.append("no_applicable_threshold")
        elif reference.get("unit") != unit:
            reasons.append("threshold_unit_mismatch")
        elif any(x is None or x < 0 for x in lines) or lines != sorted(lines):
            reasons.append("invalid_thresholds")
        tier, points = None, None
        if not reasons:
            rank = max([0] + [i + 1 for i, line in enumerate(lines) if value >= line])
            if key == "revenue_multiple_7d" and value <= 1:
                rank = 0
            tier = TIERS[rank - 1] if rank else "below_basic"
            points = Decimal(weight) * rank / 4
        signals.append(
            dict(
                metric_key=key,
                label=label,
                weight=weight,
                unit=unit,
                current_value=value,
                thresholds=raw or None,
                tier=tier,
                points=points,
                status=(
                    "valid" if not reasons else ("missing" if value is None else "not_computable")
                ),
                missing_reasons=sorted(set(reasons)),
                reference_group=reference.get("reference_group"),
                reference_sample_count=reference.get("reference_sample_count"),
                fallback_level=reference.get("fallback_level"),
            )
        )
    active = [s for s in signals if s["points"] is not None]
    verified = sum((s["points"] for s in active), Decimal(0)) if active else None
    weight = sum(s["weight"] for s in active)
    pending = 100 - weight
    status = "complete" if not pending else ("partial" if active else "unscored")
    evidence = list(evidence_reasons)
    if standard.get("status") != "validated":
        evidence.append("thresholds_trial_or_not_validated")
    return dict(
        signals=signals,
        verified_score=verified,
        verified_weight=weight,
        pending_weight=pending,
        complete_score=verified if not pending else None,
        active_signal_count=len(active),
        pending_signal_count=10 - len(active),
        met_signal_count=sum(s["tier"] in TIERS for s in active),
        score_status=status,
        decision=decide(verified, pending, signals, values, evidence),
    )
