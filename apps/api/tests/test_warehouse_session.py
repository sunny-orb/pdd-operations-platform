"""账号登录与会话边界；协议替身不代替真实账号验收。"""

import json
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

ORIGIN = "https://bi.kktree.cn"


class Protocol(HTTPSHandler):
    def __init__(self, *, reject_login=False, expire=False, captcha=False, password_status=False):
        super().__init__()
        self.calls = []
        self.reject_login, self.expire, self.captcha = reject_login, expire, captcha
        self.logins = 0
        self.password_status = password_status

    def https_open(self, request):
        self.calls.append(request)
        headers = Message()
        body = {"success": True, "code": "200", "data": {}}
        if request.full_url.endswith("/configApi/loadConfig"):
            body["data"] = {
                "loginVerifyEnable": self.captcha,
                "loginConfig": [{"loginType": "ACCOUNT_LOGIN", "loginTypeStatus": 1}],
            }
        elif request.full_url.endswith("/rest/v2/unsecure/login"):
            self.logins += 1
            if self.reject_login:
                raise HTTPError(request.full_url, 401, "sensitive server detail", headers, None)
            fields = parse_qs(request.data.decode())
            assert fields["username"] == ["test-user"]
            assert "test-password" not in request.data.decode()
            assert fields["verifyToken"]
            headers["Set-Cookie"] = f"login=test-session-{self.logins}; Path=/; Secure; HttpOnly"
        elif request.full_url.endswith("/user/checkPasswordStatus"):
            assert request.get_header("Cookie") == f"login=test-session-{self.logins}"
            body["data"] = self.password_status
        else:
            assert request.get_header("Cookie") == f"login=test-session-{self.logins}"
            if self.expire:
                self.expire = False
                raise HTTPError(request.full_url, 401, "expired", headers, None)
            body["data"] = {
                "id": "cAc3IVT4i3",
                "tableName": "ods_rpa_pdd_traffic_kanban_overview_f",
                "dataLastUpdateTime": "2026-11-03 10:02:10",
            }
        response = addinfourl(BytesIO(json.dumps(body).encode()), headers, request.full_url, 200)
        response.msg = "OK"
        return response


def session(protocol):
    from app.connectors.warehouse_session import WarehouseAccountSession

    return WarehouseAccountSession(
        lambda: {"username": "test-user", "password": "test-password"}, transport=protocol
    )


def test_login_cookie_reused_for_all_metadata_reads():
    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    protocol = Protocol()
    reader = WarehouseMetadataReader(session=session(protocol))
    assert reader.read("shop_daily")["updated_at"] == "2026-11-03 10:02:10"
    reader.read("shop_daily")
    assert protocol.logins == 1


def test_expired_cookie_reauthenticates_once():
    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    protocol = Protocol(expire=True)
    WarehouseMetadataReader(session=session(protocol)).read("shop_daily")
    assert protocol.logins == 2


def test_bad_login_is_not_repeated_for_each_source_and_hides_details():
    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    protocol = Protocol(reject_login=True)
    reader = WarehouseMetadataReader(session=session(protocol))
    for _ in range(3):
        with pytest.raises(ValueError) as error:
            reader.read("shop_daily")
        assert "sensitive" not in str(error.value)
        assert "test-password" not in str(error.value)
    assert protocol.logins == 1


def test_captcha_requires_intervention_without_sending_password():
    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    protocol = Protocol(captcha=True)
    with pytest.raises(ValueError):
        WarehouseMetadataReader(session=session(protocol)).read("shop_daily")
    assert protocol.logins == 0


@pytest.mark.parametrize("password_status", [True, None, "false", {}])
def test_required_password_change_or_unknown_status_stops_metadata(password_status):
    from app.connectors.warehouse_metadata import WarehouseMetadataReader

    protocol = Protocol(password_status=password_status)
    with pytest.raises(ValueError):
        WarehouseMetadataReader(session=session(protocol)).read("shop_daily")
    assert not any("loadWorkTableDetail" in request.full_url for request in protocol.calls)


def test_session_rejects_other_hosts_and_paths_before_loading_credentials():
    from urllib.request import Request

    from app.connectors.warehouse_session import WarehouseAccountSession

    def forbidden():
        pytest.fail("不允许的目标不得读取凭据")

    auth = WarehouseAccountSession(forbidden)
    for target in ("https://example.com/", ORIGIN + "/api/admin"):
        with pytest.raises(ValueError, match="SCOPE"):
            auth.open(Request(target), timeout=15)


def test_password_encoding_matches_published_browser_protocol():
    from base64 import b64decode

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    from app.connectors.warehouse_session import encode_password

    decrypt = Cipher(algorithms.AES(b"bSRRwjoAoGW2dX6w"), modes.ECB()).decryptor()
    padded = decrypt.update(b64decode(encode_password("测试密码"))) + decrypt.finalize()
    unpad = PKCS7(128).unpadder()
    assert (unpad.update(padded) + unpad.finalize()).decode() == "测试密码"


def test_private_account_file_permissions_and_safe_errors(tmp_path):
    from app.connectors.warehouse_session import load_account_file

    path = tmp_path / "account.json"
    path.write_text('{"username":"test-user","password":"test-password"}')
    path.chmod(0o644)
    with pytest.raises(ValueError, match="PERMISSIONS"):
        load_account_file(path)
    path.chmod(0o600)
    assert load_account_file(path)["username"] == "test-user"
    path.write_text("test-password invalid json")
    with pytest.raises(ValueError, match="INVALID") as error:
        load_account_file(path)
    assert "test-password" not in str(error.value)
