"""所有合成历史报告修订与五页已有查询，离线前后逐字段一致。"""

import os
from datetime import date

from app.modules.database_archive import drill, export
from app.modules.database_retention import apply, plan, restore
from tests.test_aftersales_api import client_for, publish
from tests.test_database_archive import old_candidate
from tests.test_promotion_api import publish_products

pytest_plugins = ["tests.sales_pg_support"]


def test_current_pages_and_every_historical_report_survive(sales_pg, tmp_path):
    old_candidate(sales_pg)
    publish_products(sales_pg, tmp_path / "inputs")
    from app.modules.sales.contracts import ReadWindow
    from app.modules.sales.normalization import normalize_shop_daily
    from app.modules.sales.source_policy import SHOPS
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_source import raw_row

    window = ReadWindow(date(2026, 10, 25), date(2026, 10, 27), tuple(SHOPS))
    ShopDailyStore(sales_pg, archive_root=tmp_path / "sales", approved=window).publish_candidate(
        normalize_shop_daily([raw_row()], window)
    )
    publish(sales_pg, "prev", start=date(2026, 8, 29), end=date(2026, 9, 27))
    publish(sales_pg, "yoy", start=date(2025, 9, 28), end=date(2025, 10, 27))
    for suffix in ("1", "2"):
        publish(
            sales_pg,
            suffix,
            revision_no=int(suffix),
            previous_report_id="reportprev",
            yoy_report_id="reportyoy",
        )
    urls = [
        "/api/v1/products/meta",
        "/api/v1/products/overview?date_from=2026-10-26&date_to=2026-10-26",
        "/api/v1/products/days?date_from=2026-10-26&date_to=2026-10-26",
        "/api/v1/products/options?date_from=2026-10-26&date_to=2026-10-26",
        "/api/v1/promotion/meta?mode=product",
        "/api/v1/promotion/overview?mode=product&date_from=2026-10-27&date_to=2026-10-27",
        "/api/v1/sales/filters",
        "/api/v1/sales/performance?date_from=2026-10-25&date_to=2026-10-27",
        "/api/v1/sales/shops?date_from=2026-10-25&date_to=2026-10-27",
        "/api/v1/published-versions",
        "/api/v1/aftersales/options",
    ]
    for report in ("report1", "report2", "reportprev", "reportyoy"):
        for endpoint in (
            "meta",
            "overview",
            "options",
            "analysis?view=category",
            "analysis?view=style",
            "analysis?view=quality",
            "analysis?view=category&focus=size",
            "analysis?view=mom",
            "analysis?view=yoy",
            "analysis?view=history",
        ):
            join = "&" if "?" in endpoint else "?"
            urls.append("/api/v1/aftersales/" + endpoint + join + "report_id=" + report)

    def read(client):
        data = {}
        tokens = {
            domain: client.get(
                "/api/v1/" + domain + "/meta" + ("?mode=product" if domain == "promotion" else "")
            ).json()["meta"]["data_version"]
            for domain in ("products", "promotion")
        }
        tokens["sales"] = client.get("/api/v1/sales/filters").json()["meta"]["data_version"]
        for url in urls:
            request_url = url
            for domain, token in tokens.items():
                if "/" + domain + "/" in url and "/meta" not in url and "/filters" not in url:
                    request_url += ("&" if "?" in url else "?") + "data_version=" + token
            response = client.get(request_url)
            assert response.status_code == 200, (url, response.json())
            body = response.json()
            if "meta" in body:
                body["meta"].pop("request_id", None)
            data[url] = body
        return data

    with client_for(sales_pg) as client:
        before = read(client)
        assert before[urls[7]]["data"]["shop_revenue"] == "10.20"
        selected = plan(sales_pg)
        assert [i["version_id"] for i in selected["candidates"]] == ["product_daily-a"]
        key = os.urandom(32)
        bundle = export(sales_pg, selected, tmp_path / "api-roundtrip", key)
        drill(bundle, key)
        apply(sales_pg, selected, bundle, key)
        assert read(client) == before
        restore(sales_pg, bundle, key)
        assert read(client) == before
