"""对同一修订和同批日数据执行月度 GMV 预测。金额始终保留元，展示层负责换算。"""

from calendar import monthrange
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext

_DAY = timedelta(days=1)
_ZERO = Decimal(0)
_PROMOTION_FIELDS = ("spend", "revenue", "clicks", "orders")


def _usable(value):
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def _continuous_days(month, cutoff, daily):
    """调用方已按六店去重并校验基础金额，再逐自然日检验是否断档、缺值或真实零。"""
    dates = []
    day = month
    while cutoff and day <= cutoff and _usable(daily.get(day)):
        dates.append(day)
        day += _DAY
    return dates


def _sum(days, daily):
    return sum((daily[day] for day in days), _ZERO) if days else None


def _promotion(days, sales, source):
    """每个比率各自配对有效日；其他字段缺失不丢弃当前指标已有的真实数据。"""
    amounts = {}
    for field in _PROMOTION_FIELDS:
        amounts[field] = {
            day: row[field] for day in days if (row := source.get(day)) and _usable(row.get(field))
        }
    result = {
        field: sum(values.values(), _ZERO) if values else None for field, values in amounts.items()
    }
    result["metric_days"], result["missing_dates"] = {}, {}
    for metric, numerator, denominator in (
        ("roi", amounts["revenue"], amounts["spend"]),
        ("sales_share", amounts["revenue"], sales),
        ("click_conversion", amounts["orders"], amounts["clicks"]),
    ):
        matched = [d for d in days if d in numerator and d in denominator]
        result[metric] = _ratio(_sum(matched, numerator), _sum(matched, denominator))
        result["metric_days"][metric] = len(matched)
        result["missing_dates"][metric] = [d for d in days if d not in matched]
    return result


def _window(size, days, daily, promotion):
    current = days[-size:] if size else days
    previous = days[-2 * size : -size] if size and len(days) >= 2 * size else []
    amount, previous_amount = _sum(current, daily), _sum(previous, daily)
    average = _ratio(amount, len(current))
    previous_average = _ratio(previous_amount, len(previous))
    change = average - previous_average if previous_average is not None else None
    return {
        "label": f"近{size}日" if size else "月累计",
        "days": size,
        "start": current[0] if current else None,
        "end": current[-1] if current else None,
        "sample_days": len(current),
        "gmv": amount,
        "daily_average": average,
        "previous_start": previous[0] if previous else None,
        "previous_end": previous[-1] if previous else None,
        "previous_average": previous_average,
        "change_amount": change,
        "change_rate": _ratio(change, previous_average),
        "promotion": _promotion(current, daily, promotion),
    }


def _range(actual, remaining, low, high):
    if low > high:
        return None
    return dict(
        low_daily=low, high_daily=high, low=actual + remaining * low, high=actual + remaining * high
    )


def _window_average(size, days, daily):
    if size > len(days):
        return None
    return _sum(days[-size:], daily) / size


def _scenarios(rule, month, days, daily, actual, remaining, comparable_start):
    scenarios = dict(baseline=None, conservative=None, optimistic=None)
    if not remaining or not days:
        return scenarios
    means = [_window_average(size, days, daily) for size in rule["baseline_windows"]]
    if None in means:
        return scenarios
    low, high = min(means), max(means)
    scenarios["baseline"] = _range(actual, remaining, low, high)
    phase = rule["activity"]
    if (
        month.month != phase["month"]
        or comparable_start is None
        # 已接受的活动条款都是“双十一后”；旧编译记录只存了月份和窗口。
        or not month.replace(day=phase.get("after_day", 11)) < comparable_start <= days[-1]
    ):
        return scenarios
    comparable = [d for d in days if d >= comparable_start]
    low_days, high_days = phase["low_days"], phase["high_days"]
    if len(comparable) >= low_days:
        moving_low = min(
            _sum(comparable[start : start + low_days], daily) / low_days
            for start in range(len(comparable) - low_days + 1)
        )
        scenarios["conservative"] = _range(actual, remaining, moving_low, low)
    if len(comparable) >= high_days:
        single_day_high = max(daily[d] for d in comparable[-high_days:])
        scenarios["optimistic"] = _range(actual, remaining, high, single_day_high)
    return scenarios


def _prior_decimal(value):
    if isinstance(value, (str, Decimal, int)) and not isinstance(value, bool):
        try:
            amount = Decimal(value)
        except InvalidOperation:
            return None
        return amount if _usable(amount) else None
    return None


def _prior_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _comparison(previous, month, cutoff, daily, center, target):
    if (
        not previous
        or center is None
        or cutoff is None
        or _prior_date(previous.get("month")) != month
    ):
        return None
    prior_cutoff = _prior_date(previous.get("cutoff"))
    prior_center = _prior_decimal(previous.get("center"))
    if prior_cutoff is None or not month <= prior_cutoff <= cutoff or prior_center is None:
        return None
    change = center - prior_center
    prior_daily = _prior_decimal(previous.get("center_daily"))
    prior_target = _prior_decimal(previous.get("target"))
    return dict(
        previous_cutoff=prior_cutoff,
        previous_center=prior_center,
        center_change=change,
        center_change_rate=_ratio(change, prior_center),
        target_change=target - prior_target
        if target is not None and prior_target is not None
        else None,
        latest_actual_minus_prior_daily=(
            daily[cutoff] - prior_daily
            if prior_daily is not None and prior_cutoff + _DAY == cutoff
            else None
        ),
    )


def calculate_forecast(
    rule: Mapping,
    *,
    month: date,
    cutoff: date | None,
    daily: Mapping[date, Decimal | None],
    target: Decimal | None,
    promotion: Mapping[date, Mapping[str, Decimal | None]] | None = None,
    comparable_start: date | None = None,
    previous: Mapping | None = None,
) -> dict:
    """输入同一自然月、同口径六店已核实的日汇总。结果保留 Decimal 和日期，允许独立缺项。

    cutoff 是调用方对连续完整销售日的上界；历史记录、推广覆盖或未知活动日期不能扩大它。
    可比起日仅由已核实的当月活动阶段提供。previous 是上次真实冻结快照，首次调用留空。
    """
    month = month.replace(day=1)
    month_end = month.replace(day=monthrange(month.year, month.month)[1])
    if rule.get("type") != "weighted_daily_mean" or (cutoff and not month <= cutoff <= month_end):
        raise ValueError("预测需要已编译规则及同月的销售核对范围。")
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        days = _continuous_days(month, cutoff, daily)
        cutoff = days[-1] if days else None
        remaining = month_end.day - len(days) if cutoff else None
        actual = _sum(days, daily)
        target = target if _usable(target) else None
        gap = target - actual if target is not None and actual is not None else None
        windows = [_window(size, days, daily, promotion or {}) for size in [*rule["windows"], None]]
        center_daily, center = None, None
        if not days:
            status = "no_complete_day"
        elif not remaining:
            status, center = "closed", actual
        elif any(term["days"] > len(days) for term in rule["terms"]):
            status = "insufficient_sample"
        else:
            status = "forecast"
            center_daily = sum(
                (
                    _window_average(term["days"], days, daily) * Decimal(term["weight"])
                    for term in rule["terms"]
                ),
                _ZERO,
            )
            center = actual + remaining * center_daily
        scenarios = _scenarios(rule, month, days, daily, actual, remaining, comparable_start)
        notes = []
        if status == "no_complete_day":
            notes.append("本月尚无从月初连续核实的销售日。")
        elif status == "insufficient_sample":
            needed = max(term["days"] for term in rule["terms"])
            notes.append(f"已核实 {len(days)} 个完整销售日，中心估计需要 {needed} 天。")
        if status == "forecast" and scenarios["optimistic"]:
            notes.append("乐观上限要求余下各天平均达到近期单日高位。")
        if gap is None and target is None:
            notes.append("本月目标待确认。")
        return dict(
            month=month,
            cutoff=cutoff,
            complete_days=len(days),
            remaining_days=remaining,
            actual=actual,
            target=target,
            gap=gap,
            achievement_rate=_ratio(actual, target),
            needed_daily=gap / remaining if gap is not None and gap > 0 and remaining else None,
            over_target=-gap if gap is not None and gap <= 0 else None,
            center_daily=center_daily,
            center=center,
            forecast_achievement_rate=_ratio(center, target),
            status=status,
            windows=windows,
            scenarios=scenarios,
            report_order=list(rule["report_order"]),
            comparison=_comparison(previous, month, cutoff, daily, center, target),
            notes=notes,
        )
