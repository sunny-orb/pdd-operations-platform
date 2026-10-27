"""只读核对本机M3当前必要归档→PG→API；不访问上游，不输出经营行或凭据。"""

import json
import sys
import time
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import median
from urllib.parse import urlencode

sys.path.insert(0, str(Path.cwd() / "apps/api"))
import sqlalchemy as sa
from app.core.config import Settings
from app.db.connection import build_engine
from app.modules.sales.archive_replay import restore_candidate
from app.modules.sales.source_policy import SHOPS

root = Path.cwd()
engine = build_engine(Settings(_env_file=root / "apps/api/.env"))
assert engine.url.host in {"127.0.0.1", "localhost"}
assert engine.url.port == 55432 and engine.url.database == "pdd_operations"
with engine.connect() as c:
    v = (
        c.execute(
            sa.text(
                "SELECT v.* FROM dataset_versions v JOIN current_datasets d ON d.version_id=v.id WHERE d.dataset_code='shop_daily'"
            )
        )
        .mappings()
        .one()
    )
    pg = (
        c.execute(
            sa.text("SELECT * FROM shop_daily_facts WHERE dataset_version_id=:v"),
            {"v": v["id"]},
        )
        .mappings()
        .all()
    )
    before = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
candidate = restore_candidate(Path(v["retained_input_ref"]).parent)
manifest = json.loads(Path(v["interpretation_ref"]).read_text())
assert manifest["producer_revision"] == v["producer_revision"]
assert manifest["fingerprint"] == v["content_fingerprint"]
raw = candidate.raw_rows
indexed = {r["source_row_key"]: r for r in pg}
assert len(raw) == len(pg)
for r in candidate.rows:
    for k in r:
        assert r[k] == indexed[r["source_row_key"]][k], k
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with opener.open(
        "http://127.0.0.1:8000/api/v1/sales/" + path, timeout=5
    ) as response:
        return json.load(response)


f = get("filters")
checks = 0
timings = {}
assert f["meta"]["data_version"] == v["id"]
for start, end in [
    ("2026-10-26", "2026-10-26"),
    ("2026-10-01", "2026-10-27"),
    ("2026-08-31", "2026-09-02"),
    (str(candidate.window.start), str(candidate.window.end)),
]:
    samples = []
    for shop in [None, *[s[0] for s in SHOPS.values()]]:
        selected = [
            r
            for r in candidate.rows
            if start <= str(r["business_date"]) <= end
            and (not shop or shop == r["shop_id"])
        ]
        sums = {
            key: sum((r[key] for r in selected), Decimal(0))
            for key in ["shop_revenue", "shop_visitors", "buyers", "orders"]
        }
        params = {
            "date_from": start,
            "date_to": end,
            "data_version": f["meta"]["data_version"],
        }
        if shop:
            params["shop_id"] = shop
        started = time.monotonic()
        actual = get("performance?" + urlencode(params))["data"]
        samples.append(round((time.monotonic() - started) * 1000, 2))
        for key, value in sums.items():
            assert Decimal(actual[key]) == value, key
        for key, num, den in [
            ("buyer_conversion", "buyers", "shop_visitors"),
            ("aov", "shop_revenue", "buyers"),
        ]:
            expected = (
                (sums[num] / sums[den]).quantize(
                    Decimal("0.000000000001"), rounding=ROUND_HALF_UP
                )
                if sums[den] > 0
                else None
            )
            assert (
                Decimal(actual[key]) if actual[key] is not None else None
            ) == expected, key
        assert actual["promotion_spend"] is None
        checks += 1
    timings[start + ":" + end] = {
        "samples": len(samples),
        "min_ms": min(samples),
        "median_ms": median(samples),
        "max_ms": max(samples),
    }
with engine.connect() as c:
    assert c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one() == before
summary = {
    "source_rows": len(raw),
    "pg_rows": len(pg),
    "all_projected_fields_equal": True,
    "archive_checksums_valid": True,
    "api_scope_checks": checks,
    "get_did_not_create_tasks": True,
    "source_aov_null_count": sum(r["source_aov"] is None for r in pg),
    "data_version": v["id"],
    "development_http_response_ms": timings,
}
path = root / "var/database-v2/m3-live-reconciliation.json"
path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
path.chmod(0o600)
print(json.dumps(summary, ensure_ascii=False))
engine.dispose()
