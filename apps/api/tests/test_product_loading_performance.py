"""商品取数范围回归：优化不得截断比较窗口或改变资料、分页语义。"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.modules.product_board.metrics import compare, divide
from app.modules.product_board.query import BoardQuery, days, labels, matches, overview, score_cards
from app.modules.product_board.repository import snapshot, wire
from app.modules.product_board.source import normalize, previous_year
from app.modules.product_board.storage import ProductBoardStore
from tests.test_product_board_review import END, Repo
from tests.test_product_board_storage import candidate
from tests.test_product_metric_pairs import fact

pytest_plugins = ["tests.sales_pg_support"]


def test_default_31_days_only_fetches_missing_two_prior_days():
    class CountRepo(Repo):
        def __init__(self):
            super().__init__(
                [
                    fact(END, "current", product_revenue=Decimal(100)),
                    fact(END - timedelta(days=31), "prior_only", product_revenue=Decimal(20)),
                    fact(END - timedelta(days=60), "old", product_revenue=Decimal(30)),
                    fact(END - timedelta(days=61), "old", product_revenue=Decimal(40)),
                ]
            )
            self.calls = []

        def rows(self, start, end):
            self.calls.append((start, end))
            return super().rows(start, end)

    repo = CountRepo()
    result = overview(repo, BoardQuery(END - timedelta(days=30), END))
    assert result["metrics"]["revenue"]["mom_comparison"]["previous"] == 90
    assert result["metrics"]["revenue"]["mom_comparison"]["previous_rows"] == 3
    assert (END - timedelta(days=61), END - timedelta(days=60)) in repo.calls
    assert (END - timedelta(days=61), END - timedelta(days=31)) not in repo.calls


@pytest.fixture
def populated(sales_pg, tmp_path):
    end, data = candidate(duplicate_profile=False)
    records = []
    for shop in ("663599807", "799193231"):
        for pid in ("123", "456", "789", "10", "2", "9"):
            for offset in range(65):
                if pid == "456" and offset < 30:
                    continue
                day = end - timedelta(days=offset)
                records.append(
                    dict(
                        z_id=len(records) + 1,
                        shop_id=shop,
                        shop_name=f"店名{offset}",
                        spu_id=pid,
                        spu_name=None if offset == 0 else f"商品{pid}-{offset}",
                        dt=str(day),
                        time_granularity="day",
                        transaction_amount="1.005",
                        product_uv=0 if offset == 2 else 10,
                        transaction_users=2,
                        transaction_orders=3,
                        transaction_products=4,
                        favorite_product_users=1,
                    )
                )
            for offset in range(30):
                records.append(
                    dict(
                        records[-1],
                        z_id=len(records) + 1,
                        dt=str(previous_year(end - timedelta(days=offset))),
                        transaction_amount=None if offset == 1 else "0.50",
                    )
                )
    data["product_daily"] = normalize("product_daily", records, end)
    data["profile_records"] = normalize(
        "profile_records",
        [
            dict(__id=1, spigf="123", spmc="资料名", cpdj="新品", pl="衣", fzyy="甲"),
            dict(__id=2, spigf="456", cpdj="新品", pl="衣", fzyy="甲"),
            dict(__id=3, spigf="999", spmc="无经营新品", cpdj="新品", pl="帽"),
            dict(__id=4, spigf="789", cpdj="A级", pl="衣"),
            dict(__id=5, spigf="789", cpdj="B级", pl="衣"),
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    return sales_pg, end


def test_identity_query_compacts_rows_without_changing_names_or_filters(populated):
    engine, end = populated
    with snapshot(engine) as repo:
        full = repo.rows(repo.start, end)
        compact = repo.identity_rows(repo.start, end)
        expected, _ = labels(full, repo.profiles())
        assert labels(compact, repo.profiles())[0] == expected
        assert len(compact) <= 2 * len(expected)
        selected = next(x for x in expected.values() if x["product_id"] == "2")
        assert selected["product_name"] == "商品2-1"
        assert selected["shop_name"] == "店名59"


def test_scores_only_load_selected_new_products_within_original_recent_windows(populated):
    engine, end = populated
    with snapshot(engine) as repo:
        original = repo.rows
        calls = []

        def limited(start, stop, *, ads=False, listing_ids=None):
            assert listing_ids is not None, "评分不应再读取全量商品事实"
            assert start == end - timedelta(days=6 if ads else 13)
            calls.append((ads, set(listing_ids)))
            return original(start, stop, ads=ads, listing_ids=listing_ids)

        repo.rows = limited
        result = score_cards(repo, BoardQuery(repo.start, end, size=12))
        assert result["total_count"] == result["total"] == 3
        assert {r["product_id"] for r in result["items"]} == {"123", "456", "999"}
        active = next(r for r in result["items"] if r["product_id"] == "123")
        assert active["revenue"] == Decimal("7.035")
        assert active["visitors"] == 60
        assert all(r["revenue"] is None for r in result["items"] if r["product_id"] != "123")
        assert len(calls) == 2 and calls[0][1] == calls[1][1]
        filtered = score_cards(repo, BoardQuery(repo.start, end, category="帽"))
        assert filtered["total_count"] == 3 and filtered["total"] == 1


def legacy_days(repo, q):
    """冻结旧版全量取数/内存分页作为独立参照，检验SQL分页及同比配对。"""
    all_rows = repo.rows(repo.start, repo.end)
    info, _ = labels(
        [r for r in all_rows if q.start <= r["business_date"] <= q.end], repo.profiles()
    )
    info = {lid: value for lid, value in info.items() if matches(value, q)}
    rows = [
        r for r in all_rows if r["listing_id"] in info and q.start <= r["business_date"] <= q.end
    ]
    rows.sort(
        key=lambda r: (
            -r["business_date"].toordinal(),
            r["source_shop_id"],
            r["source_product_id"],
            r["id"],
        )
    )
    older = repo.rows(previous_year(q.start), previous_year(q.end))
    old = {(r["listing_id"], r["business_date"]): r for r in older}
    items = []
    for row in rows[(q.page - 1) * q.size : q.page * q.size]:
        lid = row["listing_id"]
        previous = old.get((lid, previous_year(row["business_date"])))
        items.append(
            dict(
                info[lid],
                date=row["business_date"],
                product_name=repo.profiles().get(lid, {}).get("product_name")
                or row["source_product_name"],
                visitors=None if row["visitors"] is None else str(row["visitors"]),
                revenue=row["product_revenue"],
                conversion=divide(row["buyers"], row["visitors"]),
                yoy=compare(row["product_revenue"], previous["product_revenue"])
                if previous
                else None,
            )
        )
    return dict(
        items=items, total=len(rows), page=q.page, size=q.size, scope=dict(start=q.start, end=q.end)
    )


def test_sql_day_pages_match_legacy_for_filters_empty_pages_and_precise_values(populated):
    engine, end = populated
    with snapshot(engine) as repo:
        scopes = [
            dict(),
            dict(shop="663599807"),
            dict(operator="甲"),
            dict(category="衣"),
            dict(level="多等级"),
            dict(level="未匹配"),
            dict(keyword="商品2-1"),
            dict(keyword="资料名"),
            dict(keyword="不存在"),
        ]
        queries = [
            BoardQuery(end - timedelta(days=30), end, **scope, page=page, size=size)
            for scope in scopes
            for page, size in [(1, 25), (2, 50), (999, 100)]
        ]
        expected = [wire(legacy_days(repo, q)) for q in queries]

        def forbid_full_rows(*args, **kwargs):
            raise AssertionError("明细不应读取整批经营事实后再分页")

        repo.rows = forbid_full_rows
        assert [wire(days(repo, q)) for q in queries] == expected


def test_identity_projection_keeps_conflicts_empty_names_and_unmatched_identity(sales_pg):
    import sqlalchemy as sa

    from app.modules.product_board import read_queries

    # 只在隔离库新建draft测试样例，不修改已发布不可变事实。
    with sales_pg.begin() as c:
        c.execute(
            sa.text("""INSERT INTO dataset_versions
            (id,dataset_code,origin_kind,state,source_system,source_locator,
             row_schema_revision,interpretation_ref,producer_revision)
            VALUES ('projection-test','product_daily','source','draft','synthetic',
                    'synthetic://test','test','test','test')""")
        )
        table = sa.Table("product_daily_facts", sa.MetaData(), schema="public", autoload_with=c)
        records = [
            dict(
                id=str(i),
                dataset_version_id="projection-test",
                source_row_key=str(i),
                listing_id=None,
                listing_match_status="unmatched",
                business_date=day,
                date_status="parsed_label",
                source_shop_id="s",
                source_shop_name=shop,
                source_product_id=str(i),
                source_product_name=name,
            )
            for i, (day, shop, name) in enumerate(
                [
                    (END - timedelta(days=5), "首行店名", "旧名"),
                    (END, "末行店名", "冲突名甲"),
                    (END, "末行店名", "冲突名乙"),
                    (END, "末行店名", None),
                ]
            )
        ]
        c.execute(table.insert(), records)
        compact = read_queries.identity_rows(c, "projection-test", END - timedelta(days=5), END)
        assert labels(compact, {})[0] == labels(records, {})[0]
        assert labels(compact, {})[0][None]["product_name"] is None
        page, count = read_queries.page_rows(
            c, "projection-test", END - timedelta(days=5), END, [None], 1, 2
        )
        assert count == 4 and len(page) == 2
        assert [r["id"] for r in page] == ["1", "2"]
        assert len(read_queries.rows_at(c, "projection-test", [(None, END)])) == 3
        assert read_queries.page_rows(c, "projection-test", END, END, [], 1, 25) == ([], 0)


def test_day_pagination_keeps_exact_leap_date_mapping(sales_pg, tmp_path):
    from datetime import date

    end, data = candidate(duplicate_profile=False)
    end = date(2024, 3, 1)
    dates = ["2024-02-28", "2024-02-29", "2024-03-01", "2023-02-28", "2023-03-01"]
    data["product_daily"] = normalize(
        "product_daily",
        [
            dict(
                z_id=i + 1,
                shop_id="663599807",
                spu_id="123",
                dt=day,
                time_granularity="day",
                product_uv=10,
                transaction_users=2,
                transaction_amount="100" if day.startswith("2024") else "50",
            )
            for i, day in enumerate(dates)
        ],
        end,
    )
    ProductBoardStore(sales_pg, tmp_path).run(lambda hb: (end, data))
    with snapshot(sales_pg) as repo:
        result = days(repo, BoardQuery(date(2024, 2, 28), end))
        assert result["total"] == 3
        assert [row["yoy"] for row in result["items"]] == [1, None, 1]
        leap_only = days(repo, BoardQuery(date(2024, 2, 29), date(2024, 2, 29)))
        assert leap_only["total"] == 1 and leap_only["items"][0]["yoy"] is None


def test_score_window_never_reads_before_original_repository_start(populated):
    engine, end = populated
    with snapshot(engine) as repo:
        repo.start = end - timedelta(days=3)
        original = repo.rows

        def limited(start, stop, *, ads=False, listing_ids=None):
            if not ads:
                assert start == repo.start
            return original(start, stop, ads=ads, listing_ids=listing_ids)

        repo.rows = limited
        result = score_cards(repo, BoardQuery(repo.start, end))
        active = next(row for row in result["items"] if row["product_id"] == "123")
        assert active["revenue"] == Decimal("4.020")
        assert active["coverage"] == "partial"
