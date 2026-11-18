"""仅本机人工登录和开发验收使用；不进入生产镜像或定时同步链路。"""

import fcntl
import json
import os
import re
import tempfile
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from app.modules.data_status.catalog import BY_ID, WAREHOUSE_PAGES
from app.modules.data_status.schedule_policy import WAREHOUSE_SOURCES

ORIGIN = "https://bi.kktree.cn"


class WarehousePageError(ValueError):
    """仅允许固定错误码通过作业边界，不携带页面、Cookie或浏览器异常原文。"""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def page_url(code):
    if code not in WAREHOUSE_SOURCES:
        raise WarehousePageError("WAREHOUSE_WEB_SCOPE")
    return ORIGIN + "/#/data_source?tableId=" + WAREHOUSE_PAGES[code][1]


def is_login(url):
    parts = urlsplit(url)
    return parts.hostname == "passport.feishu.cn" or (
        parts.hostname == "bi.kktree.cn" and "/login" in (parts.path + parts.fragment).lower()
    )


def parse_page(code, url, body):
    """输入可见正文及实际地址，返回已核表身份的网页时间；不把任务状态当齐数标记。"""
    page_url(code)
    if is_login(url):
        raise WarehousePageError("WAREHOUSE_WEB_LOGIN_REQUIRED")
    parts = urlsplit(url)
    fragment, _, query = parts.fragment.partition("?")
    if (
        parts.scheme != "https"
        or parts.netloc != "bi.kktree.cn"
        or fragment != "/data_source"
        or parse_qs(query).get("tableId") != [WAREHOUSE_PAGES[code][1]]
    ):
        raise WarehousePageError("WAREHOUSE_WEB_IDENTITY_MISMATCH")
    if not isinstance(body, str) or len(body) > 500000:
        raise WarehousePageError("WAREHOUSE_WEB_INVALID_PAGE")
    table = BY_ID[code].locator.removeprefix("cubeappdata.")
    if table not in {line.strip() for line in body.splitlines()}:
        raise WarehousePageError("WAREHOUSE_WEB_IDENTITY_MISMATCH")
    times = re.findall(r"(?m)^\s*数据更新时间[：:\t ]*(?:\n\s*)?([^\n]+)", body)
    if len(times) != 1:
        raise WarehousePageError("WAREHOUSE_WEB_TIME_INVALID")
    value = times[0].strip()
    try:
        valid = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S") == value
    except ValueError:
        valid = False
    if not valid:
        raise WarehousePageError("WAREHOUSE_WEB_TIME_INVALID")
    return dict(basis="warehouse_web_page", table_id=WAREHOUSE_PAGES[code][1], updated_at=value)


class WarehousePageReader:
    def __init__(self, state_path, *, channel="chrome"):
        self.state_path = state_path
        self.channel = channel
        self.context = self.page = self.browser = self.playwright = self.lock = None
        self.failure = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def start(self, *, login=False):
        """按需启动本轮浏览器；login仅供人工登录入口，不在调度中弹窗或自动提交身份。"""
        if os.environ.get("APP_ENV") == "production":
            raise WarehousePageError("WAREHOUSE_BROWSER_DISABLED_IN_PRODUCTION")
        if self.failure:
            raise WarehousePageError(self.failure)
        if self.context:
            return
        try:
            state = None
            if self.state_path.is_symlink():
                raise WarehousePageError("WAREHOUSE_WEB_STATE_PERMISSIONS")
            if self.state_path.exists():
                if self.state_path.stat().st_mode & 0o077:
                    raise WarehousePageError("WAREHOUSE_WEB_STATE_PERMISSIONS")
                if self.state_path.stat().st_size > 1048576:
                    raise WarehousePageError("WAREHOUSE_WEB_STATE_INVALID")
                state = json.loads(self.state_path.read_text())
                if not isinstance(state, dict) or not all(
                    isinstance(state.get(k), list) for k in ("cookies", "origins")
                ):
                    raise WarehousePageError("WAREHOUSE_WEB_STATE_INVALID")
            elif not login:
                raise WarehousePageError("WAREHOUSE_WEB_LOGIN_REQUIRED")
            self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.state_path.parent.stat().st_mode & 0o077:
                raise WarehousePageError("WAREHOUSE_WEB_STATE_PERMISSIONS")
            self.lock = os.fdopen(
                os.open(
                    self.state_path.with_suffix(".lock"),
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                    0o600,
                ),
                "w",
            )
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WarehousePageError("WAREHOUSE_WEB_IN_USE") from None
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(channel=self.channel, headless=not login)
            self.context = self.browser.new_context(storage_state=state, accept_downloads=False)
            self.context.set_default_timeout(15000)
            self.context.set_default_navigation_timeout(30000)
            self.page = self.context.new_page()
        except Exception as exc:
            code = exc.code if isinstance(exc, WarehousePageError) else "WAREHOUSE_WEB_START_FAILED"
            self.close()
            self.failure = code
            raise WarehousePageError(code) from None

    def save_state(self):
        """仅持久化专用浏览器中的数仓域状态；不保留飞书域身份、不记录密码或Cookie。"""
        raw = self.context.storage_state()
        state = {
            "cookies": [
                c
                for c in raw["cookies"]
                if c["domain"].lstrip(".") in ("bi.kktree.cn", "kktree.cn")
            ],
            "origins": [o for o in raw["origins"] if o["origin"] == ORIGIN],
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".warehouse-web-", dir=self.state_path.parent
        )
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump(state, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.state_path)
        finally:
            from pathlib import Path

            Path(temporary).unlink(missing_ok=True)

    def read(self, code):
        """固定清单串行打开基础信息，实际重新加载后读可见正文；登录失效本轮不再重试。"""
        url = page_url(code)
        self.start()
        try:
            # 表ID位于hash；清空旧文档后加载，不能把同文档切换当成新详情已就绪。
            self.page.goto("about:blank", wait_until="domcontentloaded")
            self.page.goto(url, wait_until="domcontentloaded")
            table = BY_ID[code].locator.removeprefix("cubeappdata.")
            self.page.wait_for_function(
                r"""table => location.href.toLowerCase().includes('/login')
                  || location.hostname === 'passport.feishu.cn'
                  || (document.body.innerText.split('\n').some(s => s.trim() === table)
                      && /数据更新时间\s*[:：]?\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/
                          .test(document.body.innerText))""",
                arg=table,
            )
            value = parse_page(code, self.page.url, self.page.locator("body").inner_text())
            self.save_state()
            return value
        except Exception as exc:
            code = (
                "WAREHOUSE_WEB_LOGIN_REQUIRED"
                if is_login(self.page.url)
                else exc.code
                if isinstance(exc, WarehousePageError)
                else "WAREHOUSE_WEB_READ_FAILED"
            )
            if code == "WAREHOUSE_WEB_LOGIN_REQUIRED":
                self.failure = code
            raise WarehousePageError(code) from None

    def close(self):
        """释放本轮所有浏览器进程及状态文件互斥，关闭失败不覆盖同步结果。"""
        for resource, method in (
            (self.context, "close"),
            (self.browser, "close"),
            (self.playwright, "stop"),
        ):
            if resource:
                try:
                    getattr(resource, method)()
                except Exception:
                    pass
        self.context = self.page = self.browser = self.playwright = None
        if self.lock:
            self.lock.close()
            self.lock = None
