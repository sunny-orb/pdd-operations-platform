"""地区原行只在PG分组；产品候选搜索与经营汇总分开。"""

import json
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from .contracts import SalesError
from .detail_queries import source_info
from .query import meta, number, ratio
from .repository import read_snapshot
from .source_policy import APPROVED_START, SHOPS, calendar_through

PRODUCT_CTE = """WITH labeled AS (
 SELECT *, coalesce(nullif(btrim(source_product_category),''),'未分类') AS category,
 CASE WHEN nullif(btrim(source_style_code),'') IS NOT NULL
 AND position('未设置' in source_style_code)=0 THEN 'style'
 WHEN nullif(btrim(source_product_code),'') IS NOT NULL
 AND position('未设置' in source_product_code)=0 THEN 'sku'
 ELSE NULL END AS kind,
 CASE WHEN nullif(btrim(source_style_code),'') IS NOT NULL
 AND position('未设置' in source_style_code)=0
 THEN btrim(source_style_code)
 WHEN nullif(btrim(source_product_code),'') IS NOT NULL
 AND position('未设置' in source_product_code)=0
 THEN btrim(source_product_code)
 ELSE NULL END AS code
 FROM public.product_region_records WHERE dataset_version_id=:v
 AND regexp_replace(coalesce(source_product_category,''),'[[:space:]_－—-]','','g')
 NOT IN ('包材','儿童赠品','分类未设置')
) """


def validate_scope(q):
    if not APPROVED_START <= q.date_from <= q.date_to <= calendar_through():
        raise SalesError("INVALID_WINDOW", 422)
    if q.shop_id and q.shop_id not in {s[0] for s in SHOPS.values()}:
        raise SalesError("INVALID_SHOP", 422)


def product_selection(category, key):
    values = dict(category=category or None, kind=None, code=None, key_category=None)
    if key:
        try:
            parts = json.loads(key)
            if (
                not isinstance(parts, list)
                or len(parts) != 3
                or any(not isinstance(x, str) or not x for x in parts)
                or parts[1] not in ("style", "sku")
            ):
                raise ValueError
            values.update(key_category=parts[0], kind=parts[1], code=parts[2])
            if category and category != parts[0]:
                raise ValueError
        except (ValueError, TypeError) as e:
            raise SalesError("INVALID_PRODUCT_KEY", 422) from e
    return values


class RegionQueries:
    def __init__(self, engine):
        self.engine = engine

    def filters(self, version, category, search, offset, limit):
        if len(search) > 200 or len(category) > 200:
            raise SalesError("INVALID_SEARCH", 422)
        with read_snapshot(self.engine, version) as s:
            info = source_info(s, "product_regions")
            if "product_regions" not in s["versions"]:
                return dict(
                    data=dict(
                        categories=[], products=[], total=0, offset=offset, limit=limit, source=info
                    ),
                    meta=meta(s),
                )
            c = s["connection"]
            params = dict(
                v=s["versions"]["product_regions"],
                category=category or None,
                search=search,
                offset=offset,
                limit=limit,
            )
            categories = list(
                c.execute(
                    sa.text(
                        PRODUCT_CTE + "SELECT DISTINCT category FROM labeled ORDER BY category"
                    ),
                    params,
                ).scalars()
            )
            grouped = """SELECT category,kind,code,
 string_agg(DISTINCT source_product_name,' / ' ORDER BY source_product_name) AS names
            FROM labeled WHERE kind IS NOT NULL
 AND (CAST(:category AS text) IS NULL OR category=:category)
 GROUP BY category,kind,code"""
            matched = (
                " SELECT * FROM candidates WHERE :search='' OR "
                "position(lower(:search) in lower(coalesce(names,'') || ' ' || code))>0 "
            )
            cte = PRODUCT_CTE + ", candidates AS (" + grouped + "), matches AS (" + matched + ") "
            total = c.execute(sa.text(cte + "SELECT count(*) FROM matches"), params).scalar_one()
            rows = c.execute(
                sa.text(
                    cte + "SELECT * FROM matches ORDER BY category,kind,code LIMIT :limit OFFSET "
                    ":offset"
                ),
                params,
            ).mappings()
            products = [
                dict(
                    key=json.dumps(
                        [r["category"], r["kind"], r["code"]],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    category=r["category"],
                    code=r["code"],
                    name=r["names"] or "名称缺失",
                )
                for r in rows
            ]
            return dict(
                data=dict(
                    categories=categories,
                    products=products,
                    total=total,
                    offset=offset,
                    limit=limit,
                    source=info,
                ),
                meta=meta(s),
            )

    def regions(self, q, *, product=False, category="", product_key=""):
        validate_scope(q)
        selected = product_selection(category, product_key)
        code = "product_regions" if product else "shop_regions"
        fields = (
            (
                "actual_shipped_amount",
                "actual_shipped_qty",
                "actual_returned_amount",
                "actual_returned_qty",
            )
            if product
            else ("transaction_amount", "transaction_orders", "transaction_buyers")
        )
        with read_snapshot(self.engine, q.data_version) as s:
            info = source_info(s, code)
            v = s["versions"].get(code)
            items = []
            null_counts = {f: 0 for f in fields}
            missing = []
            if v:
                params = dict(v=v, lo=q.date_from, hi=q.date_to, shop=q.shop_id, **selected)
                if product:
                    cte = PRODUCT_CTE
                    table = "labeled"
                    day = "period_start"
                    condition = (
                        " AND (CAST(:category AS text) IS NULL OR category=:category) "
                        "AND (CAST(:code AS text) IS NULL OR "
                        "(category=:key_category AND kind=:kind AND code=:code))"
                    )
                else:
                    cte = ""
                    table = "public.shop_region_daily"
                    day = "business_date"
                    condition = ""
                where = (
                    f" WHERE dataset_version_id=:v AND {day} BETWEEN :lo AND :hi AND "
                    f"(CAST(:shop AS text) IS NULL OR shop_id=:shop)" + condition
                )
                aggregates = ",".join(
                    f"SUM({f}) AS {f}, count(*) FILTER (WHERE {f} IS NULL) AS {f}_null"
                    for f in fields
                )
                if not product:
                    aggregates += (
                        ",SUM(transaction_amount) FILTER (WHERE transaction_buyers IS NOT NULL) "
                        "AS aov_amount,SUM(transaction_buyers) FILTER "
                        "(WHERE transaction_amount IS NOT NULL) AS aov_buyers"
                    )
                rows = (
                    s["connection"]
                    .execute(
                        sa.text(
                            cte
                            + f"SELECT source_region_name,{aggregates},count(*) AS row_count FROM "
                            f"{table}"
                            + where
                            + " GROUP BY source_region_name ORDER BY "
                            + fields[0]
                            + " DESC NULLS LAST, source_region_name NULLS LAST"
                        ),
                        params,
                    )
                    .mappings()
                    .all()
                )
                denom = (
                    sum(
                        (r[fields[0]] for r in rows if r[fields[0]] is not None),
                        start=Decimal(0),
                    )
                    if any(r[fields[0]] is not None for r in rows)
                    else None
                )
                for r in rows:
                    item = dict(
                        region=r["source_region_name"] or "地区缺失",
                        **{f: number(r[f], money="amount" in f) for f in fields},
                        share=ratio(r[fields[0]], denom),
                        null_counts={f: r[f + "_null"] for f in fields},
                    )
                    item["refund_rate" if product else "aov"] = (
                        ratio(r["actual_returned_amount"], r["actual_shipped_amount"])
                        if product
                        else ratio(r["aov_amount"], r["aov_buyers"])
                    )
                    items.append(item)
                    for f in fields:
                        null_counts[f] += r[f + "_null"]
                observed = (
                    s["connection"]
                    .execute(
                        sa.text(cte + f"SELECT DISTINCT shop_id,{day} AS day FROM {table}" + where),
                        params,
                    )
                    .mappings()
                    .all()
                )
                observed = {(r["shop_id"], r["day"]) for r in observed}
                identities = [q.shop_id] if q.shop_id else [r[0] for r in SHOPS.values()]

                missing = [
                    f"{shop}:{q.date_from + timedelta(days=i)}"
                    for shop in identities
                    for i in range((q.date_to - q.date_from).days + 1)
                    if (shop, q.date_from + timedelta(days=i)) not in observed
                ]
                totals = {
                    f: number(
                        sum(
                            (r[f] for r in rows if r[f] is not None),
                            start=Decimal(0),
                        ),
                        money="amount" in f,
                    )
                    if any(r[f] is not None for r in rows)
                    else None
                    for f in fields
                }
            else:
                totals = dict.fromkeys(fields)
            if product:
                totals["refund_rate"] = ratio(
                    Decimal(totals["actual_returned_amount"])
                    if totals["actual_returned_amount"] is not None
                    else None,
                    Decimal(totals["actual_shipped_amount"])
                    if totals["actual_shipped_amount"] is not None
                    else None,
                )
            return dict(
                data=dict(
                    items=items,
                    totals=totals,
                    region_count=len(items),
                    null_counts=null_counts,
                    missing_shop_days=missing,
                    source=info,
                ),
                meta=meta(s),
            )
