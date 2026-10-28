"""只读独立复核M3必要归档→PG逐字段及HTTP聚合；只输出脱敏摘要。"""

import json
import re
import sys
import time
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path.cwd() / "apps/api"))
import sqlalchemy as sa
from app.core.config import Settings
from app.db.connection import build_engine
from app.modules.sales.archive_replay import restore_candidate
from app.modules.sales.datasets import SPECS
from app.modules.sales.source_policy import SHOPS


def summed(rows, key):
    known = [r[key] for r in rows if r[key] is not None]
    return sum(known, Decimal(0)) if known else None


def divided(a, b):
    with localcontext() as ctx:
        ctx.prec = 50
        return (
            (a / b).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
            if a is not None and b is not None and b > 0
            else None
        )


def decimal_equal(actual, expected, money=False):
    value = Decimal(actual) if actual is not None else None
    if money and expected is not None:
        expected = expected.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert value == expected, "AGGREGATE_MISMATCH"


root = Path.cwd()
engine = build_engine(Settings(_env_file=root / "apps/api/.env"))
assert engine.url.host in {"localhost", "127.0.0.1"} and engine.url.port == 55432
assert engine.url.database == "pdd_operations"
inputs = {}
counts = {}
opener = build_opener(ProxyHandler({}))
latencies = []


def get(path, params=None):
    started = time.monotonic()
    with opener.open(
        "http://127.0.0.1:8000/api/v1/sales/" + path + ("?" + urlencode(params) if params else ""),
        timeout=10,
    ) as response:
        data = json.load(response)
    latencies.append((time.monotonic() - started) * 1000)
    return data


with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
    c.execute(sa.text("SET TRANSACTION READ ONLY"))
    heads = (
        c.execute(
            sa.text(
                "SELECT d.dataset_code,v.* FROM current_datasets d JOIN "
                "dataset_versions v ON v.id=d.version_id"
            )
        )
        .mappings()
        .all()
    )
    before = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
    for head in heads:
        code = head["dataset_code"]
        if code != "shop_daily" and code not in SPECS:
            continue
        candidate = restore_candidate(Path(head["retained_input_ref"]).parent)
        table = "shop_daily_facts" if code == "shop_daily" else SPECS[code].fact_table
        expected = {r["source_row_key"]: r for r in candidate.rows}
        count = 0
        for row in (
            c.execution_options(stream_results=True, yield_per=1000)
            .execute(
                sa.text(f"SELECT * FROM public.{table} WHERE dataset_version_id=:v"),
                dict(v=head["id"]),
            )
            .mappings()
        ):
            assert row["source_row_key"] in expected
            assert all(row[k] == v for k, v in expected[row["source_row_key"]].items()), (
                "FIELD_MISMATCH"
            )
            count += 1
        assert count == len(expected)
        counts[code] = count
        inputs[code] = candidate.rows
    c.execution_options(stream_results=False)
filters = get("filters")
version = filters["meta"]["data_version"]
head_versions = {head["dataset_code"]: head["id"] for head in heads}
assert all(
    filters["meta"]["input_versions"].get(code) == head_versions[code]
    for code in inputs
), "INPUT_VERSION_CHANGED"
checks = 0
for lo, hi in [
    ("2026-10-26", "2026-10-26"),
    ("2026-10-01", "2026-10-27"),
    ("2026-08-31", "2026-09-02"),
    ("2026-02-01", "2026-10-27"),
]:
    for shop in [None, *[x[0] for x in SHOPS.values()]]:
        p = dict(data_version=version, date_from=lo, date_to=hi)
        if shop:
            p["shop_id"] = shop
        rows = [
            r
            for r in inputs["shop_daily"]
            if lo <= str(r["business_date"]) <= hi and (not shop or r["shop_id"] == shop)
        ]
        actual = get("performance", p)["data"]
        for f in ("shop_revenue", "shop_visitors", "buyers", "orders"):
            decimal_equal(actual[f], summed(rows, f), money=f == "shop_revenue")
        refund = {
            (r["shop_id"], r["business_date"]): r["reported_refund_amount"]
            for r in inputs["shop_refunds"]
        }
        matched = [
            r
            for r in rows
            if r["shop_revenue"] is not None
            and refund.get((r["shop_id"], r["business_date"])) is not None
        ]
        num = (
            sum((refund[(r["shop_id"], r["business_date"])] for r in matched), Decimal(0))
            if matched
            else None
        )
        decimal_equal(actual["refund_rate"], divided(num, summed(matched, "shop_revenue")))
        matched_keys = {(r["shop_id"], r["business_date"]) for r in matched}
        excluded = [r for r in rows if (r["shop_id"], r["business_date"]) not in matched_keys]
        decimal_equal(
            actual["refund_match"]["excluded_sales"],
            summed(excluded, "shop_revenue") if excluded else Decimal(0) if rows else None,
            money=True,
        )
        promotion = [
            r
            for code in ("shop_channel_product_promotion", "shop_channel_star_shop")
            for r in inputs.get(code, [])
            if lo <= str(r["business_date"]) <= hi
            and (not shop or r["shop_id"] == shop)
        ]
        decimal_equal(actual["promotion_spend"], summed(promotion, "spend"), money=True)
        paired = [r for r in promotion if r["clicks"] is not None and r["impressions"] is not None]
        decimal_equal(
            actual["promotion_ctr"],
            divided(summed(paired, "clicks"), summed(paired, "impressions")),
        )
        channel_checks = []
        selected_shops = [shop] if shop else [x[0] for x in SHOPS.values()]
        expected_days = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
        api_channels = {item["code"]: item for item in actual["promotion_channels"]}
        for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
            applicable = [
                sid
                for sid in selected_shops
                if code != "shop_channel_star_shop" or sid == SHOPS["663599807"][0]
            ]
            if not applicable:
                continue
            channel_rows = [
                r
                for r in inputs.get(code, [])
                if lo <= str(r["business_date"]) <= hi and r["shop_id"] in applicable
            ]
            observed_keys = {(r["shop_id"], r["business_date"]) for r in channel_rows}
            assert len(observed_keys) == len(channel_rows), "DUPLICATE_SHOP_CHANNEL_DAY"
            missing = expected_days * len(applicable) - len(observed_keys)
            channel_actual = api_channels[code]
            assert channel_actual["missing_shop_days"] == missing
            decimal_equal(
                channel_actual["spend"], summed(channel_rows, "spend"), money=True
            )
            decimal_equal(
                channel_actual["revenue"],
                summed(channel_rows, "attributed_revenue"),
                money=True,
            )
            roi_rows = [
                r
                for r in channel_rows
                if r["attributed_revenue"] is not None and r["spend"] is not None
            ]
            channel_roi = divided(
                summed(roi_rows, "attributed_revenue"), summed(roi_rows, "spend")
            )
            decimal_equal(channel_actual["roi"], channel_roi)
            channel_checks.append(
                (missing, len(roi_rows) != len(channel_rows), channel_roi)
            )
        # 以归档原行独立配对；多渠道只有所选店铺日和必要字段均齐备才发布合计ROI。
        roi_rows = [
            r
            for r in promotion
            if r["attributed_revenue"] is not None and r["spend"] is not None
        ]
        expected_roi = (
            divided(summed(roi_rows, "attributed_revenue"), summed(roi_rows, "spend"))
            if len(channel_checks) > 1
            and all(not missing and not nulls for missing, nulls, _ in channel_checks)
            else channel_checks[0][2]
            if len(channel_checks) == 1
            else None
        )
        decimal_equal(actual["promotion_roi"], expected_roi)
        for code, endpoint, fields in [
            (
                "shop_regions",
                "regions",
                ("transaction_amount", "transaction_orders", "transaction_buyers"),
            ),
            (
                "product_regions",
                "product-regions",
                (
                    "actual_shipped_amount",
                    "actual_shipped_qty",
                    "actual_returned_amount",
                    "actual_returned_qty",
                ),
            ),
        ]:
            source = [
                r
                for r in inputs[code]
                if lo <= str(r["business_date" if code == "shop_regions" else "period_start"]) <= hi
                and (not shop or r["shop_id"] == shop)
            ]
            if code == "product_regions":
                source = [
                    r
                    for r in source
                    if re.sub(r"[\s_－—-]", "", r["source_product_category"] or "")
                    not in {"包材", "儿童赠品", "分类未设置"}
                ]
            result = get(endpoint, p)["data"]
            groups = defaultdict(list)
            for r in source:
                groups[r["source_region_name"] or "地区缺失"].append(r)
            assert len(groups) == len(result["items"])
            for f in fields:
                decimal_equal(result["totals"][f], summed(source, f), money="amount" in f)
            for item in result["items"]:
                for f in fields:
                    decimal_equal(item[f], summed(groups[item["region"]], f), money="amount" in f)
        checks += 1
with engine.connect() as c:
    assert c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one() == before
summary = dict(
    rows=counts,
    all_projected_fields_equal=True,
    archive_checksums_valid=True,
    api_scopes=checks,
    api_read_did_not_create_tasks=True,
    input_versions=filters["meta"]["input_versions"],
    http_samples=len(latencies),
    http_max_ms=round(max(latencies), 2),
    target_state=get("targets", dict(month="2026-10", data_version=version))["data"]["state"],
)
path = root / "var/database-v2/m3-all-source-reconciliation.json"
path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
path.chmod(0o600)
print(json.dumps(summary, ensure_ascii=False))
engine.dispose()
