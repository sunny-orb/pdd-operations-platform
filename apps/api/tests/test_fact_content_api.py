"""同一既有业务验收在共享存储模式下运行；合成输入，禁止连接上游。"""

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]


@pytest.fixture(autouse=True)
def reuse_enabled(monkeypatch):
    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")


def test_product_promotion_business_contracts_with_shared_facts(sales_pg, tmp_path):
    from tests.test_promotion_api import (
        test_product_uses_ad_max_date_and_full_identity_not_operating_scope as check,
    )

    check(sales_pg, tmp_path)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_daily_facts")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_promotion_records")) == 3
        assert c.scalar(sa.text("SELECT count(*) FROM product_daily_facts")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM product_promotion_records")) == 0


def test_aftersales_source_reports_retry_and_failure_contract(sales_pg, tmp_path):
    from tests.test_aftersales_ingestion import (
        test_real_pg_source_report_idempotence_and_failure_preserves_pointer as check,
    )

    check(sales_pg, tmp_path)
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_return_records")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) > 0
        assert c.scalar(sa.text("SELECT count(*) FROM return_records")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM product_sales_records")) == 0


def test_region_exact_values_and_publisher_reads_previous_shared_version(sales_pg, tmp_path):
    from decimal import Decimal

    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_extended_sources import sample, window

    store = ShopDailyStore(
        sales_pg, archive_root=tmp_path, approved=window(), spec=SPECS["product_regions"]
    )
    for amount in ("100.01000000000001", "101.99"):
        candidate = normalize_source(
            "product_regions",
            [sample("product_regions", actual_shipped_amount=Decimal(amount))],
            window(),
        )
        result = store.publish_candidate(candidate)
        assert result.status == "succeeded"
    with sales_pg.connect() as c:
        values = (
            c.execute(
                sa.text(
                    "SELECT actual_shipped_amount FROM read_product_region_records "
                    "ORDER BY actual_shipped_amount"
                )
            )
            .scalars()
            .all()
        )
        assert values == [Decimal("100.01000000000001"), Decimal("101.99")]
        assert c.scalar(sa.text("SELECT count(*) FROM product_region_records")) == 0


def test_scoring_contract_and_partial_source_refresh(sales_pg, tmp_path):
    from tests.test_product_scoring_publication import (
        test_published_trial_is_atomic_idempotent_and_keeps_unknown_category as check,
    )

    check(sales_pg, tmp_path)
    from app.modules.product_board.repository import snapshot
    from app.modules.product_board.storage import ProductBoardStore
    from tests.test_product_scoring_publication import inputs

    store = ProductBoardStore(sales_pg, tmp_path / "rebuild")
    with snapshot(sales_pg) as repo:
        before = repo.profiles()
    store.rebuild_profiles()
    end, data = inputs()
    store.run_selected(
        {"product_daily"}, lambda hb: (end, {"product_daily": data["product_daily"]})
    )
    with snapshot(sales_pg) as repo:
        assert repo.profiles() == before


def test_data_status_current_source_dates(sales_pg, tmp_path):
    from tests.test_data_status import test_source_date_requires_current_version_snapshot as check

    check(sales_pg, tmp_path)


def test_every_historical_report_and_comparison_keeps_exact_business_response(sales_pg):
    from datetime import date

    from tests.test_aftersales_api import client_for, publish

    publish(sales_pg, "prev", start=date(2026, 8, 29), end=date(2026, 9, 27), shared=True)
    publish(sales_pg, "yoy", start=date(2025, 9, 28), end=date(2025, 10, 27), shared=True)
    publish(sales_pg, "old", previous_report_id="reportprev", yoy_report_id="reportyoy")
    publish(
        sales_pg,
        "new",
        revision_no=2,
        previous_report_id="reportprev",
        yoy_report_id="reportyoy",
        shared=True,
    )
    endpoints = (
        "overview",
        "options",
        "analysis?view=category",
        "analysis?view=style",
        "analysis?view=quality",
        "analysis?view=mom",
        "analysis?view=yoy",
        "analysis?view=history",
    )

    def read(client, report):
        result = {}
        for endpoint in endpoints:
            url = (
                "/api/v1/aftersales/"
                + endpoint
                + ("&" if "?" in endpoint else "?")
                + "report_id="
                + report
            )
            response = client.get(url)
            assert response.status_code == 200, (url, response.json())
            result[endpoint] = response.json()["data"]
        return result

    with client_for(sales_pg) as client:
        before = {r: read(client, r) for r in ("reportprev", "reportyoy", "reportold", "reportnew")}
        for endpoint in endpoints[1:-1]:
            assert before["reportold"][endpoint] == before["reportnew"][endpoint]
        from copy import deepcopy

        old, new = (deepcopy(before[r]["overview"]) for r in ("reportold", "reportnew"))
        for expected_id, body in (("reportold", old), ("reportnew", new)):
            assert body["report"].pop("id") == expected_id
            body["report"].pop("generated_at")
            assert body["trend"][0].pop("id") == expected_id
        assert old == new
        publish(sales_pg, "later", revision_no=3, missing=True, shared=True)
        after = {r: read(client, r) for r in before}
        # 显式选择的每份历史报告（含history视图）不得跟随最新输入漂移。
        for r in before:
            for endpoint in endpoints:
                if endpoint == "options":
                    # 报告下拉按既有规则切到最新修订；当前选择的分类不应改变。
                    assert after[r][endpoint]["reports"][0]["id"] == "reportlater"
                    assert {k: v for k, v in after[r][endpoint].items() if k != "reports"} == {
                        k: v for k, v in before[r][endpoint].items() if k != "reports"
                    }
                else:
                    assert after[r][endpoint] == before[r][endpoint]


def test_region_page_null_zero_weighted_rates_and_product_keys(sales_pg, tmp_path):
    from tests.test_sales_extended_api import (
        test_regions_null_zero_weighted_rates_and_product_keys as check,
    )

    check(sales_pg, tmp_path)


def test_product_producer_archive_matches_recorded_revision(sales_pg, tmp_path):
    from pathlib import Path

    from app.modules.product_board.source import fingerprint
    from tests.test_promotion_api import publish_products

    publish_products(sales_pg, tmp_path)
    with sales_pg.connect() as c:
        ref, revision = c.execute(
            sa.text(
                "SELECT retained_input_ref,producer_revision FROM dataset_versions "
                "WHERE dataset_code='product_daily'"
            )
        ).one()
    source = {p.name: p.read_text() for p in Path(ref).glob("*.py")}
    assert "fact_content.py" in source
    assert fingerprint(source) == revision


def test_aftersales_publisher_accepts_same_value_with_new_decimal_scale(sales_pg, tmp_path):
    from datetime import date
    from decimal import Decimal

    from app.modules.aftersales.publisher import publish_source
    from tests.test_aftersales_ingestion import sample

    ids = []
    for amount in ("100.0100", "100.01"):

        def receive(hb, amount=amount):
            end, raw, values = sample(date(2026, 10, 27))("jst_shipped_sales")
            raw[0]["synthetic_scale_case"] = amount
            values[0]["sales_amount"] = Decimal(amount)
            return end, raw, values

        ids.append(publish_source(sales_pg, "jst_shipped_sales", receive, tmp_path)["version_id"])
    assert ids[0] != ids[1]
    with sales_pg.connect() as c:
        for vid, amount in zip(ids, ("100.0100", "100.01"), strict=True):
            assert (
                c.scalar(
                    sa.text(
                        "SELECT sales_amount::text FROM read_product_sales_records "
                        "WHERE dataset_version_id=:v"
                    ),
                    {"v": vid},
                )
                == amount
            )
