"""增长健康度：只比较同店同款能按去年同月同日对应的经营记录。"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

from .metrics import compare, divide, total
from .source import previous_year

# 每项独立检查必需字段，三项始终使用同一组商品日，不能平均逐日比率。
HEALTH_METRICS = {
    "revenue": ("交易额", "product_revenue", None),
    "conversion": ("支付转化率", "buyers", "visitors"),
    "aov": ("客单价", "product_revenue", "buyers"),
}
FIELD_NAMES = {"product_revenue": "交易额", "buyers": "支付买家数", "visitors": "访客数"}


def _value(rows, label, numerator, denominator, period):
    fields = (numerator, denominator) if denominator else (numerator,)
    for field in fields:
        if any(r.get(field) is None for r in rows):
            return None, f"{period}部分记录未提供{FIELD_NAMES[field]}，{label}暂不能比较"
        if any(not Decimal(r[field]).is_finite() or r[field] < 0 for r in rows):
            return None, f"{period}{FIELD_NAMES[field]}存在无效值，{label}暂不能比较"
    value = total(rows, numerator)
    if denominator:
        base = total(rows, denominator)
        if base == 0:
            return None, f"{period}{FIELD_NAMES[denominator]}为0，{label}无法计算"
        value = divide(value, base)
    return value, None


def _change_text(label, metric):
    change = metric["yoy"]
    if change is None:
        return metric["reason"]
    if change == 0:
        return f"{label}同比持平"
    direction = "增长" if change > 0 else "下降"
    with localcontext() as ctx:
        ctx.prec = 50
        percent = abs(change) * 100
        # 数值方向按未舍入结果判断，微小变动不能写成“增长0%”。
        amount = (
            "<0.01"
            if percent < Decimal(".01")
            else format(percent.quantize(Decimal(".01"), rounding=ROUND_HALF_UP), "f")
        )
    return f"{label}同比{direction} {amount}%"


def growth_health(current, previous):
    """输入同快照已筛选的两期原行，输出健康度及三项配对证据；不修改输入或其他指标。

    listing_id包含店铺与商品身份；发布约束保证其逐日唯一。
    只取两期交集，闰日无去年同月同日映射则不纳入；未知日终完整性不把共同记录判成不可比。
    """
    baseline = {(r["listing_id"], r["business_date"]): r for r in previous}
    pairs = [
        (r, baseline[(r["listing_id"], previous_year(r["business_date"]))])
        for r in current
        if (r["listing_id"], previous_year(r["business_date"])) in baseline
    ]
    matched_current = [r for r, _ in pairs]
    matched_previous = [r for _, r in pairs]
    metrics = {}
    for key, (label, numerator, denominator) in HEALTH_METRICS.items():
        value = before = change = None
        reason = "暂无同期可比记录"
        if pairs:
            value, current_reason = _value(matched_current, label, numerator, denominator, "本期")
            before, previous_reason = _value(
                matched_previous, label, numerator, denominator, "去年同期"
            )
            reason = current_reason or previous_reason
            if reason is None and before == 0:
                reason = f"去年同期{label}为0，暂不计算同比增幅"
            if reason is None:
                change = compare(value, before)
        metrics[key] = dict(value=value, previous=before, yoy=change, reason=reason)

    revenue = metrics["revenue"]["yoy"]
    tone = "neutral"
    if not pairs:
        title = "暂无同期可比数据"
        detail = (
            "所选商品在去年对应日期没有可比记录，暂不计算同比。"
            if current
            else "所选范围暂无商品经营记录，暂不计算同比。"
        )
    else:
        if revenue is None:
            title = "交易额暂无可比增幅"
        elif revenue > 0 and any(
            metrics[k]["yoy"] is not None and metrics[k]["yoy"] < 0 for k in ("conversion", "aov")
        ):
            title, tone = "规模增长、效率承压", "watch"
        else:
            direction = "增长" if revenue > 0 else "下降" if revenue < 0 else "持平"
            title = f"交易额同比{direction}"
        detail = (
            "；".join(
                _change_text(label, metrics[key]) for key, (label, _, _) in HEALTH_METRICS.items()
            )
            + "。仅比较有同期记录的同款商品。"
        )
    return dict(
        title=title,
        tone=tone,
        detail=detail,
        matched_product_days=len(pairs),
        matched_products=len({r["listing_id"] for r in matched_current}),
        metrics=metrics,
    )
