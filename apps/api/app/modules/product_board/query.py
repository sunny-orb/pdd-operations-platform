"""M1整页确定性查询，当前资料归属，真实日历窗、比较覆盖和缺项状态。"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from itertools import chain
from math import ceil

from .actions import build_actions
from .comparison import METRIC_INPUTS, period_comparisons
from .diagnosis import operating_diagnosis
from .filter_projection import level_group, matches_level
from .health import growth_health
from .metrics import (
    METRICS,
    WEIGHTS,
    aggregate,
    compare,
    daily_aggregates_can_merge,
    divide,
    merge_aggregates,
    observed_days,
    risk,
    risk_window_ready,
    total,
)
from .profile_rules import matches_keyword
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
            risk_eligible=p.get(
                "risk_eligible", bool(p.get("level")) and p["level"] not in ("清仓", "未匹配")
            ),
            listed_date=None,
            listed_date_status="unknown",
        )
    return info, groups


def matches(item, q):
    for field in ("shop", "operator", "category"):
        wanted = getattr(q, field)
        name = "shop_id" if field == "shop" else field
        if wanted and item.get(name) != wanted:
            return False
    return (
        matches_level(item, q.level)
        and (not q.products or item["listing_id"] in q.products)
        and matches_keyword(item, q.keyword)
    )


def new_product_catalog(repo, rows, profiles):
    """评分名单以当前明确新品资料为准；无经营事实仍保留完整店铺商品身份。"""
    info, groups = labels(rows, profiles)
    missing = [lid for lid, p in profiles.items() if p.get("level") == "新品" and lid not in info]
    for identity in repo.listing_identities(missing) if missing else ():
        lid = identity["listing_id"]
        names = {
            item["shop_name"]
            for item in info.values()
            if item["shop_id"] == identity["shop_id"] and item["shop_name"]
        }
        identity["shop_name"] = next(iter(names)) if len(names) == 1 else identity["shop_name"]
        info[lid] = dict(profiles[lid], **identity)
    # 同一店铺的无记录链接与事实链接共用唯一店名；无依据时回显店铺ID。
    shop_names = defaultdict(set)
    for item in info.values():
        if item["shop_name"]:
            shop_names[item["shop_id"]].add(item["shop_name"])
    for item in info.values():
        names = shop_names[item["shop_id"]]
        item["shop_name"] = next(iter(names)) if len(names) == 1 else item["shop_id"]
    return {lid: item for lid, item in info.items() if item["level"] == "新品"}, groups


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
        # 总览周期复用未筛选的原始行，避免重复读取同一60日事实。
        return *result, members, rows, info
    return result


def risk_top_products(info, rows, q):
    """用已读取的期间事实排名；单款/搜索只筛展示，同额边界全纳入。

    店铺、运营、品类、等级限定排名池。无可用金额的商品不参与金额排名；
    正常零值保留，不额外添加未经确认的成交规模门槛。无额外数据库查询。
    """
    pool_query = replace(q, products=(), keyword="")
    members = {lid for lid, item in info.items() if matches(item, pool_query)}
    groups = defaultdict(list)
    for row in rows:
        if row["listing_id"] in members and q.start <= row["business_date"] <= q.end:
            groups[row["listing_id"]].append(row)
    amounts = {lid: total(values, "product_revenue") for lid, values in groups.items()}
    amounts = {lid: value for lid, value in amounts.items() if value is not None}
    if not amounts:
        return set()
    threshold = sorted(amounts.values(), reverse=True)[ceil(len(amounts) * 0.2) - 1]
    return {lid for lid, value in amounts.items() if value >= threshold}


def selected_rows(groups, start, end):
    return [r for values in groups.values() for r in values if start <= r["business_date"] <= end]


def collect_risks(info, groups, top, q):
    """商品页和中心共用一次规则扫描；返回全量命中及可评数量，不截前三。"""
    results = []
    counts = dict(
        selected=len(info), excluded=0, evaluated=0, unevaluated=0, flagged=0, unflagged=0
    )
    for lid, item in info.items():
        if not item["risk_eligible"]:
            counts["excluded"] += 1
            continue
        values = groups[lid]
        if not risk_window_ready(values, q.end):
            counts["unevaluated"] += 1
            continue
        counts["evaluated"] += 1
        alert = risk(values, q.end, lid in top)
        if alert:
            period = [r for r in values if q.start <= r["business_date"] <= q.end]
            results.append(dict(item, alert=alert, period_revenue=total(period, "product_revenue")))
    results.sort(
        key=lambda r: (r["alert"]["priority"], -r["alert"]["score"], r["shop_id"], r["product_id"])
    )
    counts["flagged"] = len(results)
    counts["unflagged"] = counts["evaluated"] - counts["flagged"]
    return results, counts


def risk_feed(repo, q):
    """轻量只读风险出口，日期/筛选与商品页一致，不计算七卡、同比或评分。"""
    info, groups, _, rows, period_info = scope(repo, q, with_members=True)
    risks, counts = collect_risks(info, groups, risk_top_products(period_info, rows, q), q)
    status = (
        "empty"
        if not counts["selected"]
        else "not_applicable"
        if counts["excluded"] == counts["selected"]
        else "insufficient_data"
        if not counts["evaluated"] and counts["unevaluated"]
        else "partial"
        if counts["unevaluated"]
        else "evaluated"
    )
    return dict(
        risks=risks,
        total=len(risks),
        counts=counts,
        status=status,
        period=dict(start=q.start, end=q.end),
        evaluation_date=q.end,
        latest_data_date=repo.end,
        baseline_period=dict(start=q.end - timedelta(days=7), end=q.end - timedelta(days=1)),
        trend_period=dict(start=q.end - timedelta(days=6), end=q.end),
        generation_method="rules",
        rule_version="product-risk-online-v1",
        impact_kind="estimated",
    )


def comparisons(current, baseline, map_day):
    """一对观察窗口共用身份配对；七指标仍分别检查必需字段，不放宽比较门禁。"""
    dates = {r["business_date"] for r in current}
    mapped = {day: map_day(day) for day in dates}
    expected = {(r["listing_id"], mapped[r["business_date"]]) for r in current}
    actual = {(r["listing_id"], r["business_date"]) for r in baseline}
    if not current or None in mapped.values() or expected != actual:
        return dict.fromkeys(METRIC_INPUTS, False)
    valid = {
        field: all(r.get(field) is not None and r[field] >= 0 for r in current)
        and all(r.get(field) is not None and r[field] >= 0 for r in baseline)
        for field in {field for fields in METRIC_INPUTS.values() for field in fields}
    }
    return {key: all(valid[field] for field in fields) for key, fields in METRIC_INPUTS.items()}


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


def period_windows(repo, q, loaded_rows):
    """固定结束日的7/14/30日表现；每期独立筛选，不受主卡商品名单截断。

    只读相容版本中必要的60日事实。缺经营日不补零或递补日期；已接收
    历史边界不足时不把部分期间当成完整期间比较。其他区块不使用此集合。
    """
    needed_start = q.end - timedelta(days=59)
    rows = [r for r in loaded_rows if needed_start <= r["business_date"] <= q.end]
    if needed_start < repo.start:
        rows += repo.rows(needed_start, repo.start - timedelta(days=1))
    profiles = repo.profiles()
    received_start = getattr(repo, "versions", {}).get("product_daily", {}).get("period_start")

    def filtered(start, end):
        period = [r for r in rows if start <= r["business_date"] <= end]
        info, _ = labels(period, profiles)
        ids = {lid for lid, item in info.items() if matches(item, q)}
        return [r for r in period if r["listing_id"] in ids]

    windows = []
    for days in (7, 14, 30):
        start = q.end - timedelta(days=days - 1)
        previous_end = start - timedelta(days=1)
        previous_start = start - timedelta(days=days)
        metrics = period_comparisons(
            filtered(start, q.end), filtered(previous_start, previous_end), f"前{days}日"
        )
        for value in metrics.values():
            if received_start and previous_start < received_start:
                current_missing = start < received_start
                value.update(
                    previous=None,
                    change=None,
                    reason="history_not_received",
                    message=f"{'近' if current_missing else '前'}{days}日历史尚未收齐",
                )
                if current_missing:
                    value["value"] = None
            value["status"] = "observed" if value["value"] is not None else "unknown"
            value["comparison_status"] = value["reason"] or "observed_reference"
        windows.append(
            dict(
                days=days,
                start=start,
                end=q.end,
                previous_start=previous_start,
                previous_end=previous_end,
                metrics=metrics,
            )
        )
    return windows


def overview(repo, q):
    info, groups, members, loaded_rows, period_info = scope(repo, q, with_members=True)
    shops = set(members.values())

    def covered(rows, start, end):
        return calendar_covered(rows, start, end, q.products, shops)

    current = selected_rows(groups, q.start, q.end)
    n = (q.end - q.start).days + 1
    pstart, pend = q.start - timedelta(days=n), q.start - timedelta(days=1)
    # 说明区沿用本次计算的日历窗；去年无对应闰日不补成2月28日。
    year_dates = [
        mapped
        for i in range(n)
        if (mapped := previous_year(q.start + timedelta(days=i))) is not None
    ]
    ys, ye = previous_year(q.start), previous_year(q.end)
    yr = repo.rows(ys or date(q.start.year - 1, 3, 1), ye or date(q.end.year - 1, 2, 28))
    mapped_dates = {previous_year(x["business_date"]) for x in current}
    year = [r for r in yr if r["listing_id"] in info and r["business_date"] in mapped_dates]
    merge_safe = daily_aggregates_can_merge(chain(chain.from_iterable(groups.values()), year))
    bydate = defaultdict(list)
    for values in groups.values():
        for row in values:
            bydate[row["business_date"]].append(row)
    daily = {day: aggregate(rows, daily=True) for day, rows in bydate.items()}
    empty = aggregate([])

    def period_values(start, end):
        # 只在本请求已固定的事实集合内复用日聚合；覆盖/字段门禁仍检查原行。
        if not merge_safe:
            return aggregate(selected_rows(groups, start, end))
        return merge_aggregates([value for day, value in daily.items() if start <= day <= end])

    now = period_values(q.start, q.end)
    current_covered = covered(current, q.start, q.end)
    mark_calendar(now, current_covered)
    # 同比沿线上：本期全计，去年只取本期同店同款对应日的实际记录。
    year_keys = {(r["listing_id"], previous_year(r["business_date"])) for r in current}
    card_year = [r for r in year if (r["listing_id"], r["business_date"]) in year_keys]
    # 灰线与七卡去年值共用同店同款对应日集合，普通缺商品日不遮挡整天。
    card_year_daily = defaultdict(list)
    for row in card_year:
        card_year_daily[row["business_date"]].append(row)
    yearly = {day: aggregate(rows, daily=True) for day, rows in card_year_daily.items()}
    year_comparisons = period_comparisons(current, card_year, "去年同期")
    # 环比在前等长日历窗独立筛选，不能被本期商品名单截断，也不向更早日期递补。
    prior_rows = repo.rows(pstart, pend)
    prior_info, _ = labels(prior_rows, repo.profiles())
    prior_ids = {lid for lid, item in prior_info.items() if matches(item, q)}
    comparison_past = [r for r in prior_rows if r["listing_id"] in prior_ids]
    past_comparisons = period_comparisons(current, comparison_past, "前一期")
    received_start = getattr(repo, "versions", {}).get("product_daily", {}).get("period_start")
    if received_start and pstart < received_start:
        for value in past_comparisons.values():
            value.update(change=None, reason="history_not_received", message="前一期历史尚未收齐")
    metrics = {}
    for key, label, kind in METRICS:
        yc, pc = year_comparisons[key], past_comparisons[key]
        value = now[key]
        note = None
        if value["status"] == "partial":
            note = (
                f"{label}：部分记录没有可用数值，仅合计有效部分。"
                if value["valid_rows"] != value["observed_rows"]
                else "本期有些日期没有店铺或商品经营记录，按已有记录统计。"
            )
        metrics[key] = dict(
            value,
            label=label,
            type=kind,
            previous=yc["previous"],
            previous_period=pc["previous"],
            yoy=yc["change"],
            mom=pc["change"],
            yoy_comparison=yc,
            mom_comparison=pc,
            observation_note=note,
            comparison_status="observed_reference" if yc["change"] is not None else yc["reason"],
        )
    series = []
    for i in range(n):
        day = q.start + timedelta(days=i)
        a = daily.get(day, empty)
        old_day = previous_year(day)
        b = yearly.get(old_day, empty)
        series.append(
            dict(
                date=day,
                previous_date=old_day,
                values={k: a[k]["value"] for k, _, _ in METRICS},
                previous={k: b[k]["value"] for k, _, _ in METRICS},
                partial=[
                    k
                    for k, _, _ in METRICS
                    if a[k]["value"] is not None and a[k]["status"] == "partial"
                ],
                previous_partial=[
                    k
                    for k, _, _ in METRICS
                    if b[k]["value"] is not None and b[k]["status"] == "partial"
                ],
            )
        )
    windows = period_windows(repo, q, loaded_rows)
    seven = q.end - timedelta(days=6)
    before = seven - timedelta(days=7)
    hot = []
    categories = defaultdict(list)
    category_facts = defaultdict(list)
    recent_info = {}
    period_revenue = {
        lid: total([r for r in values if q.start <= r["business_date"] <= q.end], "product_revenue")
        for lid, values in groups.items()
    }
    ranked = sorted(
        info, key=lambda k: (-(period_revenue[k] or 0), info[k]["shop_id"], info[k]["product_id"])
    )
    top = risk_top_products(period_info, loaded_rows, q)
    for lid, item in info.items():
        values = groups[lid]
        r1 = [r for r in values if seven <= r["business_date"] <= q.end]
        r0 = [r for r in values if before <= r["business_date"] < seven]
        a = aggregate(r1, only=("revenue", "conversion"))
        previous_revenue = total(r0, "product_revenue")
        full = observed_days(r1, seven, q.end, ("product_revenue",)) and observed_days(
            r0, before, seven - timedelta(days=1), ("product_revenue",)
        )
        item = dict(
            item,
            revenue=a["revenue"]["value"],
            previous_revenue=previous_revenue,
            growth=compare(a["revenue"]["value"], previous_revenue) if full else None,
            conversion=a["conversion"]["value"],
            coverage="observed_reference" if full else "partial",
        )
        recent_info[lid] = item
        if item["revenue"] is not None and item["revenue"] > 0:
            hot.append(item)
        category = item["category"] or (
            "资料冲突" if item["field_status"].get("category") == "conflict" else "未匹配品类"
        )
        categories[category].append(item)
        category_facts[category].extend((*r1, *r0))
    risk_items, _ = collect_risks(info, groups, top, q)
    anomalies = [dict(recent_info[item["listing_id"]], **item) for item in risk_items]
    hot.sort(key=lambda r: (-r["revenue"], r["shop_id"], r["product_id"]))
    cat = []
    for category, values in categories.items():
        revenue = total(values, "revenue")
        previous = total(values, "previous_revenue")
        if revenue is not None and revenue > 0:
            # 品类按品类日覆盖比较，不将新品/停售链接缺日扩大成整个品类不可比。
            # 仅汇总收到的原行；任一日期缺记录或金额无效仍阻断，不补商品日零值。
            facts = category_facts[category]
            expected_days = {before + timedelta(days=i) for i in range(14)}
            if {r["business_date"] for r in facts} != expected_days:
                reason = "两期品类日记录不足"
            elif any(r["product_revenue"] is None or r["product_revenue"] < 0 for r in facts):
                reason = "部分交易金额缺值或无效"
            elif previous is None or previous <= 0:
                reason = "前7日交易额为0"
            else:
                reason = None
            cat.append(
                dict(
                    category=category,
                    revenue=revenue,
                    previous_revenue=previous,
                    growth=compare(revenue, previous) if reason is None else None,
                    comparison_status="observed_reference" if reason is None else "not_comparable",
                    comparison_reason=reason,
                )
            )
    cat.sort(key=lambda r: (-r["revenue"], r["category"]))
    rising_candidates = sorted(
        (r for r in hot if r["level"] == "新品" and r["growth"] is not None and r["growth"] > 0),
        key=lambda r: (-r["growth"], -r["revenue"], r["shop_id"], r["product_id"]),
    )
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

    health = growth_health(current, year)
    return dict(
        metrics=metrics,
        comparison_series=comparison_series,
        comparison_mode="products" if q.products else "category_top3" if q.category else "year",
        selection=selection,
        series=series,
        windows=windows,
        health=health,
        diagnosis=operating_diagnosis(metrics),
        risks=anomalies,
        brief=dict(
            categories=cat[:3],
            products=hot[:3],
            risks=anomalies[:3],
            rising=rising_candidates[:3],
            actions=build_actions(info, groups, anomalies, rising_candidates, q.end),
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
        comparison_periods=dict(
            current=dict(start=q.start, end=q.end),
            yoy=dict(start=year_dates[0], end=year_dates[-1]) if year_dates else None,
            mom=dict(start=pstart, end=pend),
        ),
        scope=dict(
            start=q.start,
            end=q.end,
            previous_start=previous_year(q.start),
            previous_end=previous_year(q.end),
        ),
        basis="数仓原值；当前资料归属；完整日未知；各区块比较范围见对应说明",
    )


def score_cards(repo, q, listing_id=None):
    from app.modules.product_scoring.query import published_scores

    published, computation_status = published_scores(repo)
    q.validate(repo)
    catalog, groups = new_product_catalog(repo, repo.rows(repo.start, repo.end), repo.profiles())
    info = {lid: item for lid, item in catalog.items() if matches(item, q)}
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
        total_count=len(catalog),
        scored_count=sum(published.get(lid, {}).get("score") is not None for lid in catalog),
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


def catalog(repo, q, new_only=False):
    """所选期间的完整轻量目录；不读取经营度量，筛选联动不再反复取数。"""
    q.validate(repo)
    rows, profiles = repo.identity_rows(q.start, q.end), repo.profiles()
    info, _ = new_product_catalog(repo, rows, profiles) if new_only else labels(rows, profiles)
    values = sorted(info.values(), key=lambda r: (r["shop_id"], r["product_id"]))
    return dict(
        shops=[
            dict(value=key, label=name)
            for key, name in sorted({(r["shop_id"], r["shop_name"]) for r in values})
        ],
        items=[
            dict(
                value=r["listing_id"],
                label=f"{r['product_name'] or '未命名商品'} · "
                f"{r['shop_name'] or r['shop_id']} · {r['product_id']}",
                shop=r["shop_id"],
                operator=r.get("operator"),
                category=r.get("category"),
                level=r.get("level"),
                level_group=level_group(r),
                level_details=r.get("candidates", {}).get("level", []),
                search_text=" ".join(
                    (
                        r["product_id"],
                        r["product_name"] or "",
                        *r.get("candidates", {}).get("product_name", []),
                    )
                ),
            )
            for r in values
        ],
        total=len(values),
    )


def options(repo, q, selected_ids=(), new_only=False):
    # 目录不使用经营指标或跨窗分组；已选商品只影响回显，不缩小候选的基础范围。
    replace(q, products=()).validate(repo)
    rows, profiles = repo.identity_rows(q.start, q.end), repo.profiles()
    info, _ = new_product_catalog(repo, rows, profiles) if new_only else labels(rows, profiles)
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
    level = q.level if any(matches_level(r, q.level) for r in category_rows) else ""
    candidates = [r for r in category_rows if matches_level(r, level)]
    levels = {level_group(r) for r in category_rows}
    if level:
        levels.add(level)  # 旧链接保留精确组合，供选框回显，不扩大范围。
    candidate_ids = {r["listing_id"] for r in candidates}
    selection = dict(
        operator=operator,
        category=category,
        level=level,
        products=[lid for lid in selected_ids if lid in candidate_ids],
    )
    # 搜索及分页只缩小当前商品候选，不删除仍有效的上游/已选范围。
    candidates = [r for r in candidates if matches_keyword(r, q.keyword)]
    return dict(
        selection=selection,
        selected_products=selected,
        shops=[dict(value=k, label=v) for k, v in shops],
        operators=sorted({r["operator"] for r in shop_rows if r["operator"]}),
        categories=sorted({r["category"] for r in operator_rows if r["category"]}),
        levels=sorted(levels),
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
