"""本机合成容量/性能基准；只新建随机PG库，完成后销毁，不读经营数据。"""

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
import sqlalchemy as sa  # noqa: E402
from app.modules.database_retention import require_local_test  # noqa: E402
from app.modules.fact_content import write_version  # noqa: E402
from migrations_v2.baseline_20261028.schema import build_metadata  # noqa: E402
from migrations_v2.local import local_url, migrate  # noqa: E402


def run(days=60, rows_per_day=500, versions=6):
    """滑动窗口+前一天2%晚到修订；旧整版与共享表示使用完全相同合成行。"""
    if not (2 <= days <= 90 and 1 <= rows_per_day <= 6000 and 2 <= versions <= 10):
        raise ValueError("LOCAL_BENCHMARK_LIMIT")
    name = "pdd_operations_v2_test_" + uuid4().hex[:12]
    engine = sa.create_engine(local_url(name), hide_parameters=True)
    require_local_test(engine)
    admin = sa.create_engine(
        local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
    facts = build_metadata().tables["public.product_sales_records"]
    timings = {"legacy": [], "shared": []}
    stats = []
    try:
        with engine.begin() as c:
            migrate(c)
        for offset in range(versions):
            rows = [
                dict(
                    source_row_key=f"{d}/{i}",
                    row_kind="detail",
                    shop_match_status="not_provided",
                    date_status="verified_period",
                    period_start=date(2026, 1, 1) + timedelta(days=d),
                    period_end=date(2026, 1, 1) + timedelta(days=d),
                    source_style_code=f"style-{i:06}",
                    source_product_name="合成容量测试商品",
                    source_product_code=f"sku-{i:06}",
                    source_subcategory="合成分类",
                    source_color_specification="合成颜色规格",
                    source_sales_qty=Decimal("1.5"),
                    sales_amount=Decimal("100.01")
                    + (Decimal(".02") if d < offset + days - 1 and i % 50 == 0 else 0),
                )
                for d in range(offset, offset + days)
                for i in range(rows_per_day)
            ]
            for mode in timings:
                vid = f"{mode}-{offset}"
                start = perf_counter()
                with engine.begin() as c:
                    c.execute(
                        sa.text("""INSERT INTO dataset_versions
                      (id,dataset_code,origin_kind,state,source_system,source_locator,row_schema_revision,
                       producer_revision,interpretation_ref) VALUES
                      (:id,'jst_shipped_sales','source','draft','synthetic','synthetic:benchmark','bench-v1','bench','synthetic:benchmark')"""),
                        {"id": vid},
                    )
                    if mode == "legacy":
                        for i in range(0, len(rows), 1000):
                            c.execute(
                                facts.insert(),
                                [
                                    dict(
                                        r,
                                        id=f"{vid}:{r['source_row_key']}",
                                        dataset_version_id=vid,
                                    )
                                    for r in rows[i : i + 1000]
                                ],
                            )
                    else:
                        stats.append(
                            write_version(c, "product_sales_records", vid, rows)
                        )
                    c.execute(
                        sa.text(
                            "UPDATE dataset_versions SET state='validated',content_fingerprint=:id,"
                            "validation_evidence_ref='synthetic:benchmark',"
                            "received_at=clock_timestamp() WHERE id=:id"
                        ),
                        {"id": vid},
                    )
                    c.execute(
                        sa.text(
                            "UPDATE dataset_versions SET state='published' WHERE id=:id"
                        ),
                        {"id": vid},
                    )
                timings[mode].append(round(perf_counter() - start, 4))
        with engine.begin() as c:
            for table in (
                "product_sales_records",
                "shared_product_sales_records",
                "fact_content_members",
                "fact_content_blocks",
            ):
                c.execute(sa.text(f"ANALYZE public.{table}"))
        queries = {}
        with engine.connect() as c:
            for mode in timings:
                samples = []
                for _ in range(7):
                    start = perf_counter()
                    value = c.execute(
                        sa.text(
                            "SELECT count(*),sum(sales_amount),sum(source_sales_qty) "
                            "FROM read_product_sales_records WHERE dataset_version_id=:v"
                        ),
                        {"v": f"{mode}-{versions - 1}"},
                    ).one()
                    samples.append(perf_counter() - start)
                queries[mode] = {
                    "median_ms": round(median(samples) * 1000, 3),
                    "result": [str(x) for x in value],
                }
            assert queries["legacy"]["result"] == queries["shared"]["result"]
            sizes = {
                t: c.scalar(
                    sa.text("SELECT pg_total_relation_size(CAST(:t AS regclass))"),
                    {"t": "public." + t},
                )
                for t in (
                    "product_sales_records",
                    "shared_product_sales_records",
                    "fact_content_blocks",
                    "fact_content_manifests",
                    "fact_content_members",
                )
            }
            physical = c.scalar(
                sa.text("SELECT count(*) FROM shared_product_sales_records")
            )
        return dict(
            days=days,
            rows_per_day=rows_per_day,
            versions=versions,
            logical_rows=days * rows_per_day * versions,
            shared_physical_rows=physical,
            bytes=sizes,
            write_seconds=timings,
            query=queries,
            blocks=stats,
            synthetic_only=True,
            production_enabled=False,
        )
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--rows-per-day", type=int, default=500)
    parser.add_argument("--versions", type=int, default=6)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.days, args.rows_per_day, args.versions),
            ensure_ascii=False,
            indent=2,
        )
    )
