"""店铺其余指标的相容分子分母、独立日期和逐来源覆盖。"""

import re
from datetime import date, timedelta

import sqlalchemy as sa

from .contracts import SalesError
from .datasets import SPECS
from .query import meta, number, ratio, total
from .repository import read_snapshot
from .source_policy import APPROVED_START, SHOPS, calendar_through


def source_info(snapshot, code):
    cached = snapshot.setdefault("source_info", {})
    if code in cached:
        return cached[code]
    spec = SPECS[code]
    v = snapshot["versions"].get(code)
    if not v:
        return dict(
            state="not_connected",
            observed_from=None,
            observed_through=None,
            source_updated_text=None,
            last_success_at=None,
            last_attempt_status=None,
        )
    c = snapshot["connection"]
    lo, hi = c.execute(
        sa.text(
            f"SELECT min({spec.date_column}),max({spec.date_column}) FROM "
            f"public.{spec.fact_table} WHERE dataset_version_id=:v"
        ),
        dict(v=v),
    ).one()
    success = (
        c.execute(
            sa.text(
                "SELECT finished_at,source_updated_text FROM public.task_runs WHERE "
                "result_version_id=:v AND status='succeeded' ORDER BY finished_at DESC "
                "LIMIT 1"
            ),
            dict(v=v),
        )
        .mappings()
        .first()
    )
    attempt = c.execute(
        sa.text(
            "SELECT status FROM public.task_runs WHERE job_code=:job ORDER BY "
            "queued_at DESC,id DESC LIMIT 1"
        ),
        dict(job="sync_" + code),
    ).scalar()
    cached[code] = dict(
        state="observed",
        observed_from=str(lo) if lo else None,
        observed_through=str(hi) if hi else None,
        source_updated_text=success["source_updated_text"] if success else None,
        last_success_at=success["finished_at"].isoformat() if success else None,
        last_attempt_status=attempt,
    )
    return cached[code]


def scoped_rows(s, code, q, shops):
    if code not in s["versions"]:
        return []
    spec = SPECS[code]
    return (
        s["connection"]
        .execute(
            sa.text(
                f"SELECT * FROM public.{spec.fact_table} WHERE dataset_version_id=:v AND"
                f" {spec.date_column} BETWEEN :lo AND :hi AND shop_id = ANY(:shops)"
            ),
            dict(v=s["versions"][code], lo=q.date_from, hi=q.date_to, shops=shops),
        )
        .mappings()
        .all()
    )


def enrich_metrics(s, q, data, shops):
    """退款仅匹配有销售/退款值的店铺日；推广分渠道，跨渠道成交未确认不合并ROI。"""
    refunds = scoped_rows(s, "shop_refunds", q, shops)
    sales = [
        r
        for r in s["rows"]
        if r["shop_id"] in shops
        and q.date_from <= r["business_date"] <= q.date_to
        and r["shop_revenue"] is not None
    ]
    by_key = {(r["shop_id"], r["business_date"]): r for r in refunds}
    matched = [
        (r, by_key[(r["shop_id"], r["business_date"])])
        for r in sales
        if (r["shop_id"], r["business_date"]) in by_key
        and by_key[(r["shop_id"], r["business_date"])]["reported_refund_amount"] is not None
    ]
    denominator = total([r for r, _ in matched], "shop_revenue")
    numerator = total([r for _, r in matched], "reported_refund_amount")
    data["refund_rate"] = ratio(numerator, denominator)
    data["refund_match"] = dict(
        matched_refunds=number(numerator, money=True),
        matched_sales=number(denominator, money=True),
        matched_days=len(matched),
        sales_coverage=ratio(denominator, total(sales, "shop_revenue")),
        excluded_days=len(sales) - len(matched),
        excluded_sales=number(total(sales, "shop_revenue") - denominator, money=True)
        if denominator is not None and sales
        else None,
        missing_dates=[
            str(r["business_date"])
            for r in sales
            if (r["shop_id"], r["business_date"]) not in by_key
            or by_key[(r["shop_id"], r["business_date"])]["reported_refund_amount"] is None
        ],
        state="same_day_reference" if "shop_refunds" in s["versions"] else "not_connected",
    )
    data["field_states"]["refund_rate"] = (
        "same_day_reference"
        if refunds
        else "no_records"
        if "shop_refunds" in s["versions"]
        else "not_connected"
    )
    channels = []
    all_rows = []
    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        applicable = [x for x in shops if code != "shop_channel_star_shop" or x == "spigf"]
        if not applicable:
            continue
        rows = scoped_rows(s, code, q, applicable)
        all_rows.extend(rows)
        observed = {(r["shop_id"], r["business_date"]) for r in rows}
        expected = (q.date_to - q.date_from).days + 1
        channels.append(
            dict(
                code=code,
                spend=number(total(rows, "spend"), money=True),
                revenue=number(total(rows, "attributed_revenue"), money=True),
                ctr=ratio(
                    total(
                        [
                            r
                            for r in rows
                            if r["clicks"] is not None and r["impressions"] is not None
                        ],
                        "clicks",
                    ),
                    total(
                        [
                            r
                            for r in rows
                            if r["clicks"] is not None and r["impressions"] is not None
                        ],
                        "impressions",
                    ),
                ),
                roi=ratio(
                    total(
                        [
                            r
                            for r in rows
                            if r["attributed_revenue"] is not None and r["spend"] is not None
                        ],
                        "attributed_revenue",
                    ),
                    total(
                        [
                            r
                            for r in rows
                            if r["attributed_revenue"] is not None and r["spend"] is not None
                        ],
                        "spend",
                    ),
                ),
                missing_shop_days=expected * len(applicable) - len(observed),
                null_counts={
                    f: sum(r[f] is None for r in rows)
                    for f in ("spend", "attributed_revenue", "clicks", "impressions")
                },
                source=source_info(s, code),
            )
        )
    paired = [r for r in all_rows if r["clicks"] is not None and r["impressions"] is not None]
    data.update(
        promotion_channels=channels,
        promotion_spend=number(total(all_rows, "spend"), money=True),
        promotion_ctr=ratio(total(paired, "clicks"), total(paired, "impressions")),
        promotion_roi=channels[0]["roi"] if len(channels) == 1 else None,
    )
    for f in ("promotion_spend", "promotion_ctr"):
        data["field_states"][f] = "observed_partial" if all_rows else "not_connected"
    data["field_states"]["promotion_roi"] = (
        "cross_channel_unconfirmed" if len(channels) > 1 else "source_reference"
    )


class SalesDetails:
    def __init__(self, engine):
        self.engine = engine

    def targets(self, month, version):
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise SalesError("INVALID_MONTH", 422)
        try:
            start = date.fromisoformat(month + "-01")
        except ValueError as e:
            raise SalesError("INVALID_MONTH", 422) from e
        if not APPROVED_START <= start <= calendar_through():
            raise SalesError("INVALID_MONTH", 422)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        with read_snapshot(self.engine, version) as s:
            month_rows = [r for r in s["rows"] if start <= r["business_date"] <= end]
            cutoff = min(end, calendar_through())
            month_missing = ((cutoff - start).days + 1) * len(SHOPS) - len(
                {(r["shop_id"], r["business_date"]) for r in month_rows}
            )
            annual_rows = [r for r in s["rows"] if r["business_date"] <= cutoff]
            annual_missing = ((cutoff - APPROVED_START).days + 1) * len(SHOPS) - len(
                {(r["shop_id"], r["business_date"]) for r in annual_rows}
            )
            # 目标入口未能取得，不将实际完成额/历史快照当目标。
            return dict(
                data=dict(
                    month_missing_days=month_missing,
                    annual_missing_days=annual_missing,
                    month_actual=number(total(month_rows, "shop_revenue"), money=True),
                    annual_actual=number(
                        total([r for r in s["rows"] if r["business_date"] <= end], "shop_revenue"),
                        money=True,
                    ),
                    month_target=None,
                    annual_target=None,
                    month_ratio=None,
                    annual_ratio=None,
                    gap=None,
                    state="source_unavailable",
                ),
                meta=meta(s),
            )

    def ratings(self, q, shop_ids):
        if q.date_from > q.date_to:
            raise SalesError("INVALID_WINDOW", 422)
        identities = [v[0] for v in SHOPS.values()]
        chosen = shop_ids or identities
        if any(x not in identities for x in chosen):
            raise SalesError("INVALID_SHOP", 422)
        with read_snapshot(self.engine, q.data_version) as s:
            info = source_info(s, "shop_ratings")
            latest = info["observed_through"]
            rows = []
            if latest:
                rows = (
                    s["connection"]
                    .execute(
                        sa.text(
                            "SELECT * FROM public.shop_rating_daily WHERE dataset_version_id=:v AND"
                            " (rating_date BETWEEN :lo AND :hi OR rating_date=:latest)"
                        ),
                        dict(
                            v=s["versions"]["shop_ratings"],
                            lo=q.date_from,
                            hi=q.date_to,
                            latest=latest,
                        ),
                    )
                    .mappings()
                    .all()
                )

            def point(r):
                value = r["source_score"]
                valid = value is not None and 0 <= value <= 1
                return dict(
                    shop_id=r["shop_id"],
                    date=str(r["rating_date"]),
                    raw_score=number(value),
                    score=number(value * 100) if valid else None,
                    state="observed"
                    if valid
                    else "legacy_basis"
                    if value is not None
                    else "field_null",
                    source_created_time_raw=r["source_created_time_raw"],
                )

            cards = []
            for x in identities:
                found = next(
                    (r for r in rows if r["shop_id"] == x and str(r["rating_date"]) == latest), None
                )
                cards.append(
                    point(found)
                    if found
                    else dict(
                        shop_id=x,
                        date=latest,
                        raw_score=None,
                        score=None,
                        state="no_records",
                        source_created_time_raw=None,
                    )
                )
            return dict(
                data=dict(
                    latest_date=latest,
                    latest=cards,
                    points=[
                        point(r)
                        for r in rows
                        if q.date_from <= r["rating_date"] <= q.date_to and r["shop_id"] in chosen
                    ],
                    source=info,
                ),
                meta=meta(s),
            )
