"""目标只读验收：新源归档必要投影→PG→HTTP；不调用被测计算、不访问上游。"""

import json
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.core.config import Settings
from app.db.connection import build_engine


def main():
    engine = build_engine(Settings(_env_file=ROOT / "apps/api/.env"))
    opener = build_opener(ProxyHandler({}))

    def get(path, **params):
        with opener.open(
            "http://127.0.0.1:8000/api/v1/sales/" + path + "?" + urlencode(params),
            timeout=30,
        ) as response:
            return json.load(response)

    try:
        with engine.connect() as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            before = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
            head = (
                c.execute(
                    sa.text(
                        "SELECT v.* FROM current_datasets d JOIN dataset_versions v ON v.id=d.version_id "
                        "WHERE d.dataset_code='sales_targets'"
                    )
                )
                .mappings()
                .one()
            )
            folder = Path(head["retained_input_ref"]).resolve().parent
            assert folder.is_relative_to(ROOT / "var/ingestion/sales_targets")
            raw = [
                json.loads(line, parse_float=Decimal)
                for line in (folder / "input.ndjson").read_text().splitlines()
            ]
            expected = {}
            for row in raw:
                year, month = int(row["年份"][0][:-1]), int(row["月份"][0][:-1])
                if (year, month) == (2026, 1):
                    continue
                key = date(year, month, 1)
                assert row["渠道"] == ["拼多多"] and key not in expected
                expected[key] = Decimal(str(row["GMV基础"])) * 10000
            stored = (
                c.execute(
                    sa.text("SELECT * FROM sales_targets WHERE dataset_version_id=:v"),
                    dict(v=head["id"]),
                )
                .mappings()
                .all()
            )
            assert {r["period_start"]: r["target_amount"] for r in stored} == expected
            assert len(expected) == 12
            annual = sum(expected.values())
            members = (
                c.execute(
                    sa.text(
                        "SELECT m.target_id,m.shop_id FROM sales_target_shops m JOIN sales_targets t ON t.id=m.target_id WHERE t.dataset_version_id=:v"
                    ),
                    dict(v=head["id"]),
                )
                .mappings()
                .all()
            )
            by_target = {
                r["id"]: {m["shop_id"] for m in members if m["target_id"] == r["id"]}
                for r in stored
            }
            assert (
                len(members) == 72
                and len({frozenset(v) for v in by_target.values()}) == 1
            )
            shops = list(next(iter(by_target.values())))
            assert len(shops) == 6
            version = get("filters")["meta"]["data_version"]
            for month in ("2026-09", "2026-10"):
                data = get("targets", month=month, data_version=version)["data"]
                assert data["target_basis"] == "base"
                assert (
                    Decimal(data["month_target"])
                    == expected[date.fromisoformat(month + "-01")]
                )
                assert Decimal(data["annual_target"]) == annual
                for prefix in ("monthly", "annual"):
                    window = data[prefix + "_window"]
                    actual = c.execute(
                        sa.text(
                            "SELECT sum(f.shop_revenue) FROM shop_daily_facts f JOIN current_datasets d ON d.version_id=f.dataset_version_id WHERE d.dataset_code='shop_daily' AND f.shop_id=ANY(:shops) AND f.business_date BETWEEN :a AND :b AND f.shop_revenue>=0"
                        ),
                        dict(shops=shops, a=window["start"], b=window["end"]),
                    ).scalar_one()
                    metric = "month" if prefix == "monthly" else "annual"
                    assert Decimal(data[metric + "_actual"]) == actual
                    target = Decimal(data[metric + "_target"])
                    ratio = (actual / target).quantize(
                        Decimal(".000000000001"), rounding=ROUND_HALF_UP
                    )
                    assert Decimal(data[metric + "_ratio"]) == ratio
                assert Decimal(data["gap"]) == max(
                    Decimal(data["month_target"]) - Decimal(data["month_actual"]), 0
                )
            assert (
                c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
                == before
            )
        print(
            json.dumps(
                dict(
                    status="passed",
                    raw_rows=len(raw),
                    targets=12,
                    members=72,
                    months_checked=2,
                    task_count_unchanged=True,
                )
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
