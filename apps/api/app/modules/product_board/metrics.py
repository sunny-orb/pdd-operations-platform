"""商品指标纯函数：Decimal、成对分母、日历风险窗口；不推定来源完整日。"""

from datetime import timedelta
from decimal import Decimal, localcontext

D = Decimal
METRICS = (
    ("revenue", "交易金额", "currency"),
    ("visitors", "商品访客数", "number"),
    ("favorites", "累计新增收藏量（未扣取消）", "number"),
    ("buyers", "支付买家数", "number"),
    ("units", "支付件数", "number"),
    ("aov", "客单价", "currency"),
    ("conversion", "支付转化率", "percent"),
)
FIELDS = dict(
    revenue="product_revenue",
    visitors="visitors",
    favorites="favorites",
    buyers="buyers",
    units="units",
    views="views",
    orders="orders",
)
WEIGHTS = (
    ("visitors_7d", "商品访客数", 12, "number"),
    ("product_revenue_7d", "交易额", 18, "currency"),
    ("units_7d", "支付件数", 12, "number"),
    ("favorites_7d", "7日累计新增收藏量（未扣取消）", 10, "number"),
    ("score_conversion_rate_7d", "支付转化率", 8, "percent"),
    ("revenue_per_visitor_7d", "UV价值", 10, "currency"),
    ("ad_roi_7d", "投产比", 8, "ratio"),
    ("ad_click_through_rate_7d", "点击率", 4, "percent"),
    ("ad_click_conversion_rate_7d", "点击转化率", 4, "percent"),
    ("revenue_multiple_7d", "近7日交易倍数", 14, "ratio"),
)


def divide(a, b):
    if a is None or b is None or b <= 0 or a < 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return D(a) / D(b)


def compare(now, before):
    ratio = divide(now, before)
    with localcontext() as ctx:
        ctx.prec = 50
        return None if ratio is None else ratio - 1


def total(rows, field):
    values = [D(r[field]) for r in rows if r.get(field) is not None]
    if not values:
        return None
    with localcontext() as ctx:
        ctx.prec = max(
            50,
            max(v.adjusted() for v in values)
            + 1
            + max(max(-v.as_tuple().exponent, 0) for v in values)
            + len(str(len(values)))
            + 2,
        )
        return sum(values, D(0))


def aggregate(rows, *, daily=False):
    """累计已知商品日原值；收藏为日新增，未扣取消且不作跨日人数去重。"""
    output = {}
    for key, field in FIELDS.items():
        count = sum(r.get(field) is not None for r in rows)
        value = total(rows, field)
        status = "observed" if rows and count == len(rows) else "partial" if count else "missing"
        reason = None if status == "observed" else "字段缺失或无记录"
        output[key] = dict(
            value=value,
            status=status,
            reason=reason,
            valid_rows=str(count),
            observed_rows=str(len(rows)),
        )
    for key, numerator, denominator in (
        ("aov", "product_revenue", "buyers"),
        ("conversion", "buyers", "visitors"),
        ("score_conversion", "orders", "visitors"),
        ("uv_value", "product_revenue", "visitors"),
    ):
        paired = [
            r
            for r in rows
            if all(r.get(f) is not None and r[f] >= 0 for f in (numerator, denominator))
        ]
        n, d = total(paired, numerator), total(paired, denominator)
        value = divide(n, d)
        output[key] = dict(
            value=value,
            status="not_computable"
            if value is None
            else "observed"
            if len(paired) == len(rows)
            else "partial",
            reason="无记录或非正分母"
            if value is None
            else None
            if len(paired) == len(rows)
            else "只采用分子分母同时有效的行",
            numerator=n,
            denominator=d,
            valid_rows=str(len(paired)),
            observed_rows=str(len(rows)),
        )
    return output


def observed_days(rows, start, end, fields):
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    return {
        r["business_date"] for r in rows if all(r.get(f) is not None and r[f] >= 0 for f in fields)
    } == expected


def risk(rows, end, top):
    with localcontext() as ctx:
        ctx.prec = 50
        return _risk(rows, end, top)


def _risk(rows, end, top):
    """命中首分支即返回；基准为T-7..T-1，趋势仅T-6..T，损失是规则估算。"""
    byday = {r["business_date"]: r for r in rows}
    dates = [end - timedelta(days=i) for i in range(7, -1, -1)]
    if any(
        day not in byday
        or any(byday[day].get(f) is None or byday[day][f] < 0 for f in ("visitors", "orders"))
        for day in dates
    ):
        return None
    past = [byday[d] for d in dates[:-1]]
    current = byday[end]
    v, o = D(current["visitors"]), D(current["orders"])
    pv, po = total(past, "visitors"), total(past, "orders")
    avg = pv / 7
    cb = divide(po, pv)
    cv = divide(o, v)
    drop = compare(v, avg)
    label = priority = score = kind = field = None
    if v >= 100 and o == 0:
        label, priority, score, now, baseline, kind, field = (
            "有流量无成交",
            "P1",
            100,
            o,
            po / 7,
            "number",
            "orders",
        )
        reasons = ["有流量无成交", "商品承接待核查"]
        action = "核查价格、优惠、评价、详情页和商品可售状态。"
    elif v >= 100 and cb and cv is not None and cv <= cb * D(".7"):
        label, priority, score, now, baseline, kind, field = (
            "严重订单转化下滑",
            "P1",
            90,
            cv,
            cb,
            "percent",
            "conversion",
        )
        reasons = ["订单转化率下滑", "价格/详情页待核查"]
        action = "优先复核价格、优惠、主图、评价与详情页承接。"
    elif avg >= 30 and drop is not None and drop <= D("-.4") and avg - v >= 30:
        priority = "P0" if top and po >= 10 else "P1"
        label, score, now, baseline, kind, field = (
            "爆品严重流量下滑" if priority == "P0" else "严重流量下滑",
            95 if priority == "P0" else 85,
            v,
            avg,
            "number",
            "visitors",
        )
        reasons = ["访客显著下滑", "活动/素材/流量入口待核查"]
        action = "检查活动状态、素材、推广计划与自然流量入口变化。"
    elif v >= 100 and cb and cv is not None and cv <= cb * D(".85"):
        label, priority, score, now, baseline, kind, field = (
            "中度订单转化下滑",
            "P2",
            70,
            cv,
            cb,
            "percent",
            "conversion",
        )
        reasons = ["订单转化率下滑", "商品承接待优化"]
        action = "复核优惠、详情页承接和商品可售状态，隔日复盘。"
    elif avg >= 30 and drop is not None and drop <= D("-.2") and avg - v >= 20:
        label, priority, score, now, baseline, kind, field = (
            "中度流量下滑",
            "P2",
            65,
            v,
            avg,
            "number",
            "visitors",
        )
        reasons = ["访客下滑", "流量入口待核查"]
        action = "检查流量入口和活动节奏，观察次日访客恢复。"
    else:
        return None
    # 估算客单价使用同一完整七日金额/订单；金额缺失仅阻断损失估算。
    aov = (
        divide(total(past, "product_revenue"), po)
        if all(r.get("product_revenue") is not None and r["product_revenue"] >= 0 for r in past)
        else None
    )
    loss = None
    if cb is not None and aov is not None:
        loss = (
            v * cb
            if field == "orders"
            else v * max(D(0), cb - cv)
            if field == "conversion"
            else max(D(0), avg - v) * cb
        ) * aov
    trend = [
        divide(byday[d]["orders"], byday[d]["visitors"])
        if field == "conversion"
        else D(byday[d][field])
        for d in dates[1:]
    ]
    return dict(
        label=label,
        priority=priority,
        score=score,
        current=now,
        baseline=baseline,
        change=compare(now, baseline),
        type=kind,
        trend=trend,
        expected_loss=loss,
        reasons=reasons,
        action=action,
        deadline=None,
        status="未启用",
        basis="订单转化=支付订单数÷商品访客数；规则估算，来源完整性未知",
    )
