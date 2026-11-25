"""确定性全国天气解释；只认明确预报时段，保留两份原文及边界。"""

import re
from datetime import date, datetime, timedelta

from app.connectors.weather import BULLETIN_URL, MID_RANGE_URL, SOURCES, SourceDocument
from app.modules.weather.contracts import (
    WeatherBlock,
    WeatherDocument,
    WeatherProcess,
    WeatherSource,
)

PARSER_REVISION = "nmc-national-v3"
FOCUS_REGIONS = ["广东", "江苏", "山东", "浙江", "河南", "河北", "北京", "四川", "辽宁"]
REFERENCE_URL = "https://kocotree.feishu.cn/sheets/USG4sA8qjhYqDotULUQcH5uznJd?sheet=jvTHJw"
RAIN = re.compile(r"雨|雪|降水")
TEMPERATURE = re.compile(r"降温|升温|气温|最高温|最低温")
PERIOD = re.compile(
    r"(?:(\d{1,2})月)?(\d{1,2})日?(?:\d{1,2}时|白天|夜间|夜)?\s*"
    r"(?:至|到|[-—～~])\s*(?:(\d{1,2})月)?(\d{1,2})日(?:\d{1,2}时|白天|夜间|夜)?"
)


def _regions(text):
    return [region for region in FOCUS_REGIONS if region in text]


def _stamp(raw):
    try:
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None or value.utcoffset() != timedelta(hours=8):
            raise ValueError
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+08:00", raw):
            raise ValueError
        return value
    except ValueError as exc:
        raise ValueError("SOURCE_TIME_INVALID") from exc


def _period(text, issue):
    match = PERIOD.search(text)
    if not match:
        return None
    month, day, end_month, end_day = match.groups()
    try:
        first = date(issue.year, int(month or issue.month), int(day))
        # 没写月份且月底发布时，较小日数解释为下月；不任意回填历史。
        if not month and first < issue - timedelta(days=7):
            first = date(issue.year + (issue.month == 12), issue.month % 12 + 1, int(day))
        last_month = int(end_month or first.month)
        last_year = first.year + (last_month < first.month)
        last = date(last_year, last_month, int(end_day))
        if not end_month and last < first:
            last = date(first.year + (first.month == 12), first.month % 12 + 1, int(end_day))
        if last < first or (last - first).days > 14:
            return None
        return first, last, match.group()
    except ValueError:
        return None


def _sentences(text, pattern):
    return "".join(
        sentence + "。"
        for sentence in re.split(r"[。！？]", text)
        if sentence.strip() and pattern.search(sentence)
    )


def _block(text, pattern, label):
    found = _sentences(text, pattern)
    return WeatherBlock(
        regions=_regions(found),
        detail=found or f"未识别到明确{label}文本，待核原文。",
        status="parsed" if found else "unidentified",
    )


def _bulletin_forecasts(text: str) -> tuple[str | None, str | None]:
    """按新版/旧版完整章节标题取重点和三天预报；缺章节返回None，不混入实况。"""
    daily = re.split(r"(?m)^\s*三[、.．]\s*(?:国内)?未来三天具体预报", text, maxsplit=1)
    domestic = re.split(r"(?m)^\s*二[、.．]\s*全球天气情况", text, maxsplit=1)[0]
    key = re.split(r"(?m)^\s*(?:2[.．、]|二[、.．])\s*重点天气预报", domestic, maxsplit=1)
    key_text = re.split(r"(?m)^\s*三[、.．]", key[1], maxsplit=1)[0] if len(key) == 2 else None
    return key_text, daily[1] if len(daily) == 2 else None


def today_temperature(sources, issue):
    """从归档国内重点/逐日及中期过程取覆盖发布日的气温原句；不把未来或实况当今日。"""
    by_url = {s.url: s for s in sources}
    bulletin = by_url[BULLETIN_URL]
    key, daily = _bulletin_forecasts(bulletin.text)
    if daily is None or key is None:
        return WeatherBlock(
            regions=[], detail="本期气温预报暂无法读取，请查看原文。", status="unidentified"
        )
    lines = [(line, issue) for line in (key + "\n" + daily).splitlines()]
    medium = by_url[MID_RANGE_URL]
    part = re.split(r"主要天气过程[：:]", medium.text, maxsplit=1)
    if len(part) == 2:
        body = re.split(r"三[、.．]\s*未来", part[1], maxsplit=1)[0]
        lines += [(line, _stamp(medium.issue_time).date()) for line in body.splitlines()]
    found, unresolved = [], False
    for line, anchor in lines:
        if not TEMPERATURE.search(line) or re.match(r"^\s*[（(]?\d+[）)]|^\s*图", line):
            continue
        # 后续明确时段开启独立片段；未重写时段的后句沿用本段时段。
        starts = [match.start() for match in PERIOD.finditer(line)]
        boundaries = [0, *starts[1:], len(line)]
        for left, right in zip(boundaries, boundaries[1:], strict=False):
            section = line[left:right]
            if not TEMPERATURE.search(section):
                continue
            span = _period(section, anchor)
            if span is None:
                # 标题不作气温事实；包含数值但缺时段的正文不能宣称未提及。
                unresolved = unresolved or bool(re.search(r"\d|℃|今天|今日", section))
                continue
            start, end, label = span
            ends_at_morning = bool(re.search(r"日0?8时$", label))
            if start <= issue <= end and not (end == issue and ends_at_morning):
                sentence = _sentences(section, TEMPERATURE)
                if sentence not in found:
                    found.append(sentence)
    if found:
        detail = "".join(found)
        return WeatherBlock(regions=_regions(detail), detail=detail, status="parsed")
    if unresolved:
        return WeatherBlock(
            regions=[], detail="本期气温描述未明确当日时段，请查看原文。", status="unidentified"
        )
    return WeatherBlock(regions=[], detail="本期预报未提及今日气温变化。", status="not_mentioned")


def presentation_document(raw):
    """按当前定义解释已归档原文，不改写发布记录、不访问上游；版本含定义哈希。"""
    doc = WeatherDocument.model_validate(raw)
    _, interpreted = build_document(
        [SourceDocument(**source.model_dump()) for source in doc.sources]
    )
    doc.today_rain = interpreted.today_rain
    doc.today_temperature = interpreted.today_temperature
    doc.next_7_days = interpreted.next_7_days
    doc.note = doc.note.replace(
        "今日气温缺明确证据则待核。", "今日气温按国内当日预报识别；未提及不代表无天气风险。"
    )
    return doc.model_dump()


def build_document(sources: list[SourceDocument]) -> tuple[date, WeatherDocument]:
    """两份固定身份、真实时间/非空正文输入，返回公报发布日与不可变展示产物。"""
    if len(sources) != 2 or {s.url for s in sources} != set(SOURCES):
        raise ValueError("SOURCE_IDENTITY_INVALID")
    by_url = {s.url: s for s in sources}
    for source in sources:
        if source.name != SOURCES[source.url]:
            raise ValueError("SOURCE_IDENTITY_INVALID")
        _stamp(source.issue_time)
        if not source.text.strip():
            raise ValueError("SOURCE_BODY_EMPTY")
    bulletin, medium = by_url[BULLETIN_URL], by_url[MID_RANGE_URL]
    issue = _stamp(bulletin.issue_time).date()
    # 降水只从国内未来三天读取；排除前置实况、全球及先出现的大风预计。
    _, daily_text = _bulletin_forecasts(bulletin.text)
    daily = daily_text.splitlines() if daily_text is not None else []
    current = next((line for line in daily if (p := _period(line, issue)) and p[0] == issue), "")
    rain = _block(current, RAIN, "今日降水")
    temperature = today_temperature(sources, issue)
    processes = []
    medium_part = re.split(r"主要天气过程[：:]", medium.text, maxsplit=1)
    medium_lines = (
        re.split(r"三[、.．]\s*未来", medium_part[1], maxsplit=1)[0].splitlines()
        if len(medium_part) == 2
        else []
    )
    seen = set()
    for line, anchor in [
        *((line, issue) for line in daily),
        *((line, _stamp(medium.issue_time).date()) for line in medium_lines),
    ]:
        if re.match(r"^\s*图\s*\d", line):
            continue
        span = _period(line, anchor)
        if not span or not (RAIN.search(line) or TEMPERATURE.search(line)):
            continue
        start, end, label = span
        if start > issue + timedelta(days=6) or end < issue or line in seen:
            continue
        seen.add(line)
        boundary = start < issue or end > issue + timedelta(days=6)
        processes.append(
            WeatherProcess(
                period=label,
                regions=_regions(line),
                detail=line,
                status="boundary" if boundary else "parsed",
            )
        )
    if not processes:
        processes.append(
            WeatherProcess(
                period=f"{issue}至{issue + timedelta(days=6)}",
                regions=[],
                status="unidentified",
                detail="未识别到明确的未来7日过程，待核原文；不代表无天气风险。",
            )
        )
    return issue, WeatherDocument(
        title="全国关键天气",
        issue_time=bulletin.issue_time,
        focus_regions=FOCUS_REGIONS,
        today_rain=rain,
        today_temperature=temperature,
        next_7_days=processes,
        sources=[WeatherSource(**vars(by_url[url])) for url in SOURCES],
        forecast_url=BULLETIN_URL,
        reference_url=REFERENCE_URL,
        group_card_link=None,
        note="唯一生效来源为NMC两份互补官文，各自发布时间独立。未来7日为发布日起至+6日；"
        "过程原句跨窗口时保留边界标记，不等于逐日预报。今日气温按国内当日预报识别；未提及不代表无天气风险。"
        "当前官方预报链接随官网更新，飞书仅供参考；无文本命中不表示无天气风险。",
    )
