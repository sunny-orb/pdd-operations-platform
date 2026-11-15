"""店铺其余指标的相容分子分母、独立日期和逐来源覆盖。"""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa

from app.modules.fact_content import read_name
from app.modules.promotion.metrics import store_metrics
from app.modules.record_coverage import missing_shop_days

from .contracts import SalesError
from .datasets import READ_SPECS as SPECS
from .promotion_history import effective_rows, history_rows, supplement_audit
from .query import days, meta, number, ratio, total
from .repository import read_snapshot
from .source_policy import APPROVED_START, SHOPS, calendar_through


def source_info(snapshot, code):
    cached = snapshot.setdefault("source_info", {})
    if code in cached:
        return cached[code]
    spec = SPECS.get(code)
    # 来源日期与经营明细共用逻辑事实；共享版本可能没有旧物理表记录。
    table = read_name(spec.fact_table) if spec else "sales_targets"
    date_column = spec.date_column if spec else "period_start"
    v = snapshot["versions"].get(code)
    c = snapshot["connection"]
    attempt = (
        c.execute(
            sa.text(
                "SELECT status,started_at,queued_at FROM public.task_runs WHERE job_code=:job "
                "ORDER BY queued_at DESC,id DESC LIMIT 1"
            ),
            dict(job="sync_" + code),
        )
        .mappings()
        .first()
    )
    attempt_fields = dict(
        last_attempt_status=attempt["status"] if attempt else None,
        last_attempt_at=(attempt["started_at"] or attempt["queued_at"]).isoformat()
        if attempt
        else None,
    )
    if not v:
        cached[code] = dict(
            state="not_connected",
            observed_from=None,
            observed_through=None,
            source_updated_text=None,
            last_success_at=None,
            **attempt_fields,
        )
        return cached[code]
    lo, hi = c.execute(
        sa.text(
            f"SELECT min({date_column}),max({date_column}) FROM "
            f"public.{table} WHERE dataset_version_id=:v"
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
    cached[code] = dict(
        state="observed",
        observed_from=str(lo) if lo else None,
        observed_through=str(hi) if hi else None,
        source_updated_text=success["source_updated_text"] if success else None,
        last_success_at=success["finished_at"].isoformat() if success else None,
        **attempt_fields,
    )
    return cached[code]


def scoped_rows(s, code, q, shops):
    if code == "shop_channel_product_promotion":
        warehouse = []
        if code in s["versions"]:
            warehouse = list(
                s["connection"]
                .execute(
                    sa.text(
                        "SELECT * FROM public.shop_channel_promotion_daily "
                        "WHERE dataset_version_id=:v AND business_date BETWEEN :lo AND :hi "
                        "AND shop_id = ANY(:shops)"
                    ),
                    dict(v=s["versions"][code], lo=q.date_from, hi=q.date_to, shops=shops),
                )
                .mappings()
            )
        rows = effective_rows(warehouse, history_rows(s["connection"], s["versions"]))
        return [
            r
            for r in rows
            if q.date_from <= r["business_date"] <= q.date_to and r["shop_id"] in shops
        ]
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


def promotion_record_issues(rows, start, end, shops):
    """展示所选店铺日的缺行/空字段；读取已选生效行，不参与任何指标计算。"""
    by_key = {(r["shop_id"], r["business_date"]): r for r in rows}
    issues = []
    for shop in shops:
        for day in days(start, end):
            row = by_key.get((shop, day))
            fields = (
                [
                    f
                    for f in ("spend", "attributed_revenue", "clicks", "impressions")
                    if row[f] is None
                ]
                if row is not None
                else []
            )
            if row is None or fields:
                issues.append(
                    dict(
                        shop_id=shop,
                        date=str(day),
                        kind="missing_record" if row is None else "null_fields",
                        fields=fields,
                    )
                )
    return issues


def promotion_value(metrics, key, *, money=False):
    """仅将推广公共结果适配既有销售API精度，不重新计算指标。"""
    value = metrics[key]["value"]
    if value is None:
        return None
    if money:
        return number(value, money=True)
    return format(value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP), "f")


def promotion_shops(code, shops):
    """根据已接入渠道的业务范围返回适用店铺。"""
    applicable = {SHOPS[shop][0] for shop in SPECS[code].shop_ids}
    return [shop for shop in shops if shop in applicable]


def matched_refunds(sales, by_key):
    """销售与退款仅在同店同日且两项金额有效时配对。"""
    pairs = []
    for sale in sales:
        refund = by_key.get((sale["shop_id"], sale["business_date"]))
        if (
            sale["shop_revenue"] is not None
            and refund
            and refund["reported_refund_amount"] is not None
        ):
            pairs.append((sale, refund))
    return pairs


def refund_totals(pairs):
    """返回配对退款金额与对应销售金额，供卡片和日趋势共用。"""
    return (
        total([refund for _, refund in pairs], "reported_refund_amount"),
        total([sale for sale, _ in pairs], "shop_revenue"),
    )


def enrich_metrics(s, q, data, shops):
    """退款匹配同店日；推广复用店铺推广公共计算，覆盖明细只作解释。"""
    refunds = scoped_rows(s, "shop_refunds", q, shops)
    sales = [
        r
        for r in s["rows"]
        if r["shop_id"] in shops
        and q.date_from <= r["business_date"] <= q.date_to
        and r["shop_revenue"] is not None
    ]
    by_key = {(r["shop_id"], r["business_date"]): r for r in refunds}
    # 展示覆盖独立核对整个所选期间；整行缺失、金额NULL与真实0分开，不改退款率配对。
    data["refund_coverage"] = dict(
        state="observed" if "shop_refunds" in s["versions"] else "not_connected",
        missing_record_dates=[
            str(day) for shop, day in missing_shop_days(by_key, q.date_from, q.date_to, shops)
        ]
        if "shop_refunds" in s["versions"]
        else [],
        null_amount_dates=sorted(
            str(r["business_date"]) for r in refunds if r["reported_refund_amount"] is None
        ),
    )
    matched = matched_refunds(sales, by_key)
    numerator, denominator = refund_totals(matched)
    data["refund_rate"] = ratio(numerator, denominator)
    matched_keys = {(r["shop_id"], r["business_date"]) for r, _ in matched}
    excluded_sales = [r for r in sales if (r["shop_id"], r["business_date"]) not in matched_keys]
    data["refund_match"] = dict(
        matched_refunds=number(numerator, money=True),
        matched_sales=number(denominator, money=True),
        matched_days=len(matched),
        sales_coverage=ratio(denominator, total(sales, "shop_revenue")),
        excluded_days=len(sales) - len(matched),
        excluded_sales=number(
            Decimal(0) if sales and not excluded_sales else total(excluded_sales, "shop_revenue"),
            money=True,
        ),
        source=source_info(s, "shop_refunds"),
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
    history = history_rows(s["connection"], s["versions"])
    warehouse = []
    if "shop_channel_product_promotion" in s["versions"]:
        warehouse = list(
            s["connection"]
            .execute(
                sa.text(
                    "SELECT * FROM public.shop_channel_promotion_daily WHERE dataset_version_id=:v"
                ),
                dict(v=s["versions"]["shop_channel_product_promotion"]),
            )
            .mappings()
        )
    data["promotion_supplements"] = supplement_audit(
        warehouse, history, q.date_from, q.date_to, shops
    )
    channels = []
    all_rows = []
    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        applicable = promotion_shops(code, shops)
        if not applicable:
            continue
        rows = scoped_rows(s, code, q, applicable)
        all_rows.extend(rows)
        metrics = store_metrics(rows, include_star=code == "shop_channel_star_shop")
        observed = {(r["shop_id"], r["business_date"]) for r in rows}
        channels.append(
            dict(
                code=code,
                latest_observed_date=str(max((r["business_date"] for r in rows), default=""))
                or None,
                business_date_basis="payment_date",
                attribution_state="source_reported_payment_date",
                spend=promotion_value(metrics, "spend", money=True),
                revenue=promotion_value(metrics, "revenue", money=True),
                ctr=promotion_value(metrics, "ctr"),
                roi=promotion_value(metrics, "roi"),
                missing_shop_days=len(
                    missing_shop_days(observed, q.date_from, q.date_to, applicable)
                ),
                record_issues=promotion_record_issues(rows, q.date_from, q.date_to, applicable),
                null_counts={
                    f: sum(r[f] is None for r in rows)
                    for f in ("spend", "attributed_revenue", "clicks", "impressions")
                },
                source=source_info(s, code),
            )
        )
    metrics = store_metrics(all_rows, include_star=len(channels) > 1)
    data.update(
        promotion_channels=channels,
        promotion_spend=promotion_value(metrics, "spend", money=True),
        promotion_ctr=promotion_value(metrics, "ctr"),
        promotion_roi=promotion_value(metrics, "roi"),
    )
    missing_days = any(channel["missing_shop_days"] for channel in channels)
    connected = any(channel["source"]["state"] != "not_connected" for channel in channels)
    for field, key in (
        ("promotion_spend", "spend"),
        ("promotion_ctr", "ctr"),
        ("promotion_roi", "roi"),
    ):
        status = metrics[key]["status"]
        data["field_states"][field] = (
            "not_connected"
            if status == "no_records" and not connected
            else "no_records"
            if status == "no_records"
            else "missing_channel_input"
            if status in ("missing", "partial")
            else "zero_denominator"
            if status == "zero_denominator"
            else "observed_partial"
            if missing_days
            else "source_reference"
            if key == "roi"
            else "observed"
        )


class SalesDetails:
    def __init__(self, engine):
        self.engine = engine

    def targets(self, month, version, target_basis="original_gmv"):
        from .targets import query_targets

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
            return dict(
                data=query_targets(s, start, min(end, calendar_through()), target_basis),
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
                    else "invalid_score"
                    if value is not None and value < 0
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
