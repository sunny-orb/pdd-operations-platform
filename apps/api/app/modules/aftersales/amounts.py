"""固定报告规范金额的观察说明；观察小计不替代正式金额，不据NULL判来源故障。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .rowset import record_count

AmountRows = Sequence[Mapping[str, object]] | None


@dataclass(frozen=True)
class AmountAssessment:
    state: str
    rows: int
    known_rows: int
    null_rows: int
    zero_rows: int
    known_subtotal: Decimal | None
    complete_value: Decimal | None


def assess_amount(rows: AmountRows, field: str) -> AmountAssessment:
    """PG Decimal规范行→已知观察与完整值；缺输入/空筛选/字段空/真实零分别表达。"""
    if rows is None:
        return AmountAssessment("not_received", 0, 0, 0, 0, None, None)
    values = [row.get("_amount_known_subtotal", row.get(field)) for row in rows]
    known = [value for value in values if value is not None]
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in known):
        raise ValueError("AMOUNT_DECIMAL_REQUIRED")
    with localcontext() as context:
        context.prec = 50
        subtotal = sum(known, Decimal(0)) if known else None
    count = record_count(rows)
    known_count = sum(
        row.get("_amount_known_count", int(row.get(field) is not None)) for row in rows
    )
    zero_count = sum(row.get("_amount_zero_count", int(row.get(field) == 0)) for row in rows)
    state = "no_records" if not rows else "field_null" if known_count != count else "observed"
    return AmountAssessment(
        state,
        count,
        known_count,
        count - known_count,
        zero_count,
        subtotal,
        subtotal if state == "observed" else None,
    )


def _money(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 50
        return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _detail(assessment: AmountAssessment, label: str, *, inbound: bool = False) -> str:
    if assessment.state == "not_received":
        return f"本报告尚未接入{label}输入。"
    if assessment.state == "no_records":
        return "当前筛选无退货记录。" if inbound else f"当前筛选无{label}记录。"
    if inbound and assessment.known_subtotal is None:
        return f"已接入{assessment.rows}条退货记录；实际入库额映射待核，暂不计算A。"
    known = (
        f"{label}已知观察小计{_money(assessment.known_subtotal)}元。"
        if assessment.known_subtotal is not None
        else f"当前{assessment.rows}条本地记录尚无可用{label}观察值。"
    )
    if assessment.state == "field_null":
        reason = (
            "实际入库额映射待核"
            if inbound
            else "实发汇总适用待核"
            if label == "实发金额S"
            else "金额适用待核"
        )
        return known + f"{assessment.null_rows}条本地金额为空，{reason}；观察小计未作为完整金额。"
    return known + f"已覆盖当前{assessment.rows}条规范记录，完整日另核。"


def amount_details(
    returns: AmountRows, shipped: AmountRows, cancelled: AmountRows
) -> dict[str, str]:
    """同一次已筛选固定输入→九卡说明；不重算业务率、不填值或跨范围复用小计。"""
    profiles = (
        assess_amount(returns, "actual_inbound_amount"),
        assess_amount(shipped, "sales_amount"),
        assess_amount(cancelled, "sales_amount"),
    )
    a, s, p = profiles
    a_detail = _detail(a, "已映射入库金额A", inbound=True)
    s_detail = _detail(s, "实发金额S")
    p_detail = _detail(p, "已取消销售金额P")
    by_name = dict(zip(("A", "S", "P"), profiles, strict=True))

    def rate_detail(dependencies):
        blockers = [name for name in dependencies if by_name[name].state != "observed"]
        return (
            f"{'、'.join(blockers)}的本地金额/适用状态待核，依赖率暂不可算。"
            if blockers
            else "按相容分子分母计算；零或负分母不计算率，完整日另核。"
        )

    return {
        "inbound_amount": a_detail,
        "gmv": s_detail + p_detail,
        "presale_rate": rate_detail(("S", "P")),
        "aftersale_rate": rate_detail(("A", "S")),
        "total_rate": rate_detail(("A", "S", "P")),
        "total_amount": a_detail + p_detail,
    }
