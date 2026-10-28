"""M5本地只读独立SQL/API核验；不读上游、不生成业务快照。"""

import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url


def main():
    engine = sa.create_engine(local_url("pdd_operations"), hide_parameters=True)
    opener = build_opener(ProxyHandler({}))
    timings = []

    def get(path, **q):
        started = time.monotonic()
        with opener.open(
            "http://127.0.0.1:8000/api/v1/aftersales/" + path + "?" + urlencode(q),
            timeout=90,
        ) as response:
            result = json.load(response)
        timings.append(round(time.monotonic() - started, 3))
        return result

    try:
        with engine.connect() as c:
            before = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
            meta = get("meta")["data"]
            assert meta["status"] == "partial" and meta["observed_to"] is not None
            opt = get("options", data_version=meta["data_version"])["data"]
            assert opt["reports"]
            assert meta["observed_to"] == max(r["period_end"] for r in opt["reports"])
            groups = 0
            for report in opt["reports"]:
                q = {"report_id": report["id"], "data_version": meta["data_version"]}
                overview = get("overview", **q)["data"]
                observed = {m["key"]: m["value"] for m in overview["metrics"]}
                role = (
                    c.execute(
                        sa.text(
                            "SELECT i.input_role,i.input_version_id,i.window_start,i.window_end FROM version_inputs i JOIN aftersales_reports r ON r.dataset_version_id=i.output_version_id WHERE r.id=:r"
                        ),
                        {"r": report["id"]},
                    )
                    .mappings()
                    .all()
                )
                refs = {x["input_role"]: x for x in role}
                r = refs["j1_returns"]
                actual = c.execute(
                    sa.text(
                        "SELECT count(*),sum(actual_returned_qty),count(actual_returned_qty),sum(actual_inbound_amount) FROM return_records WHERE dataset_version_id=:v AND period_start>=:s AND period_end<=:e"
                    ),
                    {
                        "v": r["input_version_id"],
                        "s": r["window_start"],
                        "e": r["window_end"],
                    },
                ).one()
                assert observed["records"] == str(actual[0])
                assert (
                    Decimal(observed["returned_qty"]) == actual[1]
                    and actual[0] == actual[2]
                )
                assert observed["inbound_amount"] is None and actual[3] is None
                r = refs["j2_shipped_sales"]
                nulls = c.execute(
                    sa.text(
                        "SELECT count(*) FILTER (WHERE sales_amount IS NULL) FROM product_sales_records WHERE dataset_version_id=:v AND period_start>=:s AND period_end<=:e"
                    ),
                    {
                        "v": r["input_version_id"],
                        "s": r["window_start"],
                        "e": r["window_end"],
                    },
                ).scalar_one()
                if nulls:
                    assert observed["gmv"] is None
                for k in ("aftersale_rate", "total_rate", "size_qty", "quality_qty"):
                    assert observed[k] is None
                for view, columns in [
                    ("category", 12),
                    ("style", 13),
                    ("mom", 7),
                    ("yoy", 7),
                    ("quality", 3),
                    ("history", 10),
                ]:
                    body = get("analysis", view=view, **q)["data"]
                    assert len(body["columns"]) == columns
                    if body["total"] > 25:
                        second = get("analysis", view=view, page=2, **q)["data"]
                        assert not (
                            {x["id"] for x in body["rows"]}
                            & {x["id"] for x in second["rows"]}
                        )
                    groups += 1
            default = get("overview", data_version=meta["data_version"])["data"]
            assert len(default["trend"]) == min(8, len(opt["reports"]))
            assert (
                get("analysis", view="history", data_version=meta["data_version"])[
                    "data"
                ]["total"]
                == 3
            )
            after = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
            assert before == after
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "reports": len(opt["reports"]),
                        "analysis_groups": groups,
                        "source_date": meta["observed_to"],
                        "http_calls": len(timings),
                        "max_seconds": max(timings),
                        "get_created_tasks": after - before,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
