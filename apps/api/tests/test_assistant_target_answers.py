"""运营询问确定口径时，直接交付可追溯答案；分析和复合问题继续交给 Agent。"""

import asyncio

import pytest

from app.api.assistant import PriorTurn
from app.modules.assistant.chat import ChatScope, QueryRun, answer_question


def test_original_shop_goal_followup_needs_no_model_or_business_database():
    question = "只看刚才这家，它这个月的GMV目标是多少、完成了几成？"
    run = QueryRun(None, ChatScope(domain="sales"), question)
    history = [PriorTurn(page="店铺销售", question="哪家店贡献最高？", answer="旗舰店。")]
    result = asyncio.run(answer_question(None, question, "店铺销售", history, run))
    assert result["status"] == "answered"
    assert result["usage"]["requests"] == result["usage"]["tool_calls"] == 0
    assert len(result["reports"]) == 1
    row = result["reports"][0]["tables"][0]["rows"][0]
    assert row["id"] == "sales.targets" and result["answer"] == row["sample"]
    assert "单店" in result["answer"] and "达成率" in result["answer"]


@pytest.mark.parametrize(
    ("question", "domain", "expected"),
    [
        ("本月GMV目标达成怎么样？", "products", "channel"),
        ("这个月这家店的目标是多少？", "sales", "shop"),
        ("官方店本月的销售目标达成了吗？", "sales", "shop"),
        ("这个月这家店的目标是多少？", "promotion_store", None),
        ("这款这个月的GMV目标是多少？", "products", None),
        ("本月GMV目标、成交最多的店和退款率分别是多少？", "sales", None),
        ("本月GMV目标还差多少，应该怎么补？", "sales", None),
        ("今年和上月的GMV目标哪个高？", "sales", None),
        ("本月GMV目标是多少？再说说投产。", "sales", None),
    ],
)
def test_only_standalone_target_questions_take_the_short_path(question, domain, expected):
    from app.modules.assistant.target_answers import target_scope

    assert target_scope(question, domain) == expected


def test_rate_change_uses_raw_rates_and_keeps_zero_distinct_from_missing():
    from app.modules.assistant.business import change_points

    assert change_points("percent", "0.0577830188679", "0.0485611510791") == "0.92"
    assert change_points("percent", "0", "0.05") == "-5.00"
    assert change_points("percent", "0.10", "0") == "10.00"
    assert change_points("percent", "0", None) is None
    assert change_points("ratio", "6.22", "7.35") is None


@pytest.mark.parametrize(
    ("target", "actual", "rate", "gap", "missing", "state", "expected"),
    [
        ("1000", "0", "0", "1000", 0, "observed", "达成 0.0%"),
        (None, "1000", None, None, 0, "source_unavailable", "无法计算达成率"),
        ("1000", None, None, None, 6, "observed", "缺少成交记录"),
        ("1000", "800", ".8", "200", 2, "failed_retained", "上次成功更新"),
        ("1000", "1200", "1.20", "0", "0", "observed", "达成 120.0%"),
    ],
)
def test_monthly_channel_answer_reports_only_available_amounts(
    target, actual, rate, gap, missing, state, expected
):
    from app.modules.assistant.target_answers import channel_progress

    facts = dict(
        month_target=target,
        month_actual=actual,
        month_ratio=rate,
        gap=gap,
        month_missing_days=missing,
    )
    answer = channel_progress(
        dict(
            period={"date_to": "2026-11-24"},
            scope={"month": "2026-11", "target_source_state": state},
            facts=[dict(key=k, value=v) for k, v in facts.items()],
        )
    )
    assert expected in answer
    assert ("个店铺日" in answer) == (int(missing) > 0)
    assert ("距目标还差" in answer) == (gap is not None and int(gap) > 0)
