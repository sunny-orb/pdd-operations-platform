"""已知月度 GMV 目标查询。只接完整、单一的查数句，其他提问继续由 Agent 处理。"""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from app.modules.sales.source_policy import SHOPS

from . import dashboard

_SHOPS = sorted(
    {name for _, full, short in SHOPS.values() for name in (full, short)}, key=len, reverse=True
)
_SHOP = (
    r"(?:只看)?(?:刚才|刚刚|当前)?(?:[这那]家(?:店铺|店)?|该店|本店|单店|"
    + "|".join(map(re.escape, _SHOPS))
    + ")"
)
_MONTH = r"(?:本月|这个月|当月|上月|上个月|月度|月)"
# fullmatch 保证另问的成交、原因、建议或第二个期间仍交给普通问答。
_TARGET = re.compile(
    rf"(?:(?P<shop>{_SHOP})[,，]?它?的?{_MONTH}?|{_MONTH}(?P<after>{_SHOP})?"
    rf"|(?:六店(?:合计)?|渠道)的?{_MONTH}?)"
    r"的?(?:六店(?:合计)?|渠道)?(?:GMV|销售额?|成交额)?目标"
    r"(?:是多少|有多少|多少钱|多少|怎么样|如何)?(?:[、,，]?还?差多少|[、,，]?(?:完成|达成)"
    r"(?:率|情况|进度)?(?:了)?(?:几成|多少|怎么样|如何|吗)?)?[呢吗啊]?[?？。]?",
    re.I,
)


def target_scope(question: str, domain: str | None) -> Literal["shop", "channel"] | None:
    text = re.sub(r"\s+", "", question)
    if domain != "sales" and not re.search(r"GMV|销售|成交额", text, re.I):
        return None
    match = _TARGET.fullmatch(text)
    if match:
        return "shop" if match["shop"] or match["after"] else "channel"
    return None


def channel_progress(report: dict) -> str:
    """目标、完成额与完成率逐项读取。保留空值、缺日和最近更新失败的边界。"""
    values = {f["key"]: f["value"] for f in report["facts"]}
    month = report["scope"]["month"]
    target, actual, rate = (values[k] for k in ("month_target", "month_actual", "month_ratio"))
    if target is None:
        return f"{month} 的六店渠道月目标暂无可用值，目前无法计算达成率。"
    with localcontext(rounding=ROUND_HALF_UP):

        def money(value):
            amount = Decimal(value)
            return f"{amount / 10000:,.2f} 万元" if abs(amount) >= 10000 else f"{amount:,.2f} 元"

        answer = f"{month} 六店渠道的 GMV 目标是 {money(target)}。"
        if actual is None:
            answer += "目前缺少成交记录，暂时无法计算达成率。"
        else:
            answer += f"截至 {report['period']['date_to']}，已完成 {money(actual)}"
            answer += (
                f"，达成 {Decimal(rate) * 100:.1f}%。"
                if rate is not None
                else "，达成率暂无可用值。"
            )
            gap = values["gap"]
            if gap is not None and Decimal(gap) > 0:
                answer += f"距目标还差 {money(gap)}。"
    if int(values["month_missing_days"] or 0) > 0:
        answer += f"有 {values['month_missing_days']} 个店铺日缺成交记录，完成额按已记录数据统计。"
    if report["scope"]["target_source_state"] == "failed_retained":
        answer += "目标采用上次成功更新的版本。"
    return answer


async def target_answer(run) -> str | None:
    scope = target_scope(run.question, run.scope.domain)
    if scope == "shop":
        data = await dashboard.read(
            run,
            dashboard.DashboardRequest(
                capability="rule_definitions", view="sales", keyword="sales.targets"
            ),
        )
        return next(
            (
                r["sample"]
                for t in data["tables"]
                for r in t["rows"]
                if r["id"] == "sales.targets" and r["state"] == "defined"
            ),
            None,
        )
    if scope == "channel":
        data = await dashboard.read(run, dashboard.DashboardRequest(capability="sales_targets"))
        return channel_progress(data)
    return None
