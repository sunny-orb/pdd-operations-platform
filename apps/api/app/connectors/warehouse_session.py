"""现有数仓网页登录；会话仅驻留本轮进程，调度器不拥有认证规则。"""

import base64
import json
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.connectors.warehouse_metadata import ENDPOINT, NoRedirect

ORIGIN = "https://bi.kktree.cn"


def load_account_file(path):
    """读取调用方指定的0600私密文件；不扫描浏览器/系统凭据，不返回解析原文。"""
    if not path.is_file():
        raise ValueError("WAREHOUSE_AUTH_REQUIRED")
    if path.stat().st_mode & 0o077:
        raise ValueError("WAREHOUSE_AUTH_FILE_PERMISSIONS")
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or any(
            not isinstance(value.get(key), str) or not value[key]
            for key in ("username", "password")
        ):
            raise ValueError
        return {key: value[key] for key in ("username", "password")}
    except Exception:
        raise ValueError("WAREHOUSE_AUTH_FILE_INVALID") from None


def encode_password(password):
    """匹配站点已发布的AES表单协议；该公开编码键不是保密措施，传输必须HTTPS。"""
    padding = PKCS7(128).padder()
    raw = padding.update(password.encode()) + padding.finalize()
    encrypt = Cipher(algorithms.AES(b"bSRRwjoAoGW2dX6w"), modes.ECB()).encryptor()
    return base64.b64encode(encrypt.update(raw) + encrypt.finalize()).decode()


class WarehouseAccountSession:
    def __init__(self, credentials, *, transport=None):
        self.credentials = credentials
        self.cookies = CookieJar()
        handlers = [NoRedirect(), HTTPCookieProcessor(self.cookies)]
        if transport is not None:
            handlers.append(transport)
        self.opener = build_opener(*handlers)
        self.authenticated = False
        self.login_attempts = 0
        self.failure = None

    def _json(self, request):
        with self.opener.open(request, timeout=15) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError("WAREHOUSE_AUTH_RESPONSE_LIMIT")
        result = json.loads(raw)
        if (
            not isinstance(result, dict)
            or result.get("success") is not True
            or str(result.get("code")) != "200"
        ):
            raise ValueError("WAREHOUSE_AUTH_REJECTED")
        return result.get("data")

    def _login(self):
        """每轮最多初次登录加一次失效续登；失败立即停止本轮后续账号尝试。"""
        if self.failure:
            raise ValueError(self.failure)
        if self.login_attempts >= 2:
            raise ValueError("WAREHOUSE_AUTH_RETRY_LIMIT")
        self.login_attempts += 1
        self.cookies.clear()
        self.authenticated = False
        try:
            config = self._json(Request(ORIGIN + "/api/configApi/loadConfig"))
            if not isinstance(config, dict) or config.get("loginVerifyEnable") is not False:
                raise ValueError("WAREHOUSE_AUTH_INTERACTION_REQUIRED")
            if not any(
                method.get("loginType") == "ACCOUNT_LOGIN" and method.get("loginTypeStatus") == 1
                for method in config.get("loginConfig", [])
                if isinstance(method, dict)
            ):
                raise ValueError("WAREHOUSE_ACCOUNT_LOGIN_DISABLED")
            credentials = self.credentials()
            if not isinstance(credentials, dict) or any(
                not isinstance(credentials.get(key), str) or not credentials[key]
                for key in ("username", "password")
            ):
                raise ValueError("WAREHOUSE_AUTH_REQUIRED")
            fields = {
                "username": credentials["username"],
                "password": encode_password(credentials["password"]),
                "checkCode": "",
                "verifyToken": str(uuid4()),
            }
            self._json(
                Request(
                    ORIGIN + "/api/rest/v2/unsecure/login",
                    data=urlencode(fields).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                    method="POST",
                )
            )
            if not any(cookie for cookie in self.cookies if not cookie.is_expired()):
                raise ValueError("WAREHOUSE_AUTH_SESSION_MISSING")
            password_status = self._json(
                Request(
                    ORIGIN + "/api/user/checkPasswordStatus",
                    data=b"{}",
                    headers={"Content-Type": "application/json;charset=UTF-8"},
                    method="POST",
                )
            )
            if password_status is not False:
                raise ValueError("WAREHOUSE_AUTH_PASSWORD_ACTION_REQUIRED")
            self.authenticated = True
        except Exception:
            self.failure = "WAREHOUSE_AUTH_LOGIN_FAILED"
            raise ValueError(self.failure) from None

    def open(self, request, *, timeout):
        """只为已核元数据端点携带会话，不允许转用到其他主机或业务接口。"""
        url = urlsplit(request.full_url)
        if (
            url.scheme != "https"
            or url.netloc != "bi.kktree.cn"
            or url.path != urlsplit(ENDPOINT).path
        ):
            raise ValueError("WAREHOUSE_AUTH_SCOPE")
        if self.failure:
            raise ValueError(self.failure)
        if not self.authenticated:
            self._login()
        try:
            return self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            if error.code != 401 or self.login_attempts >= 2:
                raise
            self._login()
            # urllib在原Request中缓存Cookie；续登后必须使用新Cookie。
            request.remove_header("Cookie")
            return self.opener.open(request, timeout=timeout)
