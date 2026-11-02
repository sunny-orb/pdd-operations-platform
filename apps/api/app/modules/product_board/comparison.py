"""主卡同款比较：先配对店铺商品日，再在同一对记录上汇总两期数值。"""

from decimal import Decimal

from .metrics import aggregate, compare

METRIC_INPUTS = {
    "revenue": ("product_revenue",),
    "visitors": ("visitors",),
    "favorites": ("favorites",),
    "buyers": ("buyers",),
    "units": ("units",),
    "aov": ("product_revenue", "buyers"),
    "conversion": ("buyers", "visitors"),
}


def paired_comparisons(current, baseline, map_day, period):
    """输入固定筛选的原行和日期映射，输出七项共同样本值、增幅和不能比较的原因。

    listing_id包含店铺身份；发布约束保证商品日唯一。缺记录不补零；有记录但
    必需字段无效时仅停止受影响指标，不静默缩小该指标样本；比率按总分子/总分母。
    """
    index = {(r["listing_id"], r["business_date"]): r for r in baseline}
    pairs = [
        (r, index[(r["listing_id"], map_day(r["business_date"]))])
        for r in current
        if (r["listing_id"], map_day(r["business_date"])) in index
    ]
    sides = ([r for r, _ in pairs], [r for _, r in pairs])
    output = {}
    for key, fields in METRIC_INPUTS.items():
        values, reason, message = [], None, None
        for rows, label in zip(sides, ("本期", period), strict=True):
            if not rows:
                value, reason, message = None, "no_pairs", "暂无可比记录"
            elif any(
                r.get(f) is None or not Decimal(r[f]).is_finite() or r[f] < 0
                for r in rows
                for f in fields
            ):
                value = None
                if reason is None:
                    reason, message = "invalid_values", f"{label}计算所需数值不全或无效"
            else:
                value = aggregate(rows, only=(key,))[key]["value"]
                if value is None and reason is None:
                    denominator = "买家数" if key == "aov" else "访客数"
                    reason, message = "zero_denominator", f"{label}{denominator}为0，暂不比较"
            values.append(value)
        now, before = values
        if reason is None and before == 0:
            reason, message = "zero_baseline", f"{period}为0，不计算增幅"
        output[key] = dict(
            value=now,
            previous=before,
            change=compare(now, before) if reason is None else None,
            reason=reason,
            message=message,
            matched_product_days=len(pairs),
            matched_products=len({r["listing_id"] for r in sides[0]}),
        )
    return output
