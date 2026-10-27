"""M1本地只读对账：归档全投影与PG逐格比对，API与独立SQL复算，不访问上游。"""

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.core.config import Settings
from app.db.connection import build_engine
from app.modules.product_board.source import fingerprint
from app.modules.product_board.storage import FACT_TABLES
from app.modules.sales.source_policy import SHOPS


def main():
    engine = build_engine(Settings(_env_file=ROOT / "apps/api/.env"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path, params=None):
        url = (
            "http://127.0.0.1:8000/api/v1/products/"
            + path
            + "?"
            + urllib.parse.urlencode(params or {})
        )
        with opener.open(url, timeout=30) as r:
            return json.load(r)

    counts = {}
    timings = []
    try:
        with engine.connect() as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            task_count = c.execute(
                sa.text("SELECT count(*) FROM public.task_runs")
            ).scalar_one()
            for code in ("product_daily", "profile_records", "product_promotion"):
                version = (
                    c.execute(
                        sa.text(
                            "SELECT v.* FROM public.current_datasets d JOIN public.dataset_versions v ON d.version_id=v.id WHERE d.dataset_code=:code"
                        ),
                        {"code": code},
                    )
                    .mappings()
                    .one()
                )
                archive = Path(version["retained_input_ref"]).resolve()
                if not archive.is_relative_to(ROOT / "var/ingestion/product_board"):
                    raise RuntimeError("归档路径越界")
                data = json.loads((archive / (code + ".json")).read_text())
                expected = {r["source_row_key"]: fingerprint(r) for r in data}
                keys = tuple(data[0]) if data else ()
                found = {
                    r["source_row_key"]: fingerprint({k: r[k] for k in keys})
                    for r in c.execute(
                        sa.text(
                            f"SELECT * FROM public.{FACT_TABLES[code]} WHERE dataset_version_id=:v"
                        ),
                        {"v": version["id"]},
                    ).mappings()
                }
                if expected != found:
                    raise RuntimeError("归档与PG全投影不一致")
                manifest = json.loads((archive / "manifest.json").read_text())
                if any(
                    fingerprint((archive / name).read_text()) != value
                    for name, value in manifest["files"].items()
                ):
                    raise RuntimeError("归档校验失败")
                counts[code] = len(found)
            meta = get("meta")["data"]
            for shop in ("", *SHOPS):
                for start in (
                    meta["date_to"],
                    str(date.fromisoformat(meta["date_to"]) - timedelta(days=30)),
                ):
                    params = {
                        "date_from": start,
                        "date_to": meta["date_to"],
                        "data_version": meta["data_version"],
                    }
                    if shop:
                        params["shop_id"] = shop
                    at = time.monotonic()
                    result = get("overview", params)["data"]
                    timings.append(round((time.monotonic() - at) * 1000, 2))
                    amount = c.execute(
                        sa.text(
                            "SELECT sum(f.product_revenue) FROM public.product_daily_facts f JOIN public.current_datasets d ON d.version_id=f.dataset_version_id WHERE d.dataset_code='product_daily' AND f.business_date BETWEEN :start AND :end AND (:shop='' OR f.source_shop_id=:shop)"
                        ),
                        {"start": start, "end": meta["date_to"], "shop": shop},
                    ).scalar_one()
                    value = result["metrics"]["revenue"]["value"]
                    from decimal import Decimal

                    if (None if value is None else Decimal(value)) != amount:
                        raise RuntimeError("API与独立PG金额不一致")
            if (
                c.execute(sa.text("SELECT count(*) FROM public.task_runs")).scalar_one()
                != task_count
            ):
                raise RuntimeError("只读查询意外触发任务")
        output = {
            "status": "passed",
            "projection_rows": counts,
            "comparison_groups": len(timings),
            "http_ms": timings,
            "task_count_unchanged": True,
        }
        path = ROOT / "var/m1-rebuild/reconciliation.json"
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        path.chmod(0o600)
        print(json.dumps(output, ensure_ascii=False))
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    main()
