"""M1整页确定性查询，当前资料归属，真实日历窗、比较覆盖和缺项状态。"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from math import ceil

from .metrics import METRICS, WEIGHTS, aggregate, compare, divide, observed_days, risk, total
from .repository import BoardError
from .source import previous_year


@dataclass(frozen=True)
class BoardQuery:
    start: date
    end: date
    shop: str = ""
    operator: str = ""
    category: str = ""
    level: str = ""
    products: tuple[str, ...] = ()
    keyword: str = ""
    page: int = 1
    size: int = 25
    judgement: str = ""

    def validate(self, repo):
        if (
            not repo.start <= self.start <= self.end <= repo.end
            or self.page < 1
            or self.size not in (12, 24, 25, 50, 100)
            or len(self.products) > 20
        ):
            raise BoardError("INVALID_SCOPE")


def labels(rows, profiles):
    """展示名优先唯一资料，否则只取同K本窗口最近非空日的唯一来源名。"""
    groups = defaultdict(list)
    for row in rows:
        groups[row["listing_id"]].append(row)
    info = {}
    for lid, values in groups.items():
        p = profiles.get(lid, {})
        last = max((r["business_date"] for r in values if r["source_product_name"]), default=None)
        names = {
            r["source_product_name"]
            for r in values
            if r["business_date"] == last and r["source_product_name"]
        }
        info[lid] = dict(
            listing_id=lid,
            shop_id=values[0]["source_shop_id"],
            product_id=values[0]["source_product_id"],
            shop_name=values[0]["source_shop_name"],
            product_name=p.get("product_name") or (next(iter(names)) if len(names) == 1 else None),
            category=p.get("category"),
            level=p.get("level"),
            operator=p.get("operator"),
            field_status=p.get("field_status", {}),
            candidates=p.get("candidates", {}),
            listed_date=None,
            listed_date_status="unknown",
        )
    return info, groups


def matches(item, q):
    for field in ("shop", "operator", "category", "level"):
        wanted = getattr(q, field)
        name = "shop_id" if field == "shop" else field
        if wanted and item.get(name) != wanted:
            return False
    return (not q.products or item["listing_id"] in q.products) and (
        not q.keyword
        or q.keyword.casefold() in f"{item['product_name'] or ''} {item['product_id']}".casefold()
    )


def scope(repo, q, *, with_members=False):
    q.validate(repo)
    rows = repo.rows(repo.start, repo.end)
    profiles = repo.profiles()
    info, _ = labels([r for r in rows if q.start <= r["business_date"] <= q.end], profiles)
    groups = defaultdict(list)
    for row in rows:
        groups[row["listing_id"]].append(row)
    selected = {k: v for k, v in info.items() if matches(v, q)}
    result = selected, {k: groups[k] for k in selected}
    if with_members:
        # 固定收到的范围内匹配过的店集合，不随本/比较期缺行收缩。
        all_info, _ = labels(rows, profiles)
        members = {k: v["shop_id"] for k, v in all_info.items() if matches(v, q)}
        return *result, members
    return result


def selected_rows(groups, start, end):
    return [r for values in groups.values() for r in values if start <= r["business_date"] <= end]


def comparison(current, baseline, keys, map_day):
    expected = {(r["listing_id"], map_day(r["business_date"])) for r in current}
    actual = {(r["listing_id"], r["business_date"]) for r in baseline}
    return (
        bool(current)
        and None not in {d for _, d in expected}
        and expected == actual
        and all(
            all(r.get(k) is not None and r[k] >= 0 for k in keys) for r in (*current, *baseline)
        )
    )


METRIC_INPUTS = {
    "revenue": ("product_revenue",),
    "visitors": ("visitors",),
    "favorites": ("favorites",),
    "buyers": ("buyers",),
    "units": ("units",),
    "aov": ("product_revenue", "buyers"),
    "conversion": ("buyers", "visitors"),
}


def calendar_covered(rows, start, end, products, shops):
    """显式K或固定店集合的日历覆盖；不将缺记录补零或认定来源全集完整。"""
    members = set(products or shops)
    key = "listing_id" if products else "source_shop_id"
    actual = {(r[key], r["business_date"]) for r in rows}
    expected = {
        (member, start + timedelta(days=i))
        for member in members
        for i in range((end - start).days + 1)
    }
    return bool(expected) and actual == expected


def mark_calendar(values, covered):
    if not covered:
        for value in values.values():
            if value["status"] == "observed":
                value.update(status="partial", reason="所选范围缺店铺日或商品日，仅显示已观察小计")
    return values


def overview(repo, q):
    info, groups, members = scope(repo, q, with_members=True)
    shops = set(members.values())

    def covered(rows, start, end):
        return calendar_covered(rows, start, end, q.products, shops)

    current = selected_rows(groups, q.start, q.end)
    n = (q.end - q.start).days + 1
    pstart, pend = q.start - timedelta(days=n), q.start - timedelta(days=1)
    past = selected_rows(groups, pstart, pend)
    ys, ye = previous_year(q.start), previous_year(q.end)
    yr = repo.rows(ys or date(q.start.year - 1, 3, 1), ye or date(q.end.year - 1, 2, 28))
    mapped_dates = {previous_year(x["business_date"]) for x in current}
    year = [r for r in yr if r["listing_id"] in info and r["business_date"] in mapped_dates]
    now, old, prev = (
        aggregate(current, daily=n == 1),
        aggregate(year, daily=n == 1),
        aggregate(past, daily=n == 1),
    )
    current_covered = covered(current, q.start, q.end)
    past_covered = covered(past, pstart, pend)
    year_covered = bool(ys and ye) and covered(year, ys, ye)
    mark_calendar(now, current_covered)
    metrics = {}
    for key, label, kind in METRICS:
        fields = METRIC_INPUTS[key]
        yc = current_covered and year_covered and comparison(current, year, fields, previous_year)
        pc = (
            current_covered
            and past_covered
            and comparison(current, past, fields, lambda d: d - timedelta(days=n))
        )
        value = now[key]
        metrics[key] = dict(
            value,
            label=label,
            type=kind,
            previous=old[key]["value"] if yc else None,
            previous_period=prev[key]["value"] if pc else None,
            yoy=compare(value["value"], old[key]["value"]) if yc else None,
            mom=compare(value["value"], prev[key]["value"]) if pc else None,
            comparison_status="observed_reference" if yc else "missing_comparable_rows",
        )
    series = []
    bydate = defaultdict(list)
    byyear = defaultdict(list)
    for r in current:
        bydate[r["business_date"]].append(r)
    for r in year:
        byyear[r["business_date"]].append(r)
    for i in range(n):
        day = q.start + timedelta(days=i)
        a = aggregate(bydate[day], daily=True)
        b = aggregate(byyear[previous_year(day)], daily=True)
        old_day = previous_year(day)
        ok = (
            covered(bydate[day], day, day)
            and bool(old_day)
            and covered(byyear[old_day], old_day, old_day)
        )
        series.append(
            dict(
                date=day,
                values={k: a[k]["value"] for k, _, _ in METRICS},
                previous={
                    k: b[k]["value"]
                    if ok
                    and comparison(bydate[day], byyear[old_day], METRIC_INPUTS[k], previous_year)
                    else None
                    for k, _, _ in METRICS
                },
            )
        )
    windows = []
    for days in (7, 14, 30):
        a = q.end - timedelta(days=days - 1)
        b = a - timedelta(days=days)
        r1, r0 = selected_rows(groups, a, q.end), selected_rows(groups, b, a - timedelta(days=1))
        av, bv = aggregate(r1), aggregate(r0)
        current_ok = covered(r1, a, q.end)
        calendar_ok = current_ok and covered(r0, b, a - timedelta(days=1)) and b >= repo.start
        mark_calendar(av, current_ok)
        period_metrics = {}
        for k, _, _ in METRICS:
            good = calendar_ok and comparison(
                r1, r0, METRIC_INPUTS[k], lambda d, days=days: d - timedelta(days=days)
            )
            period_metrics[k] = dict(
                av[k],
                change=compare(av[k]["value"], bv[k]["value"]) if good else None,
                comparison_status="observed_reference" if good else "missing_comparable_rows",
            )
        windows.append(
            dict(
                days=days,
                start=a,
                end=q.end,
                previous_start=b,
                previous_end=a - timedelta(days=1),
                metrics=period_metrics,
            )
        )
    seven = q.end - timedelta(days=6)
    before = seven - timedelta(days=7)
    hot = []
    categories = defaultdict(list)
    anomalies = []
    period_revenue = {
        lid: total([r for r in values if q.start <= r["business_date"] <= q.end], "product_revenue")
        for lid, values in groups.items()
    }
    ranked = sorted(
        info, key=lambda k: (-(period_revenue[k] or 0), info[k]["shop_id"], info[k]["product_id"])
    )
    top = set(ranked[: ceil(len(ranked) * 0.2)])
    for lid, item in info.items():
        values = groups[lid]
        r1 = [r for r in values if seven <= r["business_date"] <= q.end]
        r0 = [r for r in values if before <= r["business_date"] < seven]
        a, b = aggregate(r1), aggregate(r0)
        full = observed_days(r1, seven, q.end, ("product_revenue",)) and observed_days(
            r0, before, seven - timedelta(days=1), ("product_revenue",)
        )
        item = dict(
            item,
            revenue=a["revenue"]["value"],
            previous_revenue=b["revenue"]["value"],
            growth=compare(a["revenue"]["value"], b["revenue"]["value"]) if full else None,
            conversion=a["conversion"]["value"],
            coverage="observed_reference" if full else "partial",
        )
        if item["revenue"] is not None and item["revenue"] > 0:
            hot.append(item)
        categories[
            item["category"]
            or ("资料冲突" if item["field_status"].get("category") == "conflict" else "未匹配品类")
        ].append(item)
        if item["level"] and item["level"] not in ("清仓", "未匹配"):
            alert = risk(values, q.end, lid in top)
            if alert:
                anomalies.append(dict(item, alert=alert, period_revenue=period_revenue[lid]))
    hot.sort(key=lambda r: (-r["revenue"], r["shop_id"], r["product_id"]))
    cat = []
    for category, values in categories.items():
        revenue = total(values, "revenue")
        previous = total(values, "previous_revenue")
        if revenue is not None and revenue > 0:
            cat.append(
                dict(
                    category=category,
                    revenue=revenue,
                    growth=compare(revenue, previous)
                    if all(r["coverage"] == "observed_reference" for r in values)
                    else None,
                )
            )
    cat.sort(key=lambda r: (-r["revenue"], r["category"]))
    anomalies.sort(
        key=lambda r: (r["alert"]["priority"], -r["alert"]["score"], r["shop_id"], r["product_id"])
    )
    rising = sorted(
        (r for r in hot if r["level"] == "新品" and r["growth"] is not None and r["growth"] > 0),
        key=lambda r: (-r["growth"], -r["revenue"], r["shop_id"], r["product_id"]),
    )[:3]
    comparison_ids = (
        [lid for lid in q.products if lid in info]
        if q.products
        else ranked[:3]
        if q.category
        else []
    )
    comparison_series = []
    if len(comparison_ids) > 1:
        for lid in comparison_ids:
            points = []
            daily = defaultdict(list)
            for r in groups[lid]:
                if q.start <= r["business_date"] <= q.end:
                    daily[r["business_date"]].append(r)
            for i in range(n):
                day = q.start + timedelta(days=i)
                a = aggregate(daily[day], daily=True)
                points.append(dict(date=day, values={k: a[k]["value"] for k, _, _ in METRICS}))
            comparison_series.append(
                dict(
                    listing_id=lid,
                    label=(
                        f"{info[lid]['product_name'] or info[lid]['product_id']}"
                        f" · {info[lid]['shop_name']}"
                    ),
                    points=points,
                )
            )
    selection = {}
    for field in ("product_name", "shop_name", "operator", "category", "level"):
        unique = {r[field] for r in info.values() if r[field]}
        selection[field] = next(iter(unique)) if len(unique) == 1 else None

    # 当前来源没有完整日证明：数值观察可展示，健康度不越过来源门禁。
    health = dict(
        title="增长待验证",
        tone="neutral",
        detail="来源完整性尚未确认；同比、环比仅在日历与必需字段齐备时显示观察比较，暂不发布健康度定性。",
    )
    return dict(
        metrics=metrics,
        comparison_series=comparison_series,
        comparison_mode="products" if q.products else "category_top3" if q.category else "year",
        selection=selection,
        series=series,
        windows=windows,
        health=health,
        risks=anomalies,
        brief=dict(
            categories=cat[:3],
            products=hot[:3],
            risks=anomalies[:3],
            rising=rising,
            start=seven,
            end=q.end,
            previous_start=before,
        ),
        funnel=[
            dict(label=label, **now[key])
            for key, label in (
                ("views", "商品浏览量"),
                ("visitors", "商品访客"),
                ("favorites", "累计新增收藏量（未扣取消）"),
                ("buyers", "支付买家"),
                ("units", "支付件数"),
            )
        ],
        observed_rows=str(len(current)),
        product_count=str(len({r["listing_id"] for r in current})),
        scope=dict(
            start=q.start,
            end=q.end,
            previous_start=previous_year(q.start),
            previous_end=previous_year(q.end),
        ),
        basis="数仓原值；当前资料归属；完整日未知；比较仅代表匹配观察行",
    )


def score_cards(repo, q, listing_id=None):
    from app.modules.product_scoring.query import published_scores

    published, computation_status = published_scores(repo)
    info, groups = scope(repo, q)
    end = repo.end
    start = end - timedelta(days=6)
    prior = start - timedelta(days=7)
    ads = defaultdict(list)
    for r in repo.rows(start, end, ads=True):
        ads[r["listing_id"]].append(r)
    cards = []
    for lid, item in info.items():
        if item["level"] != "新品" or listing_id and lid != listing_id:
            continue
        values = [r for r in groups[lid] if start <= r["business_date"] <= end]
        previous = [r for r in groups[lid] if prior <= r["business_date"] < start]
        a = aggregate(values)
        b = aggregate(previous)
        ad = ads[lid]

        def adratio(n, d, ad=ad):
            pairs = [
                r
                for r in ad
                if r.get(n) is not None and r.get(d) is not None and r[n] >= 0 and r[d] >= 0
            ]
            return divide(total(pairs, n), total(pairs, d))

        inputs = dict(
            visitors_7d=a["visitors"]["value"],
            product_revenue_7d=a["revenue"]["value"],
            units_7d=a["units"]["value"],
            favorites_7d=a["favorites"]["value"]
            if observed_days(values, start, end, ("favorites",))
            else None,
            score_conversion_rate_7d=a["score_conversion"]["value"],
            revenue_per_visitor_7d=a["uv_value"]["value"],
            ad_roi_7d=adratio("attributed_revenue", "spend"),
            ad_click_through_rate_7d=adratio("clicks", "impressions"),
            ad_click_conversion_rate_7d=adratio("attributed_deals", "clicks"),
            revenue_multiple_7d=divide(a["revenue"]["value"], b["revenue"]["value"])
            if observed_days(values, start, end, ("product_revenue",))
            and observed_days(
                previous, prior, start - timedelta(days=1), ("product_revenue", "orders")
            )
            and (total(previous, "orders") or 0) >= 5
            else None,
        )
        signals = []
        for code, label, weight, kind in WEIGHTS:
            reason = "未取得与当前商品身份/品类相容的四档标准；不复用历史评分当前值"
            if code == "favorites_7d" and inputs[code] is None:
                reason = "收藏新增缺日或字段缺值；不补零，不发布7日评分输入"
            elif code.startswith("ad_"):
                reason = "推广沿来源支付日汇总；原值比率仅参考；四档标准未接入"
            signals.append(
                dict(
                    code=code,
                    label=label,
                    weight=weight,
                    type=kind,
                    value=inputs[code],
                    basic=None,
                    good=None,
                    excellent=None,
                    outstanding=None,
                    grade=None,
                    points=None,
                    status="pending_validation",
                    reason=reason,
                    reference_group=None,
                    sample_count=None,
                )
            )
        cards.append(
            dict(
                item,
                signals=signals,
                start=start,
                end=end,
                decision="unscored",
                decision_label="暂未评分",
                score=None,
                pending_weight="100",
                complete_score=None,
                lifecycle=None,
                season=None,
                revenue=inputs["product_revenue_7d"],
                visitors=inputs["visitors_7d"],
                units=inputs["units_7d"],
                ad_impressions=total(ad, "impressions"),
                ad_roi=inputs["ad_roi_7d"],
                coverage="observed_reference"
                if observed_days(values, start, end, ("visitors", "product_revenue", "units"))
                else "partial",
                reason="相容四档标准未接入；来源完整性仍待核",
                action="先核对资料、原值和参照标准，保留评分位置",
            )
        )
    for item in cards:
        if item["listing_id"] in published:
            item.update(published[item["listing_id"]])
        elif computation_status == "pending_recompute":
            item.update(
                reason="输入已更新，试行评分待重算；不沿用旧分", action="等待相容试行评分发布"
            )
    cards.sort(
        key=lambda r: (
            0 if r["score"] is not None else 1,
            -(r["score"] or 0),
            r["shop_id"],
            r["product_id"],
        )
    )
    if q.judgement:
        cards = [r for r in cards if r["decision"] == q.judgement]
    return dict(
        total=len(cards),
        items=cards[(q.page - 1) * q.size : q.page * q.size],
        start=start,
        end=end,
        page=q.page,
        size=q.size,
        status=computation_status,
        groups=dict(
            complete=0,
            pending=sum(r["score"] is not None for r in cards),
            unscored=sum(r["score"] is None for r in cards),
        ),
    )


def days(repo, q):
    info, groups = scope(repo, q)
    rows = selected_rows(groups, q.start, q.end)
    year_start = previous_year(q.start)
    year_end = previous_year(q.end)
    older = repo.rows(
        year_start or date(q.start.year - 1, 3, 1), year_end or date(q.end.year - 1, 2, 28)
    )
    old = {(r["listing_id"], r["business_date"]): r for r in older}
    rows.sort(
        key=lambda r: (
            -r["business_date"].toordinal(),
            r["source_shop_id"],
            r["source_product_id"],
            r["id"],
        )
    )
    items = []
    profiles = repo.profiles()
    for r in rows[(q.page - 1) * q.size : q.page * q.size]:
        item = info[r["listing_id"]]
        p = old.get((r["listing_id"], previous_year(r["business_date"])))
        items.append(
            dict(
                item,
                date=r["business_date"],
                product_name=profiles.get(r["listing_id"], {}).get("product_name")
                or r["source_product_name"],
                visitors=None if r["visitors"] is None else str(r["visitors"]),
                revenue=r["product_revenue"],
                conversion=divide(r["buyers"], r["visitors"]),
                yoy=compare(r["product_revenue"], p["product_revenue"]) if p else None,
            )
        )
    return dict(
        items=items, total=len(rows), page=q.page, size=q.size, scope=dict(start=q.start, end=q.end)
    )


def options(repo, q, selected_ids=(), new_only=False):
    info, _ = scope(
        repo, replace(q, shop="", operator="", category="", level="", products=(), keyword="")
    )
    values = [r for r in info.values() if (not new_only or r["level"] == "新品")]
    selected = [
        dict(
            value=lid,
            label=(
                f"{info[lid]['product_name'] or info[lid]['product_id']}"
                f" · {info[lid]['shop_name']} · {info[lid]['product_id']}"
            ),
        )
        for lid in selected_ids
        if lid in info
    ]
    shops = sorted({(r["shop_id"], r["shop_name"]) for r in values})
    shop_rows = [r for r in values if not q.shop or r["shop_id"] == q.shop]
    operator = q.operator if any(r["operator"] == q.operator for r in shop_rows) else ""
    operator_rows = [r for r in shop_rows if not operator or r["operator"] == operator]
    category = q.category if any(r["category"] == q.category for r in operator_rows) else ""
    category_rows = [r for r in operator_rows if not category or r["category"] == category]
    level = q.level if any(r["level"] == q.level for r in category_rows) else ""
    candidates = [r for r in category_rows if not level or r["level"] == level]
    candidate_ids = {r["listing_id"] for r in candidates}
    selection = dict(
        operator=operator,
        category=category,
        level=level,
        products=[lid for lid in selected_ids if lid in candidate_ids],
    )
    # 搜索及分页只缩小当前商品候选，不删除仍有效的上游/已选范围。
    candidates = [
        r
        for r in candidates
        if not q.keyword
        or q.keyword.casefold() in f"{r['product_name'] or ''} {r['product_id']}".casefold()
    ]
    return dict(
        selection=selection,
        selected_products=selected,
        shops=[dict(value=k, label=v) for k, v in shops],
        operators=sorted({r["operator"] for r in shop_rows if r["operator"]}),
        categories=sorted({r["category"] for r in operator_rows if r["category"]}),
        levels=sorted({r["level"] for r in category_rows if r["level"]}),
        products=[
            dict(
                value=r["listing_id"],
                label=f"{r['product_name'] or '未命名商品'} · {r['shop_name']} · {r['product_id']}",
            )
            for r in sorted(candidates, key=lambda r: (r["shop_id"], r["product_id"]))[
                (q.page - 1) * q.size : q.page * q.size
            ]
        ],
        total=len(candidates),
    )
