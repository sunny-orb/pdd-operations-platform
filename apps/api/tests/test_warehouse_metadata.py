"""网页元数据只读取固定表身份；HTTP边界替身不代替真实接口核验。"""

import pytest


@pytest.mark.parametrize("secure", [True, False])
def test_dedicated_session_uses_only_valid_warehouse_cookie(tmp_path, secure):
    import json
    import time

    from app.connectors.warehouse_metadata import session_headers

    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "YCSESSIONID",
                        "value": "project-session",
                        "domain": "bi.kktree.cn",
                        "path": "/",
                        "expires": time.time() + 3600,
                        "secure": secure,
                    },
                    {"name": "analytics", "value": "excluded", "domain": "bi.kktree.cn"},
                    {"name": "YCSESSIONID", "value": "other", "domain": "passport.feishu.cn"},
                ],
                "origins": [{"origin": "https://bi.kktree.cn", "localStorage": []}],
            }
        )
    )
    path.chmod(0o600)
    before = path.read_bytes()
    assert session_headers(path) == {"Cookie": "YCSESSIONID=project-session"}
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "case", ["missing", "public", "symlink", "expired", "duplicate", "newline"]
)
def test_invalid_session_fails_without_exposing_credentials(tmp_path, case):
    import json
    import time

    from app.connectors.warehouse_metadata import session_headers

    path = tmp_path / "state.json"
    cookie = {
        "name": "YCSESSIONID",
        "value": "secret-token",
        "domain": "bi.kktree.cn",
        "path": "/",
        "expires": time.time() + 3600,
        "secure": True,
    }
    if case == "expired":
        cookie["expires"] = time.time() - 1
    if case == "newline":
        cookie["value"] = "secret-token\r\nInjected: header"
    path.write_text(json.dumps({"cookies": [cookie] * (2 if case == "duplicate" else 1)}))
    path.chmod(0o644 if case == "public" else 0o600)
    if case == "missing":
        path.unlink()
    if case == "symlink":
        target = tmp_path / "original.json"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(ValueError) as error:
        session_headers(path)
    assert "secret-token" not in str(error.value)


def test_metadata_auth_expiry_is_not_repeated_for_each_table(tmp_path):
    from urllib.error import HTTPError
    from urllib.request import HTTPSHandler

    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    class Expired(HTTPSHandler):
        calls = 0

        def https_open(self, request):
            self.calls += 1
            raise HTTPError(request.full_url, 401, "private-token", {}, None)

    from urllib.request import build_opener

    transport = Expired()
    reader = WarehouseMetadataReader(lambda: {"Cookie": "YCSESSIONID=example"})
    reader.opener = build_opener(transport)
    for code in ("shop_daily", "product_daily"):
        with pytest.raises(ValueError, match="WAREHOUSE_WEB_LOGIN_REQUIRED"):
            reader.read(code)
    assert transport.calls == 1


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
