"""商品指标纯函数：Decimal、成对分母、日历风险窗口；不推定来源完整日。"""

from datetime import timedelta
from decimal import Decimal, Inexact, localcontext

from . import risk_rules

D = Decimal
# 保留既有导入名；计算读取共用定义，流量下滑含边界，其他预警为严格大于。
RISK_TRAFFIC_THRESHOLD = risk_rules.TRAFFIC_MIN
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
FIELD_KEYS = {field: key for key, field in FIELDS.items()}
RATIOS = (
    ("aov", "product_revenue", "buyers"),
    ("conversion", "buyers", "visitors"),
    ("score_conversion", "orders", "visitors"),
    ("uv_value", "product_revenue", "visitors"),
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
    return _sum(values)


def _sum(values):
    if not values:
        return None
    # 通常金额/计数在50位内可精确累加；陷阱发现任何精度损失才扫描位数重算。
    # 不用float，也不在每次正常汇总中对全部Decimal反复拆分数字元组。
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            ctx.traps[Inexact] = True
            return sum(values, D(0))
    except Inexact:
        pass
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


def aggregate(rows, *, daily=False, only=None):
    """累计已知原值；only仅限制内部计算项，保留成对过滤、精确和及缺失状态。"""
    output, complete_nonnegative = {}, {}
    ratios = RATIOS if only is None else tuple(r for r in RATIOS if r[0] in only)
    fields = (
        set(FIELDS.values())
        if only is None
        else {FIELDS[key] for key in only if key in FIELDS}
        | {field for _, numerator, denominator in ratios for field in (numerator, denominator)}
    )
    for key, field in FIELDS.items():
        if field not in fields:
            continue
        values = [D(r[field]) for r in rows if r.get(field) is not None]
        count = len(values)
        value = _sum(values)
        complete_nonnegative[field] = count == len(rows) and all(v >= 0 for v in values)
        status = "observed" if rows and count == len(rows) else "partial" if count else "missing"
        reason = None if status == "observed" else "字段缺失或无记录"
        output[key] = dict(
            value=value,
            status=status,
            reason=reason,
            valid_rows=str(count),
            observed_rows=str(len(rows)),
        )
    for key, numerator, denominator in ratios:
        if complete_nonnegative[numerator] and complete_nonnegative[denominator]:
            # 全部行成对有效时复用刚刚计算的精确和；缺值/负数仍走原成对筛选。
            paired_count = len(rows)
            n = output[FIELD_KEYS[numerator]]["value"]
            d = output[FIELD_KEYS[denominator]]["value"]
        else:
            paired = [
                r
                for r in rows
                if all(r.get(f) is not None and r[f] >= 0 for f in (numerator, denominator))
            ]
            paired_count = len(paired)
            n, d = total(paired, numerator), total(paired, denominator)
        value = divide(n, d)
        output[key] = dict(
            value=value,
            status="not_computable"
            if value is None
            else "observed"
            if paired_count == len(rows)
            else "partial",
            reason="无记录或非正分母"
            if value is None
            else None
            if paired_count == len(rows)
            else "只采用分子分母同时有效的行",
            numerator=n,
            denominator=d,
            valid_rows=str(paired_count),
            observed_rows=str(len(rows)),
        )
    return output if only is None else {key: output[key] for key in only}


def merge_aggregates(parts):
    """合并不重叠行组的完整聚合；累加精确分子/分母和有效行数，不平均比率。"""
    observed = sum(int(part["revenue"]["observed_rows"]) for part in parts)
    output = {}
    for key in FIELDS:
        count = sum(int(part[key]["valid_rows"]) for part in parts)
        value = _sum([part[key]["value"] for part in parts if part[key]["value"] is not None])
        status = "observed" if observed and count == observed else "partial" if count else "missing"
        output[key] = dict(
            value=value,
            status=status,
            reason=None if status == "observed" else "字段缺失或无记录",
            valid_rows=str(count),
            observed_rows=str(observed),
        )
    for key, _, _ in RATIOS:
        count = sum(int(part[key]["valid_rows"]) for part in parts)
        n, d = (
            _sum([part[key][field] for part in parts if part[key][field] is not None])
            for field in ("numerator", "denominator")
        )
        value = divide(n, d)
        output[key] = dict(
            value=value,
            status="not_computable"
            if value is None
            else "observed"
            if count == observed
            else "partial",
            reason="无记录或非正分母"
            if value is None
            else None
            if count == observed
            else "只采用分子分母同时有效的行",
            numerator=n,
            denominator=d,
            valid_rows=str(count),
            observed_rows=str(observed),
        )
    return output


def daily_aggregates_can_merge(rows):
    """原行精度包络决定能否分组；BIGINT计数至多19位，超50位保留原窗求和顺序。"""
    integral, scale, count = 19, 0, 0
    for row in rows:
        count += 1
        value = row.get("product_revenue")
        if value is not None:
            value = D(value)
            integral = max(integral, value.adjusted() + 1)
            scale = max(scale, -value.as_tuple().exponent)
    # 额外保留行数可能带来的进位；禁止用已抵消的日小计判定原输入安全。
    return integral + scale + len(str(count)) <= 50


def observed_days(rows, start, end, fields):
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    return {
        r["business_date"] for r in rows if all(r.get(f) is not None and r[f] >= 0 for f in fields)
    } == expected


def risk(rows, end, top):
    with localcontext() as ctx:
        ctx.prec = 50
        return _risk(rows, end, top)


def risk_window_ready(rows, end):
    """仅确认八个日历日的访客/订单可评；不推定上游完整日或无风险。"""
    byday = {r["business_date"]: r for r in rows}
    return all(
        day in byday
        and all(
            byday[day].get(f) is not None and byday[day][f] >= 0 for f in ("visitors", "orders")
        )
        for day in (end - timedelta(days=i) for i in range(risk_rules.BASELINE_DAYS + 1))
    )


def _risk(rows, end, top):
    """命中首分支即返回；基准为T-7..T-1，趋势仅T-6..T，损失是规则估算。"""
    byday = {r["business_date"]: r for r in rows}
    dates = [end - timedelta(days=i) for i in range(risk_rules.BASELINE_DAYS, -1, -1)]
    if not risk_window_ready(rows, end):
        return None
    past = [byday[d] for d in dates[:-1]]
    current = byday[end]
    v, o = D(current["visitors"]), D(current["orders"])
    pv, po = total(past, "visitors"), total(past, "orders")
    avg = pv / risk_rules.BASELINE_DAYS
    cb = divide(po, pv)
    cv = divide(o, v)
    drop = compare(v, avg)
    label = priority = score = kind = field = None
    if v > risk_rules.TRAFFIC_MIN and o == 0:
        label, priority, score, now, baseline, kind, field = (
            "有流量无成交",
            "P1",
            100,
            o,
            po / risk_rules.BASELINE_DAYS,
            "number",
            "orders",
        )
        reasons = ["有流量无成交", "商品承接待核查"]
        action = "核查价格、优惠、评价、详情页和商品可售状态。"
    elif (
        v > risk_rules.TRAFFIC_MIN
        and cb
        and cv is not None
        and cv <= cb * risk_rules.CONVERSION_SEVERE_FACTOR
    ):
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
    elif (
        v >= risk_rules.TRAFFIC_MIN
        and avg >= risk_rules.TRAFFIC_MIN
        and drop is not None
        and drop <= risk_rules.TRAFFIC_SEVERE_CHANGE
        and avg - v >= risk_rules.TRAFFIC_SEVERE_LOSS
    ):
        priority = "P0" if top and po >= risk_rules.TOP_PREVIOUS_ORDERS_MIN else "P1"
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
    elif (
        v > risk_rules.TRAFFIC_MIN
        and cb
        and cv is not None
        and cv <= cb * risk_rules.CONVERSION_MODERATE_FACTOR
    ):
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
    elif (
        v >= risk_rules.TRAFFIC_MIN
        and avg >= risk_rules.TRAFFIC_MIN
        and drop is not None
        and drop <= risk_rules.TRAFFIC_MODERATE_CHANGE
        and avg - v >= risk_rules.TRAFFIC_MODERATE_LOSS
    ):
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
        metric=field,
        baseline_method="ratio_of_totals" if field == "conversion" else "daily_average",
        trend=trend,
        expected_loss=loss,
        reasons=reasons,
        action=action,
        deadline=None,
        status="未启用",
        basis="订单转化=支付订单数÷商品访客数；规则估算，来源完整性未知",
    )
