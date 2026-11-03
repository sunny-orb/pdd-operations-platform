"""店铺推广同期：来源范围隔离、真实PG发布与缺日不遮挡已有记录。"""

from datetime import date
from decimal import Decimal

import pytest

from app.modules.sales.contracts import SalesError
from app.modules.sales.source_policy import approved_window, validate_read_window

pytest_plugins = ["tests.sales_pg_support"]


def test_prior_window_is_only_authorized_for_two_promotion_sources():
    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        window = approved_window(code)
        assert window.start == date(2025, 2, 1)
        validate_read_window(window, code)
        with pytest.raises(SalesError, match="SOURCE_WINDOW_NOT_APPROVED"):
            validate_read_window(window, "shop_regions")
    assert approved_window().start == date(2026, 2, 1)
    assert approved_window("shop_ratings").start == date(2026, 2, 1)


def test_all_sources_cli_publishes_each_authorized_window(monkeypatch):
    import sys
    from types import SimpleNamespace

    from app.jobs import sync_sales_sources as job
    from app.modules.sales.contracts import PublishResult

    codes = ("shop_ratings", "shop_channel_product_promotion", "shop_channel_star_shop")
    monkeypatch.setattr(job, "SPECS", {code: job.SPECS[code] for code in codes})
    monkeypatch.setattr(job.getpass, "getpass", lambda _: "test-only")
    monkeypatch.setattr(job, "runtime_engine", lambda: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(
        job,
        "SalesSourceReader",
        lambda **_: SimpleNamespace(
            close=lambda: None,
            read_stable=lambda code, window: SimpleNamespace(window=window),
        ),
    )
    received = {}

    class Store:
        def __init__(self, engine, *, archive_root, spec):
            self.code = spec.code

        def run(self, receive):
            received[self.code] = receive(lambda: None).window
            return PublishResult("test-run", None, False, "succeeded")

    monkeypatch.setattr(job, "ShopDailyStore", Store)
    approved = approved_window()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync-sales-sources",
            "--dataset",
            "all",
            "--publish",
            "--from",
            str(approved.start),
            "--to",
            str(approved.end),
        ],
    )
    assert job._main() == 0
    assert received == {code: approved_window(code) for code in codes}


def test_prior_and_current_keep_available_shop_channel_rows(sales_pg, tmp_path):
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_promotion_api import client_for
    from tests.test_sales_extended_sources import sample

    for code in ("shop_channel_product_promotion", "shop_channel_star_shop"):
        window = approved_window(code)
        dates = ["2025-10-01", "2025-10-02", "2026-10-01", "2026-10-02"]
        if code.endswith("star_shop"):
            dates = ["2025-10-01", "2026-10-01"]
        rows = [sample(code, id=f"{code}-{day}", dt=day) for day in dates]
        store = ShopDailyStore(sales_pg, archive_root=tmp_path / code, spec=SPECS[code])
        candidate = normalize_source(code, rows, window)
        store.publish_candidate(candidate)
        assert store.publish_candidate(candidate).reused
    with client_for(sales_pg) as client:
        version = client.get("/api/v1/promotion/meta?mode=store").json()["meta"]["data_version"]
        response = client.get(
            "/api/v1/promotion/overview",
            params=dict(
                mode="store",
                date_from="2026-10-01",
                date_to="2026-10-02",
                shop_id="663599807",
                data_version=version,
            ),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["coverage"]["missing_count"] == 1
        for point in data["series"]:
            assert not point["missing"]
            assert Decimal(point["metrics"]["revenue"]["value"]) == Decimal("99.99")
            assert Decimal(point["previous"]["revenue"]["value"]) == Decimal("99.99")
        assert data["series"][1]["previous_date"] == "2025-10-02"
