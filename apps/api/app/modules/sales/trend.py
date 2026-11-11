"""按同一发布版本返回店铺经营八卡的逐日值及适用记录覆盖。"""

from collections import defaultdict

from app.modules.promotion.metrics import store_metrics

from .datasets import HISTORY_SPEC
from .detail_queries import (
    matched_refunds,
    promotion_shops,
    promotion_value,
    refund_totals,
    scoped_rows,
)
from .query import (
    PERFORMANCE_INPUTS,
    aggregate,
    days,
    meta,
    performance_inputs,
    ratio,
    selected_shops,
)
from .repository import read_snapshot
from .source_policy import PROMOTION_CODES

PROMOTION_METRICS = {
    "promotion_spend": "spend",
    "promotion_ctr": "ctr",
    "promotion_roi": "roi",
}


def point(value, observed, expected, missing_state):
    """有值时标识实际覆盖；无值时保留该指标的原因。"""
    state = missing_state if value is None else "partial" if observed < expected else "observed"
    return dict(value=value, state=state, observed=observed, expected=expected)


def read_performance_trend(engine, query):
    """单个只读快照内按范围取数，先按日分组再用经营卡相同的计算。"""
    with read_snapshot(engine, query.data_version) as snapshot:
        shops = selected_shops(query)
        sales_by_day = defaultdict(list)
        for row in snapshot["rows"]:
            if row["shop_id"] in shops and query.date_from <= row["business_date"] <= query.date_to:
                sales_by_day[row["business_date"]].append(row)
        refunds = scoped_rows(snapshot, "shop_refunds", query, shops)
        refund_by_key = {(row["shop_id"], row["business_date"]): row for row in refunds}
        refund_connected = "shop_refunds" in snapshot["versions"]
        promotion_by_day = defaultdict(list)
        promotion_expected = 0
        promotion_connected = False
        for code in PROMOTION_CODES:
            applicable = promotion_shops(code, shops)
            if not applicable:
                continue
            promotion_expected += len(applicable)
            promotion_connected |= code in snapshot["versions"] or (
                code == PROMOTION_CODES[0] and HISTORY_SPEC.code in snapshot["versions"]
            )
            for row in scoped_rows(snapshot, code, query, applicable):
                promotion_by_day[row["business_date"]].append(row)

        points = []
        for day in days(query.date_from, query.date_to):
            rows = sales_by_day[day]
            daily = aggregate(rows, day, day, shops)
            metrics = {}
            for key in PERFORMANCE_INPUTS:
                observed = len(performance_inputs(rows, key))
                missing_state = (
                    "zero_denominator" if observed else "field_null" if rows else "no_records"
                )
                metrics[key] = point(daily[key], observed, len(shops), missing_state)

            pairs = matched_refunds(rows, refund_by_key)
            refund_rate = ratio(*refund_totals(pairs))
            refund_state = (
                "not_connected"
                if not refund_connected
                else "no_records"
                if not rows
                else "zero_denominator"
                if pairs
                else "unmatched"
            )
            metrics["refund_rate"] = point(refund_rate, len(pairs), len(shops), refund_state)

            promotion = store_metrics(promotion_by_day[day])
            for key, name in PROMOTION_METRICS.items():
                metric = promotion[name]
                missing_state = {
                    "no_records": "no_records" if promotion_connected else "not_connected",
                    "zero_denominator": "zero_denominator",
                }.get(metric["status"], "field_null")
                metrics[key] = point(
                    promotion_value(promotion, name, money=name == "spend"),
                    int(metric["valid_rows"]),
                    promotion_expected,
                    missing_state,
                )
            points.append(dict(date=day.isoformat(), metrics=metrics))
        return dict(data=dict(points=points), meta=meta(snapshot))
