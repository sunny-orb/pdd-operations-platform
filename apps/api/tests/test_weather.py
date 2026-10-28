"""全国发布文本反例；不把大风、实况或越界过程冒充今日预报。"""

from datetime import date

import pytest


def sources(day="28", extra=""):
    from app.connectors.weather import BULLETIN_URL, MID_RANGE_URL, SourceDocument

    return [
        SourceDocument(
            name="天气公报",
            url=BULLETIN_URL,
            issue_time=f"2026-10-{day}T08:00:00+08:00",
            text=(
                "一、国内天气情况\n1.实况\n昨天河北气温下降8℃。\n"
                "2.重点天气预报\n预计，28日08时至29日08时，河北有5级大风。\n"
                "二、全球天气情况\n广东昨日有大雨。\n三、国内未来三天具体预报\n"
                f"10月{day}日08时至10月{int(day) + 1}日08时，江苏、浙江有中到大雨。"
                "河北有5级风。\n10月29日08时至10月30日08时，四川有小雨。" + extra
            ),
        ),
        SourceDocument(
            name="中期预报",
            url=MID_RANGE_URL,
            issue_time="2026-10-27T10:00:00+08:00",
            text=(
                "一、过去10天天气\n山东气温下降。\n二、未来10天天气趋势\n"
                "主要天气过程：\n10月31日至11月2日，河南有降雨和降温。\n"
                "11月10日至12日，北京有降温。\n三、未来11-14天天气展望\n"
                "11月10日至12日，辽宁有降温。"
            ),
        ),
    ]


def test_domestic_forecast_excludes_wind_observation_and_global_weather():
    from app.modules.weather.parser import build_document

    day, doc = build_document(sources())
    assert day == date(2026, 10, 28)
    assert doc.today_rain.regions == ["江苏", "浙江"]
    assert "大风" not in doc.today_rain.detail
    assert "河北" not in doc.today_rain.regions
    assert doc.today_temperature.status == "unidentified"
    assert all("11月10日" not in item.detail for item in doc.next_7_days)
    assert any(item.regions == ["河南"] for item in doc.next_7_days)
    assert doc.group_card_link is None
    assert doc.sources[1].issue_time == "2026-10-27T10:00:00+08:00"


def test_invalid_source_time_or_empty_body_never_fabricates_today():
    from app.connectors.weather import SourceDocument
    from app.modules.weather.parser import build_document

    for bad in ["", "2026-10-28", "2026-10-28T08:00:00"]:
        values = sources()
        values[0] = SourceDocument("天气公报", values[0].url, bad, "原文")
        with pytest.raises(ValueError, match="SOURCE_TIME_INVALID"):
            build_document(values)
    values = sources()
    values[0] = SourceDocument("天气公报", values[0].url, values[0].issue_time, " ")
    with pytest.raises(ValueError, match="SOURCE_BODY_EMPTY"):
        build_document(values)


def test_html_only_extracts_author_time_and_writing_body():
    from app.connectors.weather import BULLETIN_URL, parse_page

    value = parse_page(
        BULLETIN_URL,
        "<div>导航江苏大雨</div><div class=author>制作：甲 "
        "<b>2026</b>年10月28日08时</div><div class=writing>"
        "<p>三、国内未来三天具体预报</p><p>江苏有雨。<img src=x></p></div>"
        "<div>页脚河北降温<script>错误文本</script></div>",
    )
    assert value.issue_time == "2026-10-28T08:00:00+08:00"
    assert value.text == "三、国内未来三天具体预报\n江苏有雨。"
    with pytest.raises(ValueError, match="SOURCE_BODY_EMPTY"):
        parse_page(BULLETIN_URL, "<div class=author>2026年10月28日08时</div>")


def test_figure_caption_does_not_become_a_forecast_process():
    from app.modules.weather.parser import build_document

    _, doc = build_document(sources(extra="\n图1 全国降水量预报图（10月28日08时-29日08时）"))
    assert all(not p.detail.startswith("图1") for p in doc.next_7_days)


def test_medium_relative_dates_use_its_own_publication_month():
    from app.connectors.weather import SourceDocument
    from app.modules.weather.parser import build_document

    values = sources()
    medium = values[1]
    values[1] = SourceDocument(
        medium.name,
        medium.url,
        "2026-09-27T10:00:00+08:00",
        "二、未来10天天气趋势\n主要天气过程：\n29日至30日，广东有大雨。",
    )
    _, doc = build_document(values)
    assert all("广东有大雨" not in item.detail for item in doc.next_7_days)
