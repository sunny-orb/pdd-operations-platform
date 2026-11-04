"""网页元数据只读取固定表身份；HTTP边界替身不代替真实接口核验。"""

import pytest


def test_metadata_validates_identity_and_preserves_web_time():
    from app.connectors.warehouse_metadata import parse_metadata

    body = {
        "success": True,
        "code": "200",
        "data": {
            "id": "cAc3IVT4i3",
            "tableName": "ods_rpa_pdd_traffic_kanban_overview_f",
            "dataLastUpdateTime": "2026-11-03 10:02:10",
        },
    }
    result = parse_metadata("shop_daily", body)
    assert result == {
        "basis": "warehouse_web_metadata",
        "table_id": "cAc3IVT4i3",
        "updated_at": "2026-11-03 10:02:10",
    }
    body["data"]["tableName"] = "other"
    with pytest.raises(ValueError, match="IDENTITY"):
        parse_metadata("shop_daily", body)


@pytest.mark.parametrize(
    "body", [{}, {"success": False, "code": "401"}, {"success": True, "code": "200", "data": None}]
)
def test_metadata_rejects_auth_failure_and_invalid_response(body):
    from app.connectors.warehouse_metadata import parse_metadata

    with pytest.raises(ValueError):
        parse_metadata("shop_daily", body)
