"""助手领域适配的展示契约。只整理既有查询结果，不定义新的业务公式。"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.modules.product_board.repository import BoardError, wire


def window(params, start, end):
    """显式范围须在本域覆盖内；默认最近N日锚定本域实际观察截至。"""
    if start is None or end is None:
        raise BoardError("ASSISTANT_DATA_UNAVAILABLE", 503)
    first = params.get("date_from")
    last = params.get("date_to")
    first = date.fromisoformat(first) if isinstance(first, str) else first
    last = date.fromisoformat(last) if isinstance(last, str) else last
    days = params.get("recent_days", 7)
    if (first is None) != (last is None):
        raise BoardError("INVALID_SCOPE")
    if first is None:
        last = end
        first = max(start, last - timedelta(days=days - 1))
    if not start <= first <= last <= end or (last - first).days >= 366:
        raise BoardError("INVALID_SCOPE")
    return first, last


def change_points(unit, current, previous):
    """两期原始比例的百分点差。任一期缺值时保留缺项，ROI 等倍数不适用。"""
    if unit != "percent" or current is None or previous is None:
        return None
    delta = (Decimal(str(current)) - Decimal(str(previous))) * 100
    return format(delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def fact(key, label, value, unit="text", *, status="observed", reason=None):
    return wire(dict(key=key, label=label, value=value, unit=unit, status=status, reason=reason))


def table(title, columns, rows, *, total=None, offset=0):
    """columns为(key,中文列名,单位)；切片由领域适配显式执行，total保留真实总体数量。"""
    return wire(
        dict(
            title=title,
            columns=[dict(key=key, label=label, unit=unit) for key, label, unit in columns],
            rows=rows,
            total=total,
            offset=offset,
        )
    )


def report(
    domain,
    capability,
    title,
    source,
    version,
    *,
    period=None,
    scope=None,
    facts=(),
    tables=(),
    notes=(),
    series=(),
    versions=None,
):
    return wire(
        dict(
            kind="dashboard",
            domain=domain,
            capability=capability,
            title=title,
            period=period,
            scope=scope or {},
            facts=list(facts),
            tables=list(tables),
            notes=list(notes),
            series=list(series),
            source=source,
            data_version=version,
            versions=versions or {domain: version},
        )
    )
