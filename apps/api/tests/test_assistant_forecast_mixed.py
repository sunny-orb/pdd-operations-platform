"""已核的预测和补充业务在普通响应、SSE 和失败路径使用同一份答案。"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import PartStartEvent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import TextPart
from pydantic_ai.usage import RunUsage

from app.modules.assistant import chat, dashboard
from app.modules.assistant.business import fact, report, table

FIXED = "六店月末 GMV 中心估计为 120.00 万元。"
QUESTION = "预测11月GMV，顺便看总退款率"


def forecast():
    return dict(
        report(
            "sales",
            "sales_forecast",
            "六店预测",
            "销售日报",
            "f4",
            scope={"month": "2026-11"},
            versions={"forecast_sop": "4"},
        ),
        answer=FIXED,
        forecast_status="forecast",
    )


def refund():
    return report(
        "aftersales",
        "aftersales_overview",
        "售后报告",
        "月报",
        "r9",
        period={"date_from": "2026-10-01", "date_to": "2026-10-31"},
        facts=[
            fact("orders", "总订单数", "780"),
            fact("total_rate", "总退款率", "0.125", "percent"),
        ],
    )


@pytest.mark.parametrize("streaming", [True, False])
def test_mixed_delivers_fixed_forecast_first_and_only_verified_supplement(monkeypatch, streaming):
    events, before_tool = [], []

    async def read(run, request):
        record = forecast() if request.capability == "sales_forecast" else refund()
        run.report_cache[(request.capability,)] = record
        return record

    monkeypatch.setattr(dashboard, "read", read)

    class Rewrite:
        async def run(self, message, *, deps, **options):
            before_tool.extend(e["text"] for e in events if e["type"] == "delta")
            await dashboard.read(deps, dashboard.DashboardRequest(capability="aftersales_overview"))
            handler = options.get("event_stream_handler")
            if handler:

                async def invented():
                    yield PartStartEvent(index=0, part=TextPart("月底预测 1000 万元。"))

                await handler(None, invented())
            return SimpleNamespace(output="月底预测 1000 万元，退款率 90%。", usage=RunUsage())

    run = chat.QueryRun(
        None, chat.ChatScope(domain="sales"), QUESTION, emit=events.append if streaming else None
    )
    result = asyncio.run(chat.answer_question(Rewrite(), QUESTION, "店铺销售", [], run))
    answer = result["answer"]
    assert answer.startswith(FIXED) and answer.count(FIXED) == 1
    assert "1000" not in answer and "90%" not in answer and "780" not in answer
    assert "总退款率" in answer and "12.50%" in answer
    assert "2026-10-01" in answer and "2026-10-31" in answer
    if streaming:
        assert before_tool == [FIXED]
        assert "".join(e["text"] for e in events if e["type"] == "delta") == answer


@pytest.mark.parametrize("outcome", ["empty", "clarify", "budget", "provider", "partial"])
def test_missing_supplement_always_leaves_fixed_forecast_available(monkeypatch, outcome):
    run = chat.QueryRun(None, chat.ChatScope(domain="sales"), QUESTION)
    events = []
    run.emit = events.append

    async def read(deps, request):
        data = forecast()
        deps.report_cache[("fixed",)] = data
        return data

    monkeypatch.setattr(dashboard, "read", read)

    class Failing:
        async def run(self, *args, **options):
            if outcome == "clarify":
                raise chat.Clarify("请指定要比较的售后报告。")
            if outcome == "budget":
                raise UsageLimitExceeded("budget")
            if outcome == "partial":
                run.report_cache[("refund",)] = refund()
            if outcome in {"provider", "partial"}:
                raise RuntimeError("private-provider-error")
            return SimpleNamespace(output="我猜 1000 万元", usage=RunUsage())

    result = asyncio.run(chat.answer_question(Failing(), QUESTION, "店铺销售", [], run))
    assert result["status"] == "answered" and result["answer"].startswith(FIXED)
    assert result["answer"] != FIXED and "1000" not in result["answer"]
    assert "private-provider-error" not in result["answer"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == result["answer"]
    if outcome == "clarify":
        assert "指定要比较的售后报告" in result["answer"]
    if outcome == "partial":
        assert "12.50%" in result["answer"]


def test_table_only_supplement_keeps_its_own_scope_and_reported_rows(monkeypatch):
    question = "预测11月GMV，顺便看退款商品"
    data = report(
        "aftersales",
        "aftersales_analysis",
        "单款退款",
        "售后月报",
        "a",
        period={"date_from": "2026-10-01", "date_to": "2026-10-31"},
        tables=[
            table(
                "商品退款",
                [("name", "商品", "text"), ("amount", "退款金额", "currency")],
                [{"name": "童靴 A", "amount": "120.00"}],
                total=1,
            )
        ],
    )

    async def read(run, request):
        r = forecast() if request.capability == "sales_forecast" else data
        run.report_cache[(request.capability,)] = r
        return r

    monkeypatch.setattr(dashboard, "read", read)

    class WithTable:
        async def run(self, message, *, deps, **kw):
            await dashboard.read(deps, dashboard.DashboardRequest(capability="aftersales_analysis"))
            return SimpleNamespace(output="无依据的预测", usage=RunUsage())

    run = chat.QueryRun(None, chat.ChatScope(), question)
    result = asyncio.run(chat.answer_question(WithTable(), question, "店铺销售", [], run))
    assert all(term in result["answer"] for term in (FIXED, "童靴 A", "退款金额", "120.00 元"))


def test_lookup_of_specific_product_keeps_the_row_when_the_report_also_has_summary_facts():
    from app.modules.assistant.forecast_supplement import supplement_text

    data = refund()
    data["tables"] = [
        table(
            "单款退款明细",
            [("name", "商品", "text"), ("refund", "退款金额", "currency")],
            [{"name": "雨靴", "refund": "25.00"}],
        )
    ]
    data["notes"] = ["部分店铺日记录尚未收齐。"]
    text = supplement_text([data], "预测11月GMV，另外看哪个商品退款多", [])
    assert "雨靴" in text and "退款金额 25.00 元" in text and "尚未收齐" in text
