"""主卡期间比较：本期合计直接参与涨跌，保留两期记录和无效原因。"""

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


def period_comparisons(current, baseline, period):
    """输入已确定范围的两期原行，输出七项合计、增幅和具体空值原因。

    不取两期交集，不补零，不平均日比率；已有行的必需字段无效时仅停止
    受影响指标的比较。同比历史选择和环比筛选由调用方负责。
    """
    sides = (current, baseline)
    aggregates = [aggregate(rows, only=tuple(METRIC_INPUTS)) for rows in sides]
    valid = [
        {
            f: all(r.get(f) is not None and Decimal(r[f]).is_finite() and r[f] >= 0 for r in rows)
            for f in {f for fields in METRIC_INPUTS.values() for f in fields}
        }
        for rows in sides
    ]
    output = {}
    for key, fields in METRIC_INPUTS.items():
        values, reason, message = [], None, None
        for rows, totals, fields_valid, label in zip(
            sides, aggregates, valid, ("本期", period), strict=True
        ):
            if not rows:
                value = None
                if reason is None:
                    reason, message = "no_records", f"{label}暂无经营记录"
            elif not all(fields_valid[f] for f in fields):
                value = None
                if reason is None:
                    reason, message = "invalid_values", f"{label}计算所需数值不全或无效"
            else:
                value = totals[key]["value"]
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
            current_rows=len(current),
            previous_rows=len(baseline),
        )
    return output
