"""问答的收尾条件、条件性解释与部分结果。业务数值只取自已生成的报告。"""

import logging
from decimal import Decimal
from time import monotonic
from typing import Literal

from pydantic import BaseModel
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

MAX_REQUESTS = 6
MAX_TOOL_CALLS = 6
logger = logging.getLogger("uvicorn.error.assistant")


class AnswerPolicy(AbstractCapability):
    async def prepare_tools(self, ctx, tool_defs):
        """预留一次模型请求整理答案；本轮工具没有新信息时提前收尾。"""
        if ctx.usage.requests >= MAX_REQUESTS - 1 or ctx.usage.tool_calls >= MAX_TOOL_CALLS - 1:
            return []
        if repeated_tool_round(ctx.messages):
            ctx.deps.failure_reason = "repeated_query"
            return []
        return tool_defs

    async def prepare_output_tools(self, ctx, tool_defs):
        # 已取到报告或明确的范围/数据错误时，必须沿用业务查询结果回答。
        return [] if ctx.deps.reports or ctx.deps.tool_messages else tool_defs

    async def wrap_model_request(self, ctx, *, request_context, handler):
        start = monotonic()
        try:
            return await handler(request_context)
        finally:
            logger.info(
                "assistant_model request_id=%s step=%s elapsed_ms=%s",
                ctx.deps.request_id,
                ctx.run_step,
                round((monotonic() - start) * 1000),
            )

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        start = monotonic()
        try:
            return await handler(args)
        finally:
            logger.info(
                "assistant_tool request_id=%s step=%s tool=%s elapsed_ms=%s",
                ctx.deps.request_id,
                ctx.run_step,
                tool_def.name,
                round((monotonic() - start) * 1000),
            )


class ObservationExplanation(BaseModel):
    """运营只讨论现象的含义或确认收到时，在一次模型响应内交付回答。"""

    topic: Literal[
        "missing_record", "incomplete_coverage", "observed_zero", "causal_signal", "acknowledgement"
    ]

    def answer(self) -> str:
        return {
            "missing_record": (
                "有可能。假如同店其他款当天有记录，只有这款缺记录，可以把它列为销售偏弱的线索。"
                "如果确认这款当天在售、成交明细已收齐，而且只有成交才出记录，就能判断当天没卖出。"
                "再和它平时的销量比，就能看出这一天是否反常。"
            ),
            "observed_zero": (
                "如果指标有记录且值为零，就可以按该指标的口径解释。比如支付买家数为零，"
                "说明覆盖期间没有买家支付。要判断表现好坏，还要看这款平时或同类商品的表现。"
            ),
            "incomplete_coverage": (
                "有记录的日期可以用来比较。缺记录的日期要单独看：先确认这张表在什么情况下会出记录、"
                "当天是否收齐，再判断空白对这个指标意味着什么。"
            ),
            "causal_signal": (
                "两个指标一起变化，可以用来确定排查方向。要确认原因，还要核对变化发生的先后、"
                "受影响的商品范围，以及同期活动或投放是否也有变化。"
            ),
            "acknowledgement": "好的。",
        }[self.topic]


def repeated_tool_round(messages) -> bool:
    """连续两批成功调用的名称、参数均相同才收尾，换范围、翻页和修正参数可继续。"""
    if len(messages) < 4:
        return False
    first, first_results, second, second_results = messages[-4:]
    if not isinstance(first, ModelResponse) or not isinstance(second, ModelResponse):
        return False
    if not all(
        isinstance(message, ModelRequest)
        and message.parts
        and all(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in (first_results, second_results)
    ):
        return False

    def calls(response):
        return [(p.tool_name, p.args_as_dict()) for p in response.tool_calls]

    if not second.tool_calls or calls(first) != calls(second):
        return False
    if len(first_results.parts) != len(second_results.parts):
        return False
    return all(
        old.tool_name == new.tool_name
        and (
            old.content == new.content
            or isinstance(new.content, dict)
            and new.content.get("status") == "already_queried"
        )
        for old, new in zip(first_results.parts, second_results.parts, strict=True)
    )


def partial_answer(reports: list[dict]) -> str:
    """预算已尽时按实际报告标明范围，最多摘一项已观察到的指标，不从缺项推结论。"""
    lines = []
    for report in reports:
        title = report.get("title") or (
            "商品经营" if report["kind"] == "product_performance" else "商品推广"
        )
        period = report["period"]
        if period:
            title += f"（{period['date_from']} 至 {period['date_to']}）"
        for fact in report.get("facts", []):
            if fact["status"] != "observed" or fact["value"] is None:
                continue
            value, unit = fact["value"], fact["unit"]
            if unit == "percent":
                value = f"{Decimal(str(value)) * 100:f}%"
            elif unit == "currency":
                value = f"{value} 元"
            elif unit == "ratio":
                value = f"{value} 倍"
            title += f"，{fact['label']}为{value}"
            break
        lines.append(title)
    return "已查到" + "；".join(dict.fromkeys(lines)) + "。本轮只覆盖以上结果。"
