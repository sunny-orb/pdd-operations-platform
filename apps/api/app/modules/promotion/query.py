"""推广筛选与汇总：Decimal、支付日及相容渠道覆盖，API不回源。"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from math import ceil
from uuid import UUID

from app.modules.product_board.metrics import compare, divide, total
from app.modules.product_board.repository import BoardError
from app.modules.sales.source_policy import SHOPS

CHANNELS = {"product_promotion": "商品推广", "star_shop": "明星店铺"}
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
MAIN_KEYS = ("spend", "revenue", "roi", "impressions", "clicks", "ctr", "cvr", "cpc")


@dataclass(frozen=True)
class PromotionQuery:
    start: date
    end: date
    mode: str = "store"
    shop: str = ""
    channel: str = ""
    category: str = ""
    level: str = ""
    operator: str = ""
    keyword: str = ""
    products: tuple[str, ...] = ()
    page: int = 1
    size: int = 25

    def validate(self, repo):
        if (
            repo.mode != self.mode
            or self.start > self.end
            or (self.end - self.start).days >= 366
            or self.page < 1
            or self.page > 100000
            or self.size not in ((25,) if self.mode == "store" else (25, 50, 100))
            or self.channel not in ("", *CHANNELS)
            or self.shop
            and self.shop not in SHOPS
            or len(self.products) > 20
            or not repo.start
            or not repo.end
            or not repo.start <= self.start <= self.end <= repo.end
        ):
            raise BoardError("INVALID_PROMOTION_SCOPE")
        if self.mode == "store" and any(
            (self.category, self.level, self.operator, self.keyword, self.products)
        ):
            raise BoardError("STORE_PRODUCT_FILTER_NOT_ALLOWED")
        if self.mode == "product" and self.channel:
            raise BoardError("PRODUCT_CHANNEL_FILTER_NOT_ALLOWED")
        validate_ids(self.products)
        if self.products and any(
            lid not in {r.get("listing_id") for r in repo.rows} for lid in self.products
        ):
            raise BoardError("UNKNOWN_LISTING_ID")


def validate_ids(ids):
    if len(ids) > 20:
        raise BoardError("INVALID_SELECTED_IDS")
    try:
        for value in ids:
            UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise BoardError("INVALID_SELECTED_IDS") from exc


def dates_between(start, end):
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def previous_year(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return None


def labels(repo, rows):
    """推广原行独立驱动身份与名称；资料先唯一共识，查询窗内最近日原名不猜冲突。"""
    groups = defaultdict(list)
    for row in rows:
        if row.get("listing_id"):
            groups[row["listing_id"]].append(row)
    result = {}
    for lid, values in groups.items():
        profile = repo.profiles.get(lid, {})
        latest = max((r["date"] for r in values if r["source_product_name"]), default=None)
        names = {
            r["source_product_name"]
            for r in values
            if r["date"] == latest and r["source_product_name"]
        }
        result[lid] = dict(
            listing_id=lid,
            shop_id=values[0]["source_shop_id"],
            product_id=values[0]["source_product_id"],
            shop_name=values[0]["source_shop_name"],
            product_name=profile.get("product_name")
            or (next(iter(names)) if len(names) == 1 else None),
            category=profile.get("category"),
            level=profile.get("level"),
            operator=profile.get("operator"),
            field_status=profile.get("field_status", {}),
        )
    return result


def matches(info, q, ignore=""):
    for field in ("shop", "category", "level", "operator"):
        wanted = getattr(q, field)
        if (
            field != ignore
            and wanted
            and info.get("shop_id" if field == "shop" else field) != wanted
        ):
            return False
    return (ignore == "products" or not q.products or info.get("listing_id") in q.products) and (
        ignore == "keyword"
        or not q.keyword
        or q.keyword.casefold()
        in f"{info.get('product_id', '')} {info.get('product_name') or ''}".casefold()
    )


def select(repo, q, *, ignore="", date_scope=True):
    if repo.mode == "store":
        return [
            r
            for r in repo.rows
            if (not q.shop or r["source_shop_id"] == q.shop)
            and (not q.channel or r["channel_code"] == q.channel)
            and (not date_scope or q.start <= r["date"] <= q.end)
        ]
    label_rows = [r for r in repo.rows if q.start <= r["date"] <= q.end]
    info = labels(repo, label_rows)
    ids = {lid for lid, item in info.items() if matches(item, q, ignore)}
    return [
        r
        for r in repo.rows
        if r.get("listing_id") in ids and (not date_scope or q.start <= r["date"] <= q.end)
    ]


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


def aggregate(rows, *, strict=False, cross=False):
    """分母只配对同一原行；缺值留小计，店铺净额/比率缺项不可冒充完整值。"""
    result = {}
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
        if strict and key == "net_revenue" and len(valid) != len(rows):
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
        n, d = total(paired, numerator), total(paired, denominator)
        value = divide(n, d)
        state = (
            "observed"
            if rows and len(paired) == len(rows)
            else "partial"
            if paired
            else "missing"
            if rows
            else "no_records"
        )
        reason = None if state == "observed" else "仅使用分子分母同时有效的原行"
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


def expected_store(q):
    return [
        (sid, channel)
        for sid in ([q.shop] if q.shop else SHOPS)
        for channel in ([q.channel] if q.channel else CHANNELS)
        if channel != "star_shop" or sid == "663599807"
    ]


def coverage(repo, q, rows):
    days = dates_between(q.start, q.end)
    if repo.mode == "store":
        actual = {(r["date"], r["source_shop_id"], r["channel_code"]) for r in rows}
        missing = [
            dict(date=d, shop_id=sid, shop_name=SHOPS[sid][1], channel=ch, reason="暂无记录")
            for d in days
            for sid, ch in expected_store(q)
            if (d, sid, ch) not in actual
        ]
    else:
        # 固定来源范围中符合身份/资料的对象；不按某一天无行把对象从覆盖分母移除。
        info = labels(repo, [r for r in repo.rows if repo.start <= r["date"] <= repo.end])
        members = {lid for lid, item in info.items() if matches(item, q)}
        actual = {(r["date"], r["listing_id"]) for r in rows}
        missing = [
            dict(date=d, listing_id=lid, reason="暂无记录，缺行不视为零")
            for d in days
            for lid in sorted(members)
            if (d, lid) not in actual
        ]
    return dict(
        missing_count=len(missing),
        missing=missing[:200],
        missing_truncated=len(missing) > 200,
        observed_days=len({r["date"] for r in rows}),
        expected_days=len(days),
        complete_through=None,
        completeness="unknown",
    )


def cross_channel(q):
    return not q.channel and any(ch == "star_shop" for _, ch in expected_store(q))


def all_metrics(repo, q, rows):
    """渠道独立可加；合计比率要求所选店铺支付日的全部适用渠道齐备。"""
    result = aggregate(
        rows, strict=repo.mode == "store", cross=repo.mode == "store" and cross_channel(q)
    )
    if repo.mode == "store":
        missing = coverage(repo, q, rows)["missing_count"]
        if missing:
            for m in result.values():
                m["missing_shop_channel_days"] = missing
                if m["status"] == "observed":
                    m.update(status="partial", reason="适用店铺渠道日缺记录；当前为已知记录小计")
            if cross_channel(q):
                for key in ("roi", "cvr"):
                    result[key].update(
                        value=None,
                        status="incomplete_channel_coverage",
                        reason="所选支付日的适用渠道记录不齐，不以部分渠道计算合计比率",
                    )
    return result


def options(repo, q, selected=()):
    q.validate(repo)
    validate_ids(selected)
    channels = [dict(value=k, label=v) for k, v in CHANNELS.items()]
    if repo.mode == "store":
        if selected:
            raise BoardError("STORE_PRODUCT_FILTER_NOT_ALLOWED")
        return dict(
            shops=[dict(value=k, label=v[1]) for k, v in SHOPS.items()],
            channels=channels,
            products=[],
            selected_products=[],
            categories=[],
            levels=[],
            operators=[],
            total=0,
        )
    in_range = [r for r in repo.rows if q.start <= r["date"] <= q.end]
    info = labels(repo, in_range)

    def values(field):
        return sorted({r[field] for r in info.values() if r.get(field) and matches(r, q, field)})

    candidates = [r for r in info.values() if matches(r, q, "products")]
    candidates.sort(key=lambda r: (r["shop_id"], r["product_id"], r["listing_id"]))

    def option(r):
        return dict(
            r,
            value=r["listing_id"],
            label=(
                f"{r['product_id']} · {r['product_name'] or '名称缺失'} · "
                f"{r['shop_name'] or r['shop_id']}"
            ),
        )

    all_info = labels(repo, [r for r in repo.rows if repo.start <= r["date"] <= repo.end])
    if any(lid not in all_info for lid in (*q.products, *selected)):
        raise BoardError("UNKNOWN_LISTING_ID")
    return dict(
        shops=[
            dict(value=s, label=SHOPS[s][1])
            for s in sorted({r["shop_id"] for r in info.values() if matches(r, q, "shop")})
        ],
        channels=[],
        products=[option(r) for r in candidates[:100]],
        selected_products=[
            option(all_info[lid]) for lid in dict.fromkeys((*q.products, *selected))
        ],
        categories=values("category"),
        levels=values("level"),
        operators=values("operator"),
        total=len(candidates),
    )


def overview(repo, q):
    q.validate(repo)
    rows = select(repo, q)
    current = all_metrics(repo, q, rows)
    span = (q.end - q.start).days + 1
    prior_q = replace(q, start=q.start - timedelta(days=span), end=q.start - timedelta(days=1))
    # 身份先固定当前窗，比较窗不以当期是否有经营行决定取数。
    selected_all = select(repo, q, date_scope=False)
    before = [r for r in selected_all if prior_q.start <= r["date"] <= prior_q.end]
    previous = all_metrics(repo, prior_q, before)
    current_coverage = coverage(repo, q, rows)
    previous_coverage = coverage(repo, prior_q, before)
    for key, m in current.items():
        m["previous"] = previous[key]["value"]
        m["change"] = (
            compare(m["value"], previous[key]["value"])
            if (
                m["status"] == previous[key]["status"] == "observed"
                and not current_coverage["missing_count"]
                and not previous_coverage["missing_count"]
            )
            else None
        )
    series = []
    by_date = defaultdict(list)
    for r in selected_all:
        by_date[r["date"]].append(r)
    for day in dates_between(q.start, q.end):
        prior = previous_year(day)
        daily = by_date[day]
        prior_rows = by_date.get(prior, []) if prior else []
        c = coverage(repo, replace(q, start=day, end=day), daily)
        pc = coverage(repo, replace(q, start=prior, end=prior), prior_rows) if prior else None
        previous_q = replace(q, start=prior, end=prior) if prior else q
        prev = all_metrics(repo, previous_q, prior_rows if pc and not pc["missing_count"] else [])
        series.append(
            dict(
                date=day,
                metrics=all_metrics(repo, replace(q, start=day, end=day), daily),
                previous_date=prior,
                previous=prev,
                missing=bool(c["missing_count"] or not daily),
                coverage=c,
            )
        )
    top = []
    if repo.mode == "product":
        for point in series:
            m = point["metrics"]
            if (
                m["spend"]["value"] is not None
                and m["spend"]["value"] > 0
                and m["roi"]["value"] is not None
            ):
                top.append(
                    dict(
                        date=point["date"],
                        roi=m["roi"]["value"],
                        spend=m["spend"]["value"],
                        revenue=m["revenue"]["value"],
                    )
                )
    channels = []
    if repo.mode == "store":
        for code, label in CHANNELS.items():
            if q.channel and q.channel != code:
                continue
            channel_rows = [r for r in rows if r["channel_code"] == code]
            metrics = all_metrics(repo, replace(q, channel=code), channel_rows)
            channels.append(
                dict(
                    channel=code,
                    label=label,
                    metrics={k: metrics[k] for k in MAIN_KEYS},
                    net={k: metrics[k] for k in ("net_revenue", "net_roi")},
                )
            )
    return dict(
        metrics={k: current[k] for k in MAIN_KEYS},
        net={k: current[k] for k in ("net_revenue", "net_roi")},
        series=series,
        funnel=[
            dict(key=k, label=SPECS[k][0], value=current[k]["value"], status=current[k]["status"])
            for k in ("impressions", "clicks", "deals")
        ]
        if repo.mode == "product"
        else [],
        top5=sorted(top, key=lambda r: (r["roi"], r["date"]))[:5],
        coverage=current_coverage,
        current_record_count=str(len(rows)),
        dataset_record_count=str(len(repo.rows)),
        windows=[
            dict(role="current", start=q.start, end=q.end),
            dict(role="previous", start=prior_q.start, end=prior_q.end),
        ],
        channel_summaries=channels,
    )


def day_item(rows, strict=False):
    metrics = aggregate(rows, strict=strict)
    return {key: value["value"] for key, value in metrics.items()}


def days(repo, q):
    q.validate(repo)
    rows = select(repo, q)
    items = []
    if repo.mode == "store":
        for r in sorted(
            rows,
            key=lambda r: (r["date"], r["source_shop_id"], r["channel_code"], r["source_row_key"]),
            reverse=True,
        ):
            items.append(
                dict(
                    date=r["date"],
                    shop_id=r["source_shop_id"],
                    shop_name=r["source_shop_name"],
                    channel=r["channel_code"],
                    channel_label=CHANNELS[r["channel_code"]],
                    source=repo.versions[
                        next(
                            k
                            for k, v in repo.versions.items()
                            if v["id"] == r["dataset_version_id"]
                        )
                    ]["source_locator"],
                    **day_item([r], True),
                )
            )
    else:
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["date"]].append(row)
        items = [dict(date=d, **day_item(grouped[d])) for d in sorted(grouped, reverse=True)]
    total_rows = len(items)
    return dict(
        items=items[(q.page - 1) * q.size : q.page * q.size],
        total=total_rows,
        page=q.page,
        size=q.size,
        total_pages=max(1, ceil(total_rows / q.size)),
        row_grain="shop_channel_day" if repo.mode == "store" else "day",
    )
