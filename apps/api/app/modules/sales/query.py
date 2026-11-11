"""店铺销售领域计算；比率按相容分子分母合计，缺记录不补零。"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.modules.record_coverage import missing_shop_days

from . import source_policy
from .contracts import SalesError
from .repository import read_snapshot
from .source_policy import METRICS, SHOPS

PENDING = dict(
    refund_rate="metric_unconfirmed",
    promotion_spend="not_connected",
    promotion_ctr="not_connected",
    promotion_roi="metric_unconfirmed",
)


@dataclass(frozen=True)
class SalesQuery:
    date_from: date
    date_to: date
    data_version: str
    shop_id: str | None = None


def days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def number(value, *, money=False):
    if value is None:
        return None
    return (
        format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
        if money
        else format(value, "f")
    )


def total(rows, field):
    values = [row[field] for row in rows if row[field] is not None]
    return sum(values, Decimal(0)) if values else None


def ratio(a, b):
    return (
        format((a / b).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP), "f")
        if a is not None and b is not None and b > 0
        else None
    )


def selected_shops(query):
    """按经营日历校验范围，返回同一批经营店铺身份。"""
    if (
        not source_policy.APPROVED_START
        <= query.date_from
        <= query.date_to
        <= source_policy.calendar_through()
    ):
        raise SalesError("INVALID_WINDOW", 422)
    identities = [shop[0] for shop in SHOPS.values()]
    if query.shop_id and query.shop_id not in identities:
        raise SalesError("INVALID_SHOP", 422)
    return [query.shop_id] if query.shop_id else identities


PERFORMANCE_INPUTS = {
    "shop_revenue": ("shop_revenue",),
    "shop_visitors": ("shop_visitors",),
    "buyer_conversion": ("buyers", "shop_visitors"),
    "aov": ("shop_revenue", "buyers"),
}


def performance_inputs(rows, metric):
    """筛出该经营指标的同店日有效输入，保留真实零。"""
    return [row for row in rows if all(row[key] is not None for key in PERFORMANCE_INPUTS[metric])]


def aggregate(rows, start, end, shop_ids):
    observed = {(r["shop_id"], r["business_date"]) for r in rows}
    coverage = dict(
        observed_days=len({(r["shop_id"], r["business_date"]) for r in rows}),
        missing_dates=[
            f"{shop}:{day}" for shop, day in missing_shop_days(observed, start, end, shop_ids)
        ],
        field_null_counts={
            field: sum(r[field] is None for r in rows) for field in METRICS.values()
        },
        latest_observed_date=str(max((r["business_date"] for r in rows), default="")) or None,
        reason_code="UPSTREAM_COMPLETENESS_UNKNOWN",
        window_start=str(start),
        window_end=str(end),
    )
    conversion = performance_inputs(rows, "buyer_conversion")
    aov = performance_inputs(rows, "aov")
    return dict(
        shop_revenue=number(total(rows, "shop_revenue"), money=True),
        shop_visitors=number(total(rows, "shop_visitors")),
        buyers=number(total(rows, "buyers")),
        orders=number(total(rows, "orders")),
        buyer_conversion=ratio(total(conversion, "buyers"), total(conversion, "shop_visitors")),
        aov=ratio(total(aov, "shop_revenue"), total(aov, "buyers")),
        coverage=coverage,
        field_states={
            **PENDING,
            **{
                f: (
                    "no_records"
                    if not rows
                    else "field_null"
                    if total(rows, f) is None
                    else "observed"
                )
                for f in ("shop_revenue", "shop_visitors", "buyers", "orders")
            },
        },
        **dict.fromkeys(PENDING),
    )


def meta(snapshot):
    rows, success, attempt = snapshot["rows"], snapshot["success"], snapshot["attempt"]
    dates = [r["business_date"] for r in rows if r["business_date"]]
    return dict(
        data_version=snapshot["token"],
        input_versions=snapshot["versions"],
        observed_from=str(min(dates)) if dates else None,
        observed_through=str(max(dates)) if dates else None,
        completeness_state="unknown",
        complete_through=None,
        source_updated_text=success["source_updated_text"] if success else None,
        source_updated_at=None,
        last_attempt_status=attempt["status"] if attempt else None,
        last_attempt_at=(attempt["started_at"] or attempt["queued_at"]).isoformat()
        if attempt
        else None,
        last_success_at=success["finished_at"].isoformat() if success else None,
    )


class SalesService:
    def __init__(self, engine):
        self.engine = engine

    def filters(self):
        with read_snapshot(self.engine) as snapshot:
            start, end = snapshot["window"]
            if start is None or end is None:
                raise SalesError("SALES_COVERAGE_INVALID", 503)
            start, end = source_policy.APPROVED_START, source_policy.calendar_through()
            months = []
            current = start.replace(day=1)
            while current <= end:
                next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
                months.append(
                    dict(
                        month=current.strftime("%Y-%m"),
                        date_from=str(max(start, current)),
                        date_to=str(min(end, next_month - timedelta(days=1))),
                    )
                )
                current = next_month
            from .datasets import SPECS
            from .detail_queries import source_info

            metadata = meta(snapshot)
            observed_through = metadata["observed_through"]
            observed_date = date.fromisoformat(observed_through) if observed_through else None
            latest_day = None
            if observed_date is not None and start <= observed_date <= end:
                identities = {shop[0] for shop in SHOPS.values()}
                rows = [
                    row
                    for row in snapshot["rows"]
                    if row["business_date"] == observed_date and row["shop_id"] in identities
                ]
                latest_day = dict(
                    date=observed_through,
                    shop_revenue=number(total(rows, "shop_revenue"), money=True),
                    observed_shop_count=len({row["shop_id"] for row in rows}),
                )
            return dict(
                data=dict(
                    sources={
                        code: source_info(snapshot, code) for code in (*SPECS, "sales_targets")
                    },
                    shops=[
                        dict(
                            source_shop_id=source_id, shop_id=id, shop_name=name, display_name=label
                        )
                        for source_id, (id, name, label) in SHOPS.items()
                    ],
                    months=months,
                    default_month=months[-1]["month"],
                    recommended_date_from=months[-1]["date_from"],
                    recommended_date_to=months[-1]["date_to"],
                    calendar_through=str(end),
                    observed_from=metadata["observed_from"],
                    observed_through=observed_through,
                    latest_day=latest_day,
                    capabilities=dict(
                        shop_daily=True,
                        targets="sales_targets" in snapshot["versions"],
                        refunds="shop_refunds" in snapshot["versions"],
                        promotion=any(
                            code in snapshot["versions"]
                            for code in ("shop_channel_product_promotion", "shop_channel_star_shop")
                        ),
                        ratings="shop_ratings" in snapshot["versions"],
                        regions="shop_regions" in snapshot["versions"],
                        product_regions="product_regions" in snapshot["versions"],
                    ),
                ),
                meta=metadata,
            )

    def read(self, query, *, scope):
        with read_snapshot(self.engine, query.data_version) as snapshot:
            selected = selected_shops(query)
            rows = [
                r
                for r in snapshot["rows"]
                if query.date_from <= r["business_date"] <= query.date_to
            ]
            if scope == "performance":
                data = aggregate(
                    [r for r in rows if r["shop_id"] in selected],
                    query.date_from,
                    query.date_to,
                    selected,
                )
            else:
                items = [
                    dict(
                        shop_id=id,
                        shop_name=name,
                        display_name=label,
                        **aggregate(
                            [r for r in rows if r["shop_id"] == id],
                            query.date_from,
                            query.date_to,
                            [id],
                        ),
                    )
                    for id, name, label in SHOPS.values()
                ]
                items.sort(
                    key=lambda r: (r["shop_revenue"] is None, -Decimal(r["shop_revenue"] or 0))
                )
                denominator = total(rows, "shop_revenue")
                for rank, item in enumerate(items, 1):
                    item["rank"] = rank if item["shop_revenue"] is not None else None
                    item["sales_share"] = ratio(
                        Decimal(item["shop_revenue"]) if item["shop_revenue"] is not None else None,
                        denominator,
                    )
                data = dict(
                    items=items,
                    missing_shop_days=sum(len(item["coverage"]["missing_dates"]) for item in items),
                )
            from .detail_queries import enrich_metrics

            if scope == "performance":
                enrich_metrics(snapshot, query, data, selected)
            else:
                for item in data["items"]:
                    enrich_metrics(snapshot, query, item, [item["shop_id"]])
            return dict(data=data, meta=meta(snapshot))

    def performance(self, query):
        return self.read(query, scope="performance")

    def shops(self, query):
        return self.read(query, scope="shops")
