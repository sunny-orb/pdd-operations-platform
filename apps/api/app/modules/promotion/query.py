"""推广筛选与汇总：Decimal、支付日及相容渠道覆盖，API不回源。"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from uuid import UUID

from app.modules.product_board.filter_projection import level_group, matches_level
from app.modules.product_board.metrics import compare
from app.modules.product_board.profile_rules import matches_keyword
from app.modules.product_board.repository import BoardError
from app.modules.sales.source_policy import SHOPS, display_shop_name

from .metrics import SPECS, product_metrics, store_metrics

CHANNELS = {"product_promotion": "商品推广", "star_shop": "明星店铺"}
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
        if self.products and any(lid not in repo.listing_ids for lid in self.products):
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
            shop_name=display_shop_name(values[0]["source_shop_id"], values[0]["source_shop_name"]),
            product_name=profile.get("product_name")
            or (next(iter(names)) if len(names) == 1 else None),
            category=profile.get("category"),
            level=profile.get("level"),
            operator=profile.get("operator"),
            field_status=profile.get("field_status", {}),
            candidates=profile.get("candidates", {}),
        )
    return result


def window_labels(repo, start, end):
    """所选窗原名与固定来源范围原名分别复用；保持原行顺序及最新日冲突规则。"""
    key = (start, end)
    if key not in repo.label_cache:
        repo.label_cache[key] = labels(repo, repo.identity_rows(start, end, ordered=True))
    return repo.label_cache[key]


def matches(info, q, ignore=""):
    for field in ("shop", "category", "operator"):
        wanted = getattr(q, field)
        actual = info.get("shop_id" if field == "shop" else field)
        if field != ignore and wanted and actual != wanted:
            return False
    return (
        (ignore == "level" or matches_level(info, q.level))
        and (ignore == "products" or not q.products or info.get("listing_id") in q.products)
        and (ignore == "keyword" or matches_keyword(info, q.keyword))
    )


def select(repo, q, *, ignore="", date_scope=True, windows=None):
    if windows is None and date_scope:
        windows = [(q.start, q.end)]
    if repo.mode == "store":
        return [
            r
            for r in repo.rows_for_windows(windows)
            if (not q.shop or r["source_shop_id"] == q.shop)
            and (not q.channel or r["channel_code"] == q.channel)
        ]
    info = window_labels(repo, q.start, q.end)
    ids = {lid for lid, item in info.items() if matches(item, q, ignore)}
    return repo.rows_for_windows(windows, listing_ids=ids)


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
        key = (q.shop, q.category, q.level, q.operator, q.keyword, q.products)
        if key not in repo.coverage_member_cache:
            info = window_labels(repo, repo.start, repo.end)
            repo.coverage_member_cache[key] = sorted(
                lid for lid, item in info.items() if matches(item, q)
            )
        members = repo.coverage_member_cache[key]
        actual = {(r["date"], r["listing_id"]) for r in rows}
        missing = [
            dict(date=d, listing_id=lid, reason="暂无记录，缺行不视为零")
            for d in days
            for lid in members
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
    """按模式汇总；商品独立累计已知分子分母，店铺保留严格空值规则。"""
    result = (
        store_metrics(rows, include_star=cross_channel(q))
        if repo.mode == "store"
        else product_metrics(rows)
    )
    if repo.mode == "store":
        missing = coverage(repo, q, rows)["missing_count"]
        if missing:
            for m in result.values():
                m["missing_shop_channel_days"] = missing
                if m["status"] == "observed":
                    m.update(status="partial", reason="按已有推广记录统计；部分日期暂无数据")
    return result


def product_option(row, *, catalog=False):
    """共享商品选项投影：目录与已选回显保持同一等级、名称及字段规则。"""
    result = dict(
        row,
        level=level_group(row),
        level_details=row.get("candidates", {}).get("level", []),
        value=row["listing_id"],
        label=(
            f"{row['product_id']} · {row['product_name'] or '名称缺失'} · "
            f"{row['shop_name'] or row['shop_id']}"
        ),
    )
    if not catalog:
        return result
    # 只传可筛选资料，避免把原始行、金额或资料冲突明细发送到前端。
    return {
        **{
            key: result[key]
            for key in (
                "value",
                "listing_id",
                "label",
                "product_id",
                "product_name",
                "shop_id",
                "category",
                "level",
                "level_details",
                "operator",
            )
        },
        "search_names": row.get("candidates", {}).get("product_name", []),
    }


def selected_options(repo, selected=()):
    """只恢复已发布身份的显示名；不依赖当前日期，也不将其加入当前日期目录。"""
    if repo.mode != "product":
        raise BoardError("STORE_PRODUCT_FILTER_NOT_ALLOWED")
    validate_ids(selected)
    if not selected:
        return dict(products=[])
    ids = tuple(dict.fromkeys(selected))
    info = labels(repo, repo.identity_rows(ordered=True, listing_ids=ids))
    if any(lid not in info for lid in ids):
        raise BoardError("UNKNOWN_LISTING_ID")
    return dict(products=[product_option(info[lid], catalog=True) for lid in ids])


def options(repo, q, selected=(), *, catalog=False):
    """目录模式只返回所选日期的完整去重身份/资料，不下发经营事实；普通候选兼容原接口。"""
    if catalog and repo.mode == "product":
        q = replace(q, shop="", category="", level="", operator="", keyword="", products=())
        selected = ()
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
    info = (
        labels(repo, repo.identity_rows(q.start, q.end))
        if catalog
        else window_labels(repo, q.start, q.end)
    )

    def values(field):
        return sorted(
            {
                level_group(r) if field == "level" else r[field]
                for r in info.values()
                if (field == "level" or r.get(field)) and matches(r, q, field)
            }
        )

    candidates = [r for r in info.values() if matches(r, q, "products")]
    candidates.sort(key=lambda r: (r["shop_id"], r["product_id"], r["listing_id"]))

    all_info = {} if catalog else window_labels(repo, repo.start, repo.end)
    if any(lid not in all_info for lid in (*q.products, *selected)):
        raise BoardError("UNKNOWN_LISTING_ID")
    return dict(
        shops=(
            [dict(value=k, label=v[1]) for k, v in SHOPS.items()]
            if catalog
            else [
                dict(value=s, label=SHOPS[s][1])
                for s in sorted({r["shop_id"] for r in info.values() if matches(r, q, "shop")})
            ]
        ),
        channels=[],
        products=[
            product_option(r, catalog=catalog)
            for r in (candidates if catalog else candidates[:100])
        ],
        selected_products=[
            product_option(all_info[lid], catalog=catalog)
            for lid in dict.fromkeys((*q.products, *selected))
        ],
        categories=values("category"),
        levels=values("level"),
        operators=values("operator"),
        total=len(candidates),
    )


def period_change(current, previous, *, history_available):
    """商品三张累计卡比较各期已有值；缺行不拦截，空值、零基数和历史不足分开。"""
    if current["value"] is None:
        state = "no_current"
    elif previous["value"] is None:
        state = "no_previous"
    elif not history_available:
        state = "history_short"
    elif previous["value"] <= 0:
        state = "zero_base" if previous["value"] == 0 else "invalid_base"
    else:
        current["change"] = compare(current["value"], previous["value"])
        state = (
            "available" if current["status"] == previous["status"] == "observed" else "reference"
        )
    current["comparison_status"] = state


def roi_excluded_rows(rows):
    """兼容旧接口名，返回缺项说明；已知成交仍参与商品ROI，不修改原始空值。"""
    without_spend = [r for r in rows if r["spend"] is None]
    return dict(
        missing_spend_rows=len(without_spend),
        missing_revenue_rows=sum(r["attributed_revenue"] is None for r in rows),
        revenue_without_spend=sum(
            (r["attributed_revenue"] for r in without_spend if r["attributed_revenue"] is not None),
            Decimal(0),
        ),
    )


def overview(repo, q):
    q.validate(repo)
    span = (q.end - q.start).days + 1
    prior_q = replace(q, start=q.start - timedelta(days=span), end=q.start - timedelta(days=1))
    # 本期及同比沿当前身份；商品环比独立筛选前期，不能漏掉前期独有商品。
    prior_days = [previous_year(day) for day in dates_between(q.start, q.end)]
    prior_days = [day for day in prior_days if day is not None]
    windows = [(q.start if repo.mode == "product" else prior_q.start, q.end)]
    if prior_days:
        windows.append((min(prior_days), max(prior_days)))
    selected_all = select(repo, q, windows=windows)
    rows = [r for r in selected_all if q.start <= r["date"] <= q.end]
    current = all_metrics(repo, q, rows)
    before = (
        select(repo, prior_q)
        if repo.mode == "product"
        else [r for r in selected_all if prior_q.start <= r["date"] <= prior_q.end]
    )
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
        if repo.mode == "product" and key in ("spend", "impressions", "clicks"):
            period_change(m, previous[key], history_available=prior_q.start >= repo.start)
    series = []
    by_date = defaultdict(list)
    for r in selected_all:
        by_date[r["date"]].append(r)
    for day in dates_between(q.start, q.end):
        prior = previous_year(day)
        daily = by_date[day]
        prior_rows = by_date.get(prior, []) if prior else []
        c = coverage(repo, replace(q, start=day, end=day), daily)
        previous_q = replace(q, start=prior, end=prior) if prior else q
        # 商品同期按同店同商品逐日对应；真实零保留，缺商品日不遮挡整天。
        # listing_id包含店铺身份；成员集合不去掉同商品的其他推广方式事实。
        daily_members = {row["listing_id"] for row in daily} if repo.mode == "product" else set()
        prior_available = (
            [row for row in prior_rows if row["listing_id"] in daily_members]
            if repo.mode == "product"
            else prior_rows
        )
        prev = all_metrics(repo, previous_q, prior_available)
        series.append(
            dict(
                date=day,
                metrics=all_metrics(repo, replace(q, start=day, end=day), daily),
                previous_date=prior,
                previous=prev,
                missing=not daily,
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
                        roi_status=m["roi"]["status"],
                        roi_exclusions=roi_excluded_rows(by_date[point["date"]]),
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
    # 兼容字段记录当前筛选的缺项范围，不再表示剔除的成交。
    roi_exclusions = roi_excluded_rows(rows) if repo.mode == "product" else None
    return dict(
        roi_exclusions=roi_exclusions,
        historical_supplements=[
            dict(
                r,
                status="outside_range"
                if (
                    not str(q.start) <= r["date"] <= str(q.end)
                    or (q.shop and r["shop_id"] != SHOPS[q.shop][0])
                    or q.channel == "star_shop"
                )
                else r["status"],
            )
            for r in repo.supplements
        ]
        if repo.mode == "store"
        else [],
        metrics={k: current[k] for k in MAIN_KEYS},
        net={k: current[k] for k in ("net_revenue", "net_roi")},
        series=series,
        funnel=[
            dict(key=k, label=SPECS[k][0], value=current[k]["value"], status=current[k]["status"])
            for k in ("impressions", "clicks", "deals")
        ]
        if repo.mode == "product"
        else [],
        # 原值排序，完全同值时较新日期优先；展示舍入不参与排名。
        top5=sorted(top, key=lambda r: (r["roi"], -r["date"].toordinal()))[:5],
        coverage=current_coverage,
        current_record_count=str(len(rows)),
        dataset_record_count=str(sum(repo.date_counts.values())),
        windows=[
            dict(role="current", start=q.start, end=q.end),
            dict(role="previous", start=prior_q.start, end=prior_q.end),
        ],
        channel_summaries=channels,
    )


def day_item(rows, strict=False):
    metrics = store_metrics(rows) if strict else product_metrics(rows)
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
                    shop_name=display_shop_name(r["source_shop_id"], r["source_shop_name"]),
                    channel=r["channel_code"],
                    channel_label=CHANNELS[r["channel_code"]],
                    # 历史行已在导入时确认来源；只缩短展示标签，不改生效来源和数值。
                    source_label={
                        "飞书历史补齐": "飞书",
                        "原版停投确认记录": "人工确认",
                    }.get(r.get("source_channel_label"), "数仓"),
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
