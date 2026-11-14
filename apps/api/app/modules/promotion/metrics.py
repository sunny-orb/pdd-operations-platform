"""推广指标公共计算：已筛选生效日记录入参，返回Decimal指标及状态，不读库不补记录。"""

import hashlib
from pathlib import Path

from app.modules.product_board.metrics import divide, total

FIELDS = {
    "spend": "spend",
    "revenue": "attributed_revenue",
    "net_revenue": "net_attributed_revenue",
    "deals": "attributed_deals",
    "impressions": "impressions",
    "clicks": "clicks",
}
SPECS = {
    "spend": ("总花费", "currency"),
    "revenue": ("交易额", "currency"),
    "roi": ("实际投产比", "ratio"),
    "impressions": ("曝光量", "number"),
    "clicks": ("点击量", "number"),
    "ctr": ("点击率", "percent"),
    "cvr": ("点击转化率", "percent"),
    "cpc": ("CPC", "currency"),
    "net_revenue": ("净交易额", "currency"),
    "net_roi": ("净投产比", "ratio"),
    "deals": ("成交笔数", "number"),
}


def metric(value, key, status="observed", reason=None, **extra):
    label, kind = SPECS[key]
    return dict(
        value=value,
        status=status,
        reason=reason,
        label=label,
        type=kind,
        previous=None,
        change=None,
        **extra,
    )


def aggregate(rows, *, strict=False, cross=False, independent=False):
    """默认按原行配对；商品显式独立汇总已知分子/分母，不修改输入空值。"""
    result, field_totals = {}, {}
    for key, field in FIELDS.items():
        valid = [r for r in rows if r.get(field) is not None]
        state = (
            "observed"
            if rows and len(valid) == len(rows)
            else "partial"
            if valid
            else "missing"
            if rows
            else "no_records"
        )
        value = total(valid, field)
        # 独立比率复用本次精确累计；保留strict门控前的原值及NULL，不能用展示值。
        if independent:
            field_totals[field] = value
        if strict and len(valid) != len(rows):
            value = None
        result[key] = metric(
            value,
            key,
            state,
            None if state == "observed" else "字段缺失或无记录；缺失不视为零",
            valid_rows=str(len(valid)),
            observed_rows=str(len(rows)),
        )
    for key, numerator, denominator in (
        ("roi", "attributed_revenue", "spend"),
        ("net_roi", "net_attributed_revenue", "spend"),
        ("ctr", "clicks", "impressions"),
        ("cvr", "attributed_deals", "clicks"),
        ("cpc", "spend", "clicks"),
    ):
        paired = [
            r for r in rows if r.get(numerator) is not None and r.get(denominator) is not None
        ]
        if independent:
            n, d = field_totals[numerator], field_totals[denominator]
        else:
            n, d = total(paired, numerator), total(paired, denominator)
        value = divide(n, d)
        state = (
            "observed"
            if rows and len(paired) == len(rows)
            else "partial"
            if n is not None and d is not None
            else "missing"
            if rows
            else "no_records"
        )
        reason = (
            None
            if state == "observed"
            else (
                "按已提供的分子分母分别汇总；存在缺项，仅供参考"
                if independent
                else "仅使用分子分母同时有效的原行"
            )
        )
        if strict and len(paired) != len(rows):
            value = None
        if d is not None and d <= 0:
            state, reason = "zero_denominator", "分母为零，不计算比率"
        result[key] = metric(
            value,
            key,
            state,
            reason,
            numerator=n,
            denominator=d,
            valid_rows=str(len(paired)),
            observed_rows=str(len(rows)),
        )
    if cross:
        for key in ("net_revenue", "net_roi"):
            result[key].update(
                value=None,
                status="missing_channel_input",
                reason="明星店铺未提供净交易额；不以商品渠道部分净额冒充合计",
            )
    for key in ("revenue", "net_revenue", "deals", "roi", "net_roi", "cvr"):
        result[key]["business_date_basis"] = "payment_date"
        result[key]["attribution_state"] = "source_reported_payment_date"
        if result[key]["status"] == "observed":
            result[key]["reason"] = "沿用来源已归因的支付日汇总；不重算点击归因，完整日仍未知"
    return result


def product_metrics(rows):
    """商品沿线上分别合计已知分子/分母；保留原始空值及partial审计状态。

    当前、前期、同期及每个日期独立计算；无记录、全空字段、非正分母不硬算。
    费用未拆到单品不等于实际免费，不能据此分摊店铺花费或更改店铺计算。
    """
    return aggregate(rows, independent=True)


def store_metrics(rows, *, include_star=False):
    """销售卡片、销售明细和店铺推广共用入口；缺日照算，已有行缺必要字段返回空。"""
    return aggregate(rows, strict=True, cross=include_star)


def definition_revision():
    """规则或数值运算变化同步使两页查询版本失效，不改变来源发布版本。"""
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(__file__).parents[1].joinpath("product_board/metrics.py").read_bytes()
    ).hexdigest()
