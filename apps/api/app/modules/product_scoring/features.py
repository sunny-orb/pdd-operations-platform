"""完整K按日聚合为可追溯双7日特征；缺日/缺字段不补零。"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext


def observations(products, ads, profiles, ranges):
    """来源投影先按完整K/日期校验；缺一天/字段不补0，多推广方式同列分别加总。"""
    product_days, ad_days = defaultdict(dict), defaultdict(lambda: defaultdict(list))
    identities = {}
    for row in products:
        lid, day = row["listing_id"], row["business_date"]
        if day in product_days[lid]:
            raise ValueError("重复商品日，停止试算")
        product_days[lid][day] = row
        identities[lid] = ("pdd", row["source_shop_id"], row["source_product_id"])
    ad_ids = set()
    for row in ads:
        if row["source_row_key"] in ad_ids:
            raise ValueError("重复推广来源行，停止试算")
        ad_ids.add(row["source_row_key"])
        ad_days[row["listing_id"]][row["period_start"]].append(row)

    def full_sum(rows, field, expected):
        if len(rows) != expected or any(r[field] is None or r[field] < 0 for r in rows):
            return None
        return sum((Decimal(r[field]) for r in rows), Decimal(0))

    def ratio(numerator, denominator):
        return (
            numerator / denominator
            if numerator is not None and denominator is not None and denominator > 0
            else None
        )

    output = []
    with localcontext() as context:
        context.prec = 50
        for lid, days in product_days.items():
            profile = profiles.get(lid, {})
            category = profile.get("category")
            for start, finish in ranges:
                for offset in range(6, (finish - start).days + 1):
                    end = start + timedelta(days=offset)
                    dates = [end - timedelta(days=i) for i in range(6, -1, -1)]
                    selected = [days[day] for day in dates if day in days]
                    if not selected and end != ranges[0][1]:
                        continue
                    previous = [
                        days[day - timedelta(days=7)]
                        for day in dates
                        if day - timedelta(days=7) in days
                    ]
                    totals = {
                        field: full_sum(selected, field, 7)
                        for field in (
                            "visitors",
                            "product_revenue",
                            "units",
                            "orders",
                            "favorites",
                            "buyers",
                        )
                    }
                    previous_gmv = full_sum(previous, "product_revenue", 7)
                    previous_orders = full_sum(previous, "orders", 7)
                    ad_rows = [r for day in dates for r in ad_days[lid].get(day, [])]
                    ad_complete = all(day in ad_days[lid] for day in dates)
                    ad_totals = {
                        field: full_sum(ad_rows, field, len(ad_rows))
                        if ad_rows and ad_complete
                        else None
                        for field in (
                            "impressions",
                            "clicks",
                            "spend",
                            "attributed_revenue",
                            "attributed_deals",
                        )
                    }
                    raw_features = dict(
                        visitors_7d=totals["visitors"],
                        product_revenue_7d=totals["product_revenue"],
                        units_7d=totals["units"],
                        favorites_7d=totals["favorites"],
                        buyers_7d=totals["buyers"],
                        orders_7d=totals["orders"],
                        ad_spend_7d=ad_totals["spend"],
                        ad_attributed_revenue_7d=ad_totals["attributed_revenue"],
                        ad_impressions_7d=ad_totals["impressions"],
                        ad_clicks_7d=ad_totals["clicks"],
                        ad_deals_7d=ad_totals["attributed_deals"],
                        previous_revenue_7d=previous_gmv,
                        previous_orders_7d=previous_orders,
                    )
                    output.append(
                        {
                            "platform_code": identities[lid][0],
                            "shop_id": identities[lid][1],
                            "product_id": identities[lid][2],
                            "listing_id": lid,
                            "raw_features": raw_features,
                            "category": category,
                            "level": profile.get("level"),
                            "window_end": end,
                            "complete_7d": len(selected) == 7,
                            "complete_ad_7d": ad_complete,
                            "complete_14d": len(selected) == len(previous) == 7,
                            "source_complete_14d": False,
                            "uv_7": totals["visitors"],
                            "gmv_7": totals["product_revenue"],
                            "items_7": totals["units"],
                            "fav_7": totals["favorites"],
                            "cvr_7": ratio(totals["orders"], totals["visitors"]),
                            "uv_value_7": ratio(totals["product_revenue"], totals["visitors"]),
                            "ad_roi_7": ratio(ad_totals["attributed_revenue"], ad_totals["spend"]),
                            "ad_ctr_7": ratio(ad_totals["clicks"], ad_totals["impressions"]),
                            "ad_cvr_7": ratio(ad_totals["attributed_deals"], ad_totals["clicks"]),
                            "ad_impressions_7": ad_totals["impressions"],
                            "roi_invalid_7": int(
                                ad_totals["attributed_revenue"] is None
                                or not (ad_totals["spend"] or 0) > 0
                            ),
                            "ctr_invalid_7": int(
                                ad_totals["clicks"] is None
                                or not (ad_totals["impressions"] or 0) > 0
                            ),
                            "ad_cvr_invalid_7": int(
                                ad_totals["attributed_deals"] is None
                                or not (ad_totals["clicks"] or 0) > 0
                            ),
                            "gmv_growth_7": ratio(totals["product_revenue"], previous_gmv),
                            "gmv_prev7": previous_gmv,
                            "orders_prev7": previous_orders,
                        }
                    )
    return output
