"""M5当前报告指标与图表；只用固定输入原值，未知规则不产生可信评级。"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .metrics import from_values, groups, identity, matches, ratio, summarize, total
from .repository import ROLES, public_report


@dataclass(frozen=True)
class AfterQuery:
    report_id: str | None = None
    category: str = ""
    keyword: str = ""
    focus: str = "all"
    view: str = "category"
    page: int = 1
    size: int = 25


def roles(repo, q, report=None):
    if q.focus != "all":
        return [None, None, None]
    blocks = [repo.rows(role, report) for role in ROLES]
    chosen = {identity(row) for block in blocks for row in block or [] if matches(row, q)}
    return [
        [row for row in block if identity(row) in chosen] if block is not None else None
        for block in blocks
    ]


def options(repo, q):
    rows = repo.rows("j1_returns") or []
    cats = sorted({r["source_product_category"] for r in rows if r.get("source_product_category")})
    return dict(
        reports=[public_report(r) for r in repo.defaults],
        categories=cats,
        note="报告期间与来源完整性分别显示；缺少分类时不从款号推定。",
    )


METRICS = (
    ("returned_qty", "实退数量", "number", "确认期间已观察实退数量，不是去重订单数。"),
    (
        "inbound_amount",
        "实际入库金额",
        "currency",
        "仅用已核实际入库金额，不以售后金额或实际收货金额替代。",
    ),
    ("gmv", "支付口径GMV", "currency", "G=实发金额S+已取消销售金额P，任一角色缺项则不可计算。"),
    ("presale_rate", "售前退款率", "percent", "P/G；已取消销售代理，不证明现金退款成功。"),
    ("aftersale_rate", "售后退款率", "percent", "A/S；确认期间与支付期间不是同批订单。"),
    ("total_rate", "总退款率", "percent", "(P+A)/(S+P)，不把售前率和售后率相加。"),
    ("records", "售后记录", "number", "接收明细行，不是原导出商品/原因汇总行、去重订单或客户数。"),
    ("size_qty", "尺码问题", "number", "原原因分类/词典及主原因优先级待核，缺失不计零。"),
    (
        "quality_qty",
        "质量问题",
        "number",
        "质量主原因与多标签计数待M5-02确认，不相加冒充独立件数。",
    ),
)


def metric(key, label, type, value, note, detail=""):
    return dict(
        key=key,
        label=label,
        type=type,
        value=value,
        note=note,
        detail=detail,
        status="observed_reference" if value is not None else "unavailable",
    )


def reasons(rows):
    if rows is None:
        return []
    counts = defaultdict(list)
    for r in rows:
        counts[r.get("source_return_reason") or "未填写原因"].append(r)
    denominator = total(rows, "actual_returned_qty")
    return [
        dict(
            name=name,
            qty=total(block, "actual_returned_qty"),
            share=ratio(total(block, "actual_returned_qty"), denominator),
        )
        for name, block in sorted(
            counts.items(),
            key=lambda p: (-(total(p[1], "actual_returned_qty") or Decimal(0)), p[0]),
        )
    ]


def history_reports(repo, q):
    return [repo.report] if q.report_id and repo.report else repo.defaults


def report_totals(repo, report):
    summaries = {role: repo.source_summary(role, report) for role in ROLES}
    j1, j2, j3 = (summaries[role] for role in ROLES)
    return from_values(
        j1["actual_returned_qty"] if j1 else None,
        j1["actual_inbound_amount"] if j1 else None,
        j2["sales_amount"] if j2 else None,
        j3["sales_amount"] if j3 else None,
        str(j1["rows"]) if j1 else None,
    )


def overview(repo, q):
    blocks = roles(repo, q)
    values = summarize(*blocks)
    metrics = [metric(key, label, type, values[key], note) for key, label, type, note in METRICS]
    report = repo.report
    scope = (
        "关键词/重点先选完整对象，指标和原因统计所选对象的全部明细；"
        "金额/比率为已观察参考，完整日未知。"
    )
    if q.focus != "all":
        scope += " 尺码/质量分类规则尚未核实，此重点不返回推测匹配结果。"
    risk_metrics = [
        metric(
            "total_rate",
            "总退款率",
            "percent",
            values["total_rate"],
            "来源与完整性待核，暂不评级。",
        ),
        metric(
            "total_amount",
            "退款金额",
            "currency",
            values["total_amount"],
            "P+A；非已证实的现金退款金额。",
        ),
        metric("change_pp", "较上期变化", "points", None, "须相容固定上期报告及两侧可用分母。"),
        metric("target_gap", "目标差距", "points", None, "目标周期与汇总映射待M5-03确认。"),
        metric(
            "excess_estimate",
            "预计损失",
            "currency",
            None,
            "缺有效目标，暂不测算；超目标退款测算不是利润损失。",
        ),
    ]
    trend = []
    for r in list(reversed(history_reports(repo, q)[:8])):
        trend.append(
            dict(
                id=r["id"],
                label=public_report(r)["label"],
                period_start=str(r["period_start"]),
                period_end=str(r["period_end"]),
                qty=report_totals(repo, r)["returned_qty"],
            )
        )

    def risks(level):
        result = []
        for row in sorted(
            (r for r in groups(blocks, level) if r["metrics"]["inbound_amount"] is not None),
            key=lambda r: (-(r["metrics"]["inbound_amount"] or Decimal(0)), str(r["key"])),
        )[:5]:
            m = row["metrics"]
            e = row["example"]
            result.append(
                dict(
                    id=str(row["key"]),
                    name=e.get("source_product_category") or "待核对品类"
                    if level == "category"
                    else e.get("source_product_name") or "未提供商品名称",
                    amount=m["inbound_amount"],
                    amount_share=ratio(m["inbound_amount"], values["inbound_amount"]),
                    total_refund_rate=m["total_rate"],
                    change_pp=None,
                    risk_score=None,
                    note="已观察规模参考；风险分、跨期判断与根因未确认，不作为正式风险排序。",
                )
            )
        return result

    stages = [
        dict(
            name="未发货退款",
            amount=values["presale_amount"],
            qty=None,
            share=ratio(values["presale_amount"], values["total_amount"]),
            trend="趋势待核",
            status="observed_reference" if values["presale_amount"] is not None else "unavailable",
            note="已取消销售金额代理；销售数量不补作退款件数。",
        ),
        *[
            dict(
                name=name,
                amount=None,
                qty=None,
                share=None,
                trend="趋势待补",
                status="unavailable",
                note="缺少相容物流阶段事实；原因词不证明真实拒收/签收，不用相减反推。",
            )
            for name in ("已发货拒收", "签收后退货")
        ],
    ]
    return dict(
        report=public_report(report),
        metrics=metrics,
        risk_metrics=risk_metrics,
        decision=dict(
            title="先核对来源与覆盖，再判断退款风险",
            status="pending_validation",
            rate=values["total_rate"],
            amount=values["total_amount"],
            amount_share=None,
            change_pp=None,
            note="M5-01—04及来源完整性未关闭，不生成可信综合分或人工整改进度。",
        ),
        priorities=[],
        actions=[],
        stages=stages,
        size_breakdown=[
            dict(name=n, qty=None, share=None) for n in ("尺码偏大", "尺码偏小", "尺码不合适未说明")
        ],
        reasons=reasons(blocks[0]),
        trend=trend,
        scope_note=scope,
        quality_note="原行缺金额将阻断对应总额及比率；未接入/缺记录/缺字段不补零。三角色独立聚合，完整组键不匹配时细层分母不可用。原因按来源原文本展示，未生成已确认主原因/质量标签。",
        core_insights=[
            dict(
                label="阅读口径",
                title="实际期间与观察范围分开",
                detail="J1确认时间、J2/J3支付时间；接收明细行不是去重订单或旧报告汇总行。",
            ),
            dict(
                label="风险与建议",
                title="当前只作原值参考",
                detail="没有相容比较或有效目标时不称改善/达标，不伪造整改效果。",
            ),
        ],
        category_risks=risks("category"),
        style_risks=risks("style"),
        category_actions=[],
        style_actions=[],
    )
