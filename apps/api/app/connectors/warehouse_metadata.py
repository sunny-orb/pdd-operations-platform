"""数仓网页表详情的只读元数据接口；后台身份由触发适配器显式注入。"""

import json
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.modules.data_status.catalog import BY_ID, WAREHOUSE_PAGES

ENDPOINT = "https://bi.kktree.cn/api/worktable/loadWorkTableDetail"


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

    def read(self, code):
        """固定同源POST为空体，只读目录元信息；不把认证内容/原始响应写入日志。"""
        if code not in WAREHOUSE_PAGES:
            raise ValueError("WAREHOUSE_METADATA_SCOPE")
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
                return parse_metadata(code, json.loads(raw))
        except HTTPError as error:
            raise ValueError(
                "WAREHOUSE_METADATA_AUTH_FAILED"
                if error.code in (401, 403)
                else "WAREHOUSE_METADATA_HTTP_FAILED"
            ) from None
        except Exception:
            raise ValueError("WAREHOUSE_METADATA_READ_FAILED") from None
