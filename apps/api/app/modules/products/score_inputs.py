"""从相容PG输入读取新品和必要聚合；商品、推广分别聚合，禁止事实扇出。"""

from datetime import date, timedelta

import sqlalchemy as sa

from app.db import tables as t
from app.modules.products.repository import days_between
from app.modules.products.scoring import METRICS, divide, number

PRODUCT_FIELDS = ("visitors", "product_revenue", "units", "favorites", "orders", "buyers")
AD_FIELDS = ("impressions", "spend", "attributed_revenue", "clicks", "attributed_deals")


def candidates(view):
    """唯一等级为新品才进入集合；缺日/缺推广/缺日期仍保留卡片。"""
    p, s = t.product_profiles.c, t.shop_products.c
    stmt = (
        sa.select(
            *(p[k] for k in t.K),
            p.product_name,
            p.category,
            p.level,
            p.operator,
            p.listed_date,
            p.profile_match_status,
            p.field_statuses,
            t.shops.c.shop_name,
            s.source_product_name,
        )
        .select_from(
            t.product_profiles.join(t.shop_products, sa.and_(*(p[k] == s[k] for k in t.K))).join(
                t.shops,
                sa.and_(t.shops.c.platform_code == p.platform_code, t.shops.c.shop_id == p.shop_id),
            )
        )
        .where(
            p.platform_code == "pdd",
            p.level == "新品",
            p.last_changed_version_id == view.versions["product_profile"]["version_id"],
        )
    )
    return [dict(r) for r in view.connection.execute(stmt).mappings()]


def aggregate(c, table, keys, start, end, fields):
    if not keys:
        return {}
    columns = [table.c[k] for k in t.K]
    stmt = (
        sa.select(
            *columns,
            sa.func.count().label("rows"),
            sa.func.array_agg(sa.distinct(table.c.business_date)).label("days"),
            *[
                x
                for f in fields
                for x in (
                    sa.func.sum(table.c[f]).label(f),
                    sa.func.count(table.c[f]).label(f + "_count"),
                    sa.func.bool_or(table.c[f] < 0).label(f + "_negative"),
                )
            ],
        )
        .where(sa.tuple_(*columns).in_(keys), table.c.business_date.between(start, end))
        .group_by(*columns)
    )
    return {tuple(r[k] for k in t.K): dict(r) for r in c.execute(stmt).mappings()}


def read_inputs(view, model, as_of):
    """读取一次一致快照，返回每K的值、字段缺口和判分上下文，不发布结果。"""
    profiles = candidates(view)
    keys = [tuple(r[k] for k in t.K) for r in profiles]
    start, previous_start, previous_end = (
        as_of - timedelta(days=6),
        as_of - timedelta(days=13),
        as_of - timedelta(days=7),
    )
    current = aggregate(view.connection, t.product_days, keys, start, as_of, PRODUCT_FIELDS)
    previous = aggregate(
        view.connection, t.product_days, keys, previous_start, previous_end, PRODUCT_FIELDS
    )
    ads = aggregate(view.connection, t.promotion_records, keys, start, as_of, AD_FIELDS)
    p = t.product_days.c
    first = (
        dict(
            view.connection.execute(
                sa.select(
                    sa.func.concat_ws(":", *(p[k] for k in t.K)), sa.func.min(p.business_date)
                )
                .where(sa.tuple_(*(p[k] for k in t.K)).in_(keys), p.business_date <= as_of)
                .group_by(*(p[k] for k in t.K))
            ).all()
        )
        if keys
        else {}
    )
    results = []
    ad_windows = view.versions["promotion_records"]["requested_range"].get("windows", [])
    ad_imported = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in ad_windows]
    for profile in profiles:
        k = tuple(profile[x] for x in t.K)
        key = ":".join(k)
        raw, before, ad = current.get(k, {}), previous.get(k, {}), ads.get(k, {})
        source_complete = complete_window(view, k, start, as_of, "product_daily")
        previous_complete = complete_window(view, k, previous_start, previous_end, "product_daily")
        promotion_complete = complete_window(view, k, start, as_of, "promotion_records")
        standard = model["parameters"]["products"].get(key)
        scope_reason = None
        if standard and standard.get("category") != profile["category"]:
            standard, scope_reason = None, "threshold_profile_category_changed"
        elif standard and profile["field_statuses"].get("category") == "conflict":
            standard, scope_reason = None, "threshold_profile_category_conflict"
        source_day = date.fromisoformat(model["provenance"]["source_as_of"])
        if standard and as_of < source_day:
            standard, scope_reason = None, "threshold_from_future"
        missing = dict(
            current=sorted(set(days_between(start, as_of)) - set(raw.get("days", []))),
            previous=sorted(
                set(days_between(previous_start, previous_end)) - set(before.get("days", []))
            ),
            promotion=sorted(set(days_between(start, as_of)) - set(ad.get("days", []))),
            promotion_not_imported=[
                d
                for d in days_between(start, as_of)
                if not any(a <= d <= b for a, b in ad_imported)
            ],
        )

        def invalid(row, field, window, missing=missing):
            reasons = []
            if missing[window]:
                reasons.append(window + "_missing_dates")
            if not row.get("rows"):
                reasons.append(window + "_no_records_received")
            if row.get(field + "_count", 0) != row.get("rows", 0) or row.get(field) is None:
                reasons.append(field + "_missing_values")
            if row.get(field + "_negative"):
                reasons.append(field + "_negative_input")
            return reasons

        blocked = {m: [] for m in METRICS}
        values = {}
        direct = dict(
            visitors_7d="visitors",
            product_revenue_7d="product_revenue",
            units_7d="units",
            favorites_7d="favorites",
        )
        for metric, field in direct.items():
            values[metric] = raw.get(field)
            blocked[metric] += invalid(raw, field, "current")
        blocked["favorites_7d"].append("favorite_definition_pending")
        pairs = {
            "score_conversion_rate_7d": (raw, "orders", "visitors", "current"),
            "revenue_per_visitor_7d": (raw, "product_revenue", "visitors", "current"),
            "ad_roi_7d": (ad, "attributed_revenue", "spend", "promotion"),
            "ad_click_through_rate_7d": (ad, "clicks", "impressions", "promotion"),
            "ad_click_conversion_rate_7d": (ad, "attributed_deals", "clicks", "promotion"),
        }
        for metric, (row, n, d, window) in pairs.items():
            blocked[metric] += invalid(row, n, window) + invalid(row, d, window)
            # 有缺值或负数不把不匹配的合计拼成比率；缺日时仅保同窗观察比率并标阻断。
            fields_valid = all(
                row.get(f + "_count", 0) == row.get("rows", 0) and not row.get(f + "_negative")
                for f in (n, d)
            )
            values[metric] = divide(row.get(n), row.get(d)) if fields_valid else None
            if number(row.get(d)) is not None and number(row[d]) <= 0:
                blocked[metric].append("nonpositive_denominator")
            if window == "promotion":
                blocked[metric] += invalid(ad, "impressions", "promotion")
                if missing["promotion_not_imported"]:
                    blocked[metric].append("promotion_outside_imported_range")
                if number(ad.get("impressions")) is not None and number(ad["impressions"]) < 2000:
                    blocked[metric].append("ad_impressions_below_2000")
        growth = "revenue_multiple_7d"
        blocked[growth] += (
            invalid(raw, "product_revenue", "current")
            + invalid(before, "product_revenue", "previous")
            + invalid(before, "orders", "previous")
        )
        values[growth] = (
            divide(raw.get("product_revenue"), before.get("product_revenue"))
            if not blocked[growth]
            else None
        )
        if number(before.get("orders")) is not None and number(before["orders"]) < 5:
            blocked[growth].append("previous_orders_below_5")
        floor = number(standard.get("growth_base_floor")) if standard else None
        if floor is None:
            blocked[growth].append("growth_base_floor_missing")
        elif (
            number(before.get("product_revenue")) is not None
            and number(before["product_revenue"]) < floor
        ):
            blocked[growth].append("previous_revenue_below_growth_floor")
        if (
            number(before.get("product_revenue")) is not None
            and number(before["product_revenue"]) <= 0
        ):
            blocked[growth].append("nonpositive_previous_revenue")
        if not (source_complete and previous_complete):
            blocked[growth].append("source_complete_windows_unverified")
        observed_date = first.get(key)
        listed = profile["listed_date"]
        age = (as_of - listed).days if listed and listed <= as_of else None
        evidence = []
        if not (source_complete and previous_complete and promotion_complete):
            evidence.append("source_completeness_unknown")
        evidence.append("promotion_attribution_description_pending")
        context = dict(
            profile=profile,
            current_window=dict(start_date=start, end_date=as_of),
            previous_window=dict(start_date=previous_start, end_date=previous_end),
            first_product_observed_date=observed_date,
            observed_age_days=(as_of - observed_date).days if observed_date else None,
            observed_range=view.versions["product_daily"]["requested_range"],
            listed_date=listed,
            age_days=age,
            age_basis="listed_date" if age is not None else "unknown",
            lifecycle_status="not_classified" if age is not None else "unknown",
            model_category=standard.get("model_category") if standard else None,
            season=standard.get("season") if standard else None,
            lifecycle=standard.get("lifecycle") if standard else None,
            reference_context_basis="fixed_published_reference"
            if standard
            else "no_applicable_reference",
            threshold_status="fixed_display_trial"
            if standard
            else (scope_reason or "no_applicable_threshold"),
            threshold_source_as_of=source_day,
            threshold_precision=model["provenance"]["precision"],
            current_lifecycle=None,
            complete_through=None,
            coverage=dict(
                missing_dates=missing,
                completeness="unknown",
                source_current_complete=source_complete,
                source_previous_complete=previous_complete,
                promotion_complete=promotion_complete,
            ),
            conversion_numerator="orders",
            roi_basis="total_attributed",
            roi_denominator="total_cost",
            evidence_reasons=evidence,
        )
        inputs = dict(
            values=values,
            current=raw,
            previous=before,
            promotion=ad,
            growth_base_floor=floor,
            blocked=blocked,
        )
        results.append(
            dict(
                key=k,
                inputs=inputs,
                context=context,
                standard={**standard, "status": model["status"]} if standard else {},
            )
        )
    return results


def complete_window(view, key, start, end, dataset):
    """仅采纳来源覆盖记录中的完整声明，不把最大日期/观察齐7日当完整证明。"""
    c = t.dataset_coverage.c
    cache = view.__dict__.setdefault("_score_complete_cache", {})
    cache_key = (key[0], key[1], start, end, dataset)
    if cache_key in cache:
        return cache[cache_key]
    cache[cache_key] = bool(
        view.connection.execute(
            sa.select(
                sa.exists().where(
                    c.version_id == view.versions[dataset]["version_id"],
                    c.platform_code == key[0],
                    c.shop_id == key[1],
                    c.window_from <= start,
                    c.window_to >= end,
                    c.completeness == "complete",
                    c.complete_through >= end,
                )
            )
        ).scalar_one()
    )
    return cache[cache_key]
