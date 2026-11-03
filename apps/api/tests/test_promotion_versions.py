"""共享资料或指标规则变化后，旧筛选版本必须失效，不能沿用旧候选。"""

from pathlib import Path

import pytest

from app.modules.promotion.repository import definition_revision
from tests.test_promotion_api import client_for, publish_products

pytest_plugins = ["tests.sales_pg_support"]


def change_dependency(monkeypatch, suffix):
    original = Path.read_bytes

    def read(path):
        raw = original(path)
        return raw + b"\n# simulated dependency revision\n" if str(path).endswith(suffix) else raw

    monkeypatch.setattr(Path, "read_bytes", read)


@pytest.mark.parametrize(
    "dependency",
    [
        "product_board/profile_rules.py",
        "product_board/filter_projection.py",
        "product_board/metrics.py",
        "product_board/repository.py",
        "sales/source_policy.py",
    ],
)
def test_promotion_version_tracks_shared_query_dependencies(monkeypatch, dependency):
    before = definition_revision()
    change_dependency(monkeypatch, dependency)
    assert definition_revision() != before


def test_shared_profile_change_invalidates_old_requests_without_changing_facts(
    sales_pg, tmp_path, monkeypatch
):
    publish_products(sales_pg, tmp_path)
    with client_for(sales_pg) as client:
        old = client.get("/api/v1/promotion/meta?mode=product").json()["meta"]["data_version"]
        scope = dict(mode="product", date_from="2026-10-27", date_to="2026-10-27")
        baseline = {
            path: client.get(
                f"/api/v1/promotion/{path}", params={**scope, "data_version": old}
            ).json()["data"]
            for path in ("options", "overview", "days", "diagnosis")
        }
        change_dependency(monkeypatch, "product_board/profile_rules.py")
        new = client.get("/api/v1/promotion/meta?mode=product").json()["meta"]["data_version"]
        assert new != old
        for path, expected in baseline.items():
            stale = client.get(
                f"/api/v1/promotion/{path}", params={**scope, "data_version": old}
            )
            assert stale.status_code == 409
            fresh = client.get(
                f"/api/v1/promotion/{path}", params={**scope, "data_version": new}
            )
            assert fresh.status_code == 200
            assert fresh.json()["data"] == expected
