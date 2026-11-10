"""推广查询真实PG回归：只读取必要经营窗，覆盖对象与来源原名仍按原范围。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.modules.promotion import query
from app.modules.promotion.repository import snapshot
from tests.test_promotion_api import client_for, publish_products

pytest_plugins = ["tests.sales_pg_support"]


@pytest.fixture(params=[False, True], autouse=True)
def content_storage_mode(request, monkeypatch):
    """两种物理表示都必须遵守相同的字段/日期/行数读取边界。"""
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", str(request.param).lower())


def promotion_row(day, product="900", *, name="窗口商品", spend="1"):
    return dict(
        id=f"{day}-{product}",
        shop_id="663599807",
        spu_id=product,
        spu_name=name,
        dt=str(day),
        start_dt=str(day),
        time_granularity="day",
        total_cost=spend,
        transaction_amount="4",
        net_transaction_amount="0",
        impression_count="10",
        click_count="2",
        transaction_count="1.5",
    )


def test_overview_reuses_full_scope_labels_and_only_reads_comparison_windows(
    sales_pg, tmp_path, monkeypatch
):
    current = [promotion_row(date(2026, 10, 1) + timedelta(days=n)) for n in range(27)]
    prior = [promotion_row(date(2025, 10, 20) + timedelta(days=n), spend="2") for n in range(8)]
    publish_products(
        sales_pg, tmp_path, [*current, *prior, promotion_row(date(2026, 10, 1), "901")]
    )
    label_calls = []
    original_labels = query.labels

    def count_labels(repo, rows):
        label_calls.append(len(rows))
        return original_labels(repo, rows)

    monkeypatch.setattr(query, "labels", count_labels)
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, cursor.rowcount))

    event.listen(sales_pg, "after_cursor_execute", record_sql)
    try:
        with snapshot(sales_pg, "product") as repo:
            lid = next(
                row["listing_id"]
                for row in repo.identity_rows(date(2026, 10, 27), date(2026, 10, 27))
                if row["source_product_id"] == "900"
            )
            scope = query.PromotionQuery(
                date(2026, 10, 20), date(2026, 10, 27), mode="product", products=(lid,)
            )
            result = query.overview(repo, scope)
            assert result["metrics"]["spend"]["value"] == Decimal("8")
            assert result["metrics"]["spend"]["previous"] == Decimal("8")
            assert result["metrics"]["spend"]["change"] == 0
            assert result["current_record_count"] == "8"
            assert result["dataset_record_count"] == "36"
            assert result["coverage"]["missing_count"] == 0
            assert all(p["previous"]["spend"]["value"] == Decimal("2") for p in result["series"])
    finally:
        event.remove(sales_pg, "after_cursor_execute", record_sql)
    assert len(label_calls) <= 3, "本期、前期各自筛选商品，固定来源范围的标签仍只构建一次"
    fact_queries = [
        item for item in statements if "FROM public.read_product_promotion_records" in item[0]
    ]
    assert all("SELECT *" not in sql and "source_payload" not in sql for sql, _ in fact_queries)
    metric_queries = [(sql, count) for sql, count in fact_queries if "spend" in sql]
    assert metric_queries and all("BETWEEN" in sql for sql, _ in metric_queries)
    assert sum(count for _, count in metric_queries) == 24


def test_coverage_keeps_out_of_window_members_and_current_name_filter(sales_pg, tmp_path):
    publish_products(
        sales_pg,
        tmp_path,
        [
            promotion_row(date(2026, 10, 20), name="旧名"),
            promotion_row(date(2026, 10, 27), name="新名"),
            promotion_row(date(2026, 10, 1), "901", name="仅历史有行"),
        ],
    )
    with snapshot(sales_pg, "product") as repo:
        scope = query.PromotionQuery(date(2026, 10, 20), date(2026, 10, 20), mode="product")
        result = query.overview(repo, scope)
        assert result["current_record_count"] == "1"
        assert result["coverage"]["missing_count"] == 1
        assert not result["series"][0]["missing"]  # 另一商品缺行不遮挡当天已有值。
        # 经营筛选取所选窗名称；覆盖对象仍按完整发布范围的最近来源名判定。
        old_name = query.overview(repo, query.replace(scope, keyword="旧名"))
        assert old_name["current_record_count"] == "1"
        assert old_name["coverage"]["missing_count"] == 0
        new_name = query.overview(repo, query.replace(scope, keyword="新名"))
        assert new_name["current_record_count"] == "0"
        assert new_name["coverage"]["missing_count"] == 1


def test_days_reads_only_selected_window_and_identity(sales_pg, tmp_path):
    current = promotion_row(date(2026, 10, 27))
    publish_products(
        sales_pg,
        tmp_path,
        [
            current,
            dict(current, id="second-method", promotion_name="另一方式", total_cost="2"),
            promotion_row(date(2026, 10, 26)),
            promotion_row(date(2026, 10, 27), "901"),
            promotion_row(date(2025, 10, 27), "902"),
        ],
    )
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        if "FROM public.read_product_promotion_records" in statement and "spend" in statement:
            statements.append((statement, cursor.rowcount))

    event.listen(sales_pg, "after_cursor_execute", record_sql)
    try:
        with snapshot(sales_pg, "product") as repo:
            identities = {
                row["source_product_id"]: row["listing_id"]
                for row in repo.identity_rows(date(2025, 10, 27), date(2026, 10, 27))
            }
            scope = query.PromotionQuery(
                date(2026, 10, 27),
                date(2026, 10, 27),
                mode="product",
                products=(identities["900"],),
            )
            result = query.days(repo, scope)
            assert result["total"] == 1
            assert result["items"][0]["spend"] == Decimal("3")
            # 仅比较期出现过的真实身份仍通过验证，本期无记录不读取其他商品填充。
            empty = query.days(repo, query.replace(scope, products=(identities["902"],)))
            assert empty["items"] == [] and empty["total"] == 0
            assert "rows" not in repo.__dict__
        with snapshot(sales_pg, "product") as next_repo:
            assert next_repo.label_cache == next_repo.coverage_member_cache == {}
    finally:
        event.remove(sales_pg, "after_cursor_execute", record_sql)
    assert len(statements) == 1
    statement, count = statements[0]
    assert count == 2
    assert "BETWEEN" in statement and "listing_id=ANY" in statement
    assert "SELECT *" not in statement and "source_payload" not in statement


@pytest.mark.parametrize("mode", ["product", "store"])
def test_overview_display_preserves_visible_contract_and_full_default(sales_pg, tmp_path, mode):
    if mode == "product":
        publish_products(sales_pg, tmp_path)
        day = "2026-10-27"
    else:
        from app.modules.sales.datasets import SPECS
        from app.modules.sales.source_normalization import normalize_source
        from app.modules.sales.storage import ShopDailyStore
        from tests.test_sales_extended_sources import sample, window

        for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
            ShopDailyStore(
                sales_pg, archive_root=tmp_path / code, approved=window(), spec=SPECS[code]
            ).publish_candidate(normalize_source(code, [sample(code)], window()))
        day = "2026-10-26"
    with client_for(sales_pg) as client:
        version = client.get("/api/v1/promotion/meta", params={"mode": mode}).json()["meta"][
            "data_version"
        ]
        params = dict(mode=mode, date_from=day, date_to=day, data_version=version)
        full = client.get("/api/v1/promotion/overview", params=params).json()["data"]
        explicit = client.get(
            "/api/v1/promotion/overview", params={**params, "detail": "full"}
        ).json()["data"]
        compact_response = client.get(
            "/api/v1/promotion/overview", params={**params, "detail": "display"}
        )
        assert compact_response.status_code == 200, compact_response.text
        compact = compact_response.json()["data"]
        assert full == explicit
        assert all("coverage" not in point for point in compact["series"])
        for field in (
            "coverage",
            "funnel",
            "top5",
            "windows",
            "historical_supplements",
            "current_record_count",
            "dataset_record_count",
        ):
            assert compact[field] == full[field]
        frontend_metric_fields = {
            "value",
            "status",
            "reason",
            "label",
            "type",
            "previous",
            "change",
            "comparison_status",
            "business_date_basis",
            "attribution_state",
        }

        def compare_metrics(original, displayed):
            assert original.keys() == displayed.keys()
            for key, value in original.items():
                assert displayed[key] == {
                    name: field for name, field in value.items() if name in frontend_metric_fields
                }

        compare_metrics(full["metrics"], compact["metrics"])
        compare_metrics(full["net"], compact["net"])
        for original, displayed in zip(full["series"], compact["series"], strict=True):
            for field in ("date", "previous_date", "missing"):
                assert original[field] == displayed[field]
            compare_metrics(original["metrics"], displayed["metrics"])
            compare_metrics(original["previous"], displayed["previous"])
        for original, displayed in zip(
            full["channel_summaries"], compact["channel_summaries"], strict=True
        ):
            assert original["channel"] == displayed["channel"]
            assert original["label"] == displayed["label"]
            compare_metrics(original["metrics"], displayed["metrics"])
            compare_metrics(original["net"], displayed["net"])
        assert (
            client.get(
                "/api/v1/promotion/overview", params={**params, "detail": "unsupported"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/promotion/overview",
                params={**params, "detail": "display", "data_version": "old"},
            ).status_code
            == 409
        )


def test_selected_options_resolve_published_identities_without_date_or_facts(sales_pg, tmp_path):
    conflict = promotion_row(date(2026, 10, 27), "902", name="名称甲")
    publish_products(
        sales_pg,
        tmp_path,
        [
            promotion_row(date(2026, 10, 20), name="旧名"),
            promotion_row(date(2026, 10, 27), name="最近名称"),
            promotion_row(date(2025, 10, 27), "901", name="只在比较期"),
            promotion_row(date(2026, 10, 27), "123", name="资料名称优先"),
            conflict,
            dict(conflict, id="conflict-other", spu_name="名称乙"),
        ],
    )
    with snapshot(sales_pg, "product") as repo:
        identities = {
            row["source_product_id"]: row["listing_id"]
            for row in repo.identity_rows(date(2025, 10, 27), date(2026, 10, 27))
        }
        version = repo.token
        assert date(2025, 10, 27) < repo.start
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        if "FROM public.read_product_promotion_records" in statement:
            statements.append(statement)

    selected = [identities[key] for key in ("901", "900", "123", "902", "901")]
    params = dict(mode="product", data_version=version, selected_ids=selected)
    event.listen(sales_pg, "before_cursor_execute", record_sql)
    try:
        with client_for(sales_pg) as client:
            response = client.get("/api/v1/promotion/options/selected", params=params)
            assert response.status_code == 200, response.text
            products = response.json()["data"]["products"]
            assert [row["value"] for row in products] == selected[:-1]
            assert [row["product_name"] for row in products] == [
                "只在比较期",
                "最近名称",
                "商品A",
                None,
            ]
            for product in products:
                assert set(product) == {
                    "value",
                    "listing_id",
                    "label",
                    "product_id",
                    "product_name",
                    "shop_id",
                    "category",
                    "level",
                    "level_details",
                    "operator",
                    "search_names",
                }
    finally:
        event.remove(sales_pg, "before_cursor_execute", record_sql)
    assert len(statements) == 1
    assert "listing_id=ANY" in statements[0]
    assert all(value not in statements[0] for value in ("SELECT *", "spend", "source_payload"))
    with client_for(sales_pg) as client:
        catalog = client.get(
            "/api/v1/promotion/options",
            params=dict(
                mode="product",
                data_version=version,
                date_from="2026-10-27",
                date_to="2026-10-27",
                catalog=True,
            ),
        ).json()["data"]["products"]
        assert identities["901"] not in {item["value"] for item in catalog}
        assert next(item for item in catalog if item["product_id"] == "900") == products[1]
        assert (
            client.get(
                "/api/v1/promotion/options/selected", params={**params, "data_version": "old"}
            ).status_code
            == 409
        )
        for invalid in ("bad", "00000000-0000-0000-0000-000000000001"):
            assert (
                client.get(
                    "/api/v1/promotion/options/selected",
                    params={**params, "selected_ids": [invalid]},
                ).status_code
                == 422
            )
        assert (
            client.get(
                "/api/v1/promotion/options/selected",
                params={**params, "selected_ids": selected * 5},
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/promotion/options/selected", params={**params, "mode": "store"}
            ).status_code
            == 422
        )
        empty = client.get(
            "/api/v1/promotion/options/selected", params=dict(mode="product", data_version=version)
        )
        assert empty.status_code == 200 and empty.json()["data"] == {"products": []}
