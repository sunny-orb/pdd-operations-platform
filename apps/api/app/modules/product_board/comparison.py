"""主卡期间比较：本期合计直接参与涨跌，保留两期记录和无效原因。"""

from dataclasses import dataclass
from decimal import Decimal

from .metrics import _sum, compare, divide

METRIC_INPUTS = {
    "revenue": ("product_revenue",),
    "visitors": ("visitors",),
    "favorites": ("favorites",),
    "buyers": ("buyers",),
    "units": ("units",),
    "aov": ("product_revenue", "buyers"),
    "conversion": ("buyers", "visitors"),
}
PERIOD_FIELDS = tuple(dict.fromkeys(field for fields in METRIC_INPUTS.values() for field in fields))


@dataclass(frozen=True)
class PeriodSummary:
    row_count: int
    totals: dict[str, Decimal | None]


def summarize_period(rows) -> PeriodSummary:
    """按已筛选原行校验并精确汇总，每个必需字段只读一次。

    单个字段不齐时保留其不可比较状态。两项比率只使用完整有效的
    分子分母合计，金额保留原求和顺序和小数精度。
    """
    totals = {}
    for field in PERIOD_FIELDS:
        values = []
        for row in rows:
            raw = row.get(field)
            if raw is None:
                break
            value = Decimal(raw)
            if not value.is_finite() or value < 0:
                break
            values.append(value)
        else:
            totals[field] = _sum(values)
            continue
        totals[field] = None
    return PeriodSummary(len(rows), totals)


def period_comparisons(current: PeriodSummary, baseline: PeriodSummary, period: str):
    """比较已确定范围的两期合计，保留缺项原因及实际记录数。"""
    output = {}
    for key, fields in METRIC_INPUTS.items():
        values, reason, message = [], None, None
        for side, label in ((current, "本期"), (baseline, period)):
            totals = [side.totals[field] for field in fields]
            if not side.row_count:
                value = None
                if reason is None:
                    reason, message = "no_records", f"{label}暂无经营记录"
            elif None in totals:
                value = None
                if reason is None:
                    reason, message = "invalid_values", f"{label}计算所需数值不全或无效"
            else:
                value = totals[0] if len(totals) == 1 else divide(*totals)
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
            current_rows=current.row_count,
            previous_rows=baseline.row_count,
        )
    return output
