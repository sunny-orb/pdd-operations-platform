"""NMC两份全国发布文档的有限只读接收；不访问城市、飞书或群消息。"""

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BULLETIN_URL = "https://www.nmc.cn/publish/weather-bulletin/index.htm"
MID_RANGE_URL = "https://www.nmc.cn/publish/bulletin/mid-range.htm"
SOURCES = {BULLETIN_URL: "天气公报", MID_RANGE_URL: "中期预报"}


@dataclass(frozen=True)
class SourceDocument:
    name: str
    url: str
    issue_time: str
    text: str


class _Page(HTMLParser):
    """只保留作者栏发布时间和writing正文，避免导航/页脚成为天气。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.author, self.body = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {"img", "br", "hr", "meta", "link", "input", "wbr"}:
            if tag == "br" and "writing" in self.stack:
                self.body.append("\n")
            return
        classes = dict(attrs).get("class", "").split()
        self.stack.append(
            "writing"
            if "writing" in classes
            else "author"
            if "author" in classes
            else "skip"
            if tag in ("script", "style")
            else tag
        )
        if tag in ("p", "div") and "writing" in self.stack:
            self.body.append("\n")

    def handle_endtag(self, tag):
        if tag in {"img", "br", "hr", "meta", "link", "input", "wbr"}:
            return
        if tag in ("p", "div") and "writing" in self.stack:
            self.body.append("\n")
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if "skip" in self.stack:
            return
        if "author" in self.stack:
            self.author.append(data)
        if "writing" in self.stack:
            self.body.append(data)


def parse_page(url: str, html: str) -> SourceDocument:
    """HTML输入仅允许固定官文；没有精确发布时间/正文则失败，不补今日日期。"""
    if url not in SOURCES:
        raise ValueError("SOURCE_IDENTITY_INVALID")
    page = _Page()
    page.feed(html)
    raw = "".join(page.author)
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*时", raw)
    if not match:
        raise ValueError("SOURCE_TIME_INVALID")
    try:
        stamp = datetime(*map(int, match.groups()), tzinfo=ZoneInfo("Asia/Shanghai"))
    except ValueError as exc:
        raise ValueError("SOURCE_TIME_INVALID") from exc
    body = "\n".join(
        text
        for line in "".join(page.body).splitlines()
        if (text := re.sub(r"\s+", " ", line).strip())
    )
    if not body:
        raise ValueError("SOURCE_BODY_EMPTY")
    return SourceDocument(SOURCES[url], url, stamp.isoformat(), body)


def receive_weather() -> list[SourceDocument]:
    """串行、限时、限体积读取两份官文；任一失败则整体不发布。"""
    result = []
    for url in SOURCES:
        try:
            request = Request(url, headers={"User-Agent": "pdd-operations-weather/1.0"})
            with urlopen(request, timeout=20) as response:
                if response.geturl() != url:
                    raise ValueError("SOURCE_REDIRECT_REJECTED")
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("SOURCE_TOO_LARGE")
            result.append(parse_page(url, raw.decode("utf-8")))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("SOURCE_RECEIVE_FAILED") from exc
    return result
