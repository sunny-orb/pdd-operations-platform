"""数仓网页表详情的只读元数据接口；后台身份由触发适配器显式注入。"""

import json
import math
import os
import stat
import time
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.modules.data_status.catalog import BY_ID, WAREHOUSE_PAGES

ENDPOINT = "https://bi.kktree.cn/api/worktable/loadWorkTableDetail"


class WarehouseMetadataError(ValueError):
    """固定错误码供调度展示；不得携带会话或响应正文。"""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def session_headers(path):
    """只读项目专用登录文件的数仓会话；不启动浏览器、不读个人配置或改写文件。"""
    try:
        if path.parent.is_symlink():
            raise ValueError
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW)) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1048576:
                raise ValueError
            state = json.loads(source.read(1048577))
        cookies = [
            cookie
            for cookie in state["cookies"]
            if isinstance(cookie, dict)
            and cookie.get("name") == "YCSESSIONID"
            and cookie.get("domain") == "bi.kktree.cn"
        ]
        if len(cookies) != 1:
            raise ValueError
        cookie = cookies[0]
        value, expires = cookie.get("value"), cookie.get("expires")
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 8192
            or any(ord(c) < 33 or ord(c) > 126 or c in ';,"\\' for c in value)
            or not isinstance(expires, (float, int))
            or not math.isfinite(expires)
            or (expires != -1 and expires <= time.time())
            or cookie.get("path") != "/"
        ):
            raise ValueError
        return {"Cookie": "YCSESSIONID=" + value}
    except (OSError, ValueError, TypeError, KeyError):
        raise WarehouseMetadataError("WAREHOUSE_WEB_LOGIN_REQUIRED") from None


def configured_warehouse_reader(root):
    """沿用已有项目专用会话位置，免重新登录和复制秘密；每次请求只读更新时间接口。"""
    return WarehouseMetadataReader(
        lambda: session_headers(root / "var/local-db/warehouse-browser/state.json")
    )


def parse_metadata(code, body):
    """只接受成功响应、固定表ID/物理表及合法原样时间，不推断时区或业务齐数。"""
    data = body.get("data") if isinstance(body, dict) else None
    if (
        not isinstance(data, dict)
        or body.get("success") is not True
        or str(body.get("code")) != "200"
    ):
        raise ValueError("WAREHOUSE_METADATA_INVALID_RESPONSE")
    if (
        code not in WAREHOUSE_PAGES
        or data.get("id") != WAREHOUSE_PAGES[code][1]
        or data.get("tableName") != BY_ID[code].locator.removeprefix("cubeappdata.")
    ):
        raise ValueError("WAREHOUSE_METADATA_IDENTITY_MISMATCH")
    value = data.get("dataLastUpdateTime")
    try:
        valid = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S") == value
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("WAREHOUSE_METADATA_TIME_INVALID")
    return {"basis": "warehouse_web_metadata", "table_id": data["id"], "updated_at": value}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WarehouseMetadataReader:
    def __init__(self, headers=None, *, session=None):
        self.headers = headers
        self.session = session
        self.opener = session or build_opener(NoRedirect())
        self.failure = None

    def read(self, code):
        """固定同源POST为空体，只读目录元信息；不把认证内容/原始响应写入日志。"""
        if code not in WAREHOUSE_PAGES:
            raise ValueError("WAREHOUSE_METADATA_SCOPE")
        if self.failure:
            raise WarehouseMetadataError(self.failure)
        try:
            headers = {} if self.session is not None else self.headers()
            if not isinstance(headers, dict) or (not headers and self.session is None):
                raise ValueError("WAREHOUSE_METADATA_AUTH_REQUIRED")
            request = Request(
                ENDPOINT + "?tableId=" + WAREHOUSE_PAGES[code][1],
                data=b"",
                method="POST",
                headers={
                    **headers,
                    "Content-Type": "application/json;charset=UTF-8",
                    "Accept": "application/json",
                },
            )
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(131073)
                if len(raw) > 131072:
                    raise ValueError("WAREHOUSE_METADATA_RESPONSE_LIMIT")
                body = json.loads(raw)
                if isinstance(body, dict) and str(body.get("code")) in ("401", "403"):
                    raise WarehouseMetadataError("WAREHOUSE_WEB_LOGIN_REQUIRED")
                return parse_metadata(code, body)
        except HTTPError as error:
            if error.code in (401, 403, 301, 302, 303, 307, 308):
                self.failure = "WAREHOUSE_WEB_LOGIN_REQUIRED"
            raise WarehouseMetadataError(self.failure or "WAREHOUSE_METADATA_HTTP_FAILED") from None
        except WarehouseMetadataError as error:
            if error.code == "WAREHOUSE_WEB_LOGIN_REQUIRED":
                self.failure = error.code
            raise
        except Exception:
            raise ValueError("WAREHOUSE_METADATA_READ_FAILED") from None
