"""本地只读看板性能基线；记录延迟、SQL耗时和响应摘要，不保存业务明细。"""

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.main import create_app
from app.core.config import Settings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--repeat", type=int, default=5)
    p.add_argument("--cases", help="沿用先前结果的日期/报告/筛选，仅刷新当前版本令牌")
    p.add_argument(
        "--only",
        action="append",
        help="仅复测指定路径，如sales/product-regions/filters",
    )
    args = p.parse_args()
    if not 1 <= args.repeat <= 50:
        raise ValueError("repeat must be 1..50")
    app = create_app(Settings(_env_file=ROOT / "apps/api/.env"))
    results = []
    with TestClient(app) as client:
        engine = app.state.engine
        if (
            engine.url.host not in ("localhost", "127.0.0.1")
            or engine.url.database != "pdd_operations"
        ):
            raise ValueError("LOCAL_DATABASE_REQUIRED")
        sql = []

        @event.listens_for(engine, "before_cursor_execute")
        def before(conn, cursor, statement, params, context, many):
            context.perf_start = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def after(conn, cursor, statement, params, context, many):
            sql.append((time.perf_counter() - context.perf_start) * 1000)

        def get(path, params=None):
            response = client.get("/api/v1/" + path, params=params or {})
            if response.status_code != 200:
                raise RuntimeError(f"{path}: HTTP {response.status_code}")
            return response

        cases = []
        for domain, modes in [("products", [""]), ("promotion", ["store", "product"])]:
            for mode in modes:
                opt = dict(mode=mode) if mode else {}
                m = get(domain + "/meta", opt).json()
                end = date.fromisoformat(m["data"]["date_to"])
                q = dict(
                    opt,
                    date_from=str(end - timedelta(days=29)),
                    date_to=str(end),
                    data_version=m["meta"]["data_version"],
                )
                cases.append((domain + "/meta", opt))
                for endpoint in (
                    ["overview", "options", "scores", "days"]
                    if domain == "products"
                    else ["overview", "options", "days", "diagnosis"]
                ):
                    cases.append((domain + "/" + endpoint, q))
        m = get("sales/filters").json()
        token = m["meta"]["data_version"]
        q = dict(date_from="2026-10-01", date_to="2026-10-29", data_version=token)
        cases += [("sales/filters", {})]
        cases += [
            ("sales/" + name, q)
            for name in [
                "performance",
                "shops",
                "ratings",
                "regions",
                "product-regions",
            ]
        ]
        cases += [
            ("sales/product-regions/filters", dict(data_version=token)),
            ("sales/targets", dict(month="2026-10", data_version=token)),
        ]
        m = get("aftersales/meta").json()
        q = dict(
            report_id=m["data"]["default_report_id"],
            data_version=m["meta"]["data_version"],
        )
        cases += [
            ("aftersales/" + name, {} if name == "meta" else q)
            for name in ["meta", "options", "overview", "analysis"]
        ]
        m = get("weather/meta").json()
        cases += [
            ("weather/meta", {}),
            (
                "weather/publication",
                dict(
                    publication_id=m["data"]["default_publication_id"],
                    data_version=m["data"]["data_version"],
                ),
            ),
        ]
        if args.cases:
            tokens = {
                (path.split("/")[0], q.get("mode", "")): q.get("data_version")
                for path, q in cases
                if q.get("data_version")
            }
            fixed = []
            for prior in json.loads(Path(args.cases).read_text()):
                path, q = prior["path"], dict(prior["params"])
                if "data_version" in q:
                    q["data_version"] = tokens[(path.split("/")[0], q.get("mode", ""))]
                fixed.append((path, q))
            cases = fixed
        if args.only:
            cases = [(path, q) for path, q in cases if path in args.only]
            if not cases:
                raise ValueError("NO_MATCHING_CASES")
        for path, q in cases:
            times = []
            sql_times = []
            counts = []
            hashes = []
            for _ in range(args.repeat):
                sql.clear()
                at = time.perf_counter()
                r = get(path, q)
                times.append((time.perf_counter() - at) * 1000)
                sql_times.append(sum(sql))
                counts.append(len(sql))
                hashes.append(
                    hashlib.sha256(
                        json.dumps(r.json()["data"], sort_keys=True).encode()
                    ).hexdigest()
                )
            row = dict(
                path=path,
                params=q,
                ms=[round(x, 2) for x in times],
                median_ms=round(statistics.median(times), 2),
                p95_ms=round(sorted(times)[math.ceil(len(times) * 0.95) - 1], 2),
                sql_median_ms=round(statistics.median(sql_times), 2),
                sql_count=counts,
                response_bytes=len(r.content),
                data_hashes=hashes,
            )
            results.append(row)
            print(
                path,
                q.get("mode", ""),
                row["median_ms"],
                row["sql_median_ms"],
                counts[-1],
                flush=True,
            )
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    dest.chmod(0o600)


if __name__ == "__main__":
    main()
