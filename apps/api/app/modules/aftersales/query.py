"""M5当前报告指标与图表；只用固定输入原值，未知规则不产生可信评级。"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .amounts import amount_details
from .classification import classify_rows, enabled, observations
from .decisions import decisions
from .metrics import from_values, groups, identity, matches, ratio, summarize, total
from .repository import ROLES, BoardError, public_report


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
    parameters = repo.classification_parameters(report)
    if q.focus != "all" and not enabled(parameters, q.focus):
        return [None, None, None]
    blocks = [repo.rows(role, report) for role in ROLES]
    if not q.category and not q.keyword and q.focus == "all":
        return blocks
    chosen = {identity(row) for block in blocks for row in block or [] if matches(row, q)}
    if q.focus != "all":
        focused = {
            identity(row)
            for row, result in classify_rows(blocks[0] or [], parameters)
            if result[q.focus]
        }
        chosen &= focused
    return [
        [row for row in block if identity(row) in chosen] if block is not None else None
        for block in blocks
    ]


def options(repo, q):
    summary = repo.source_summary("j1_returns", include_categories=True)
    if summary and summary["rows"] > 300000:
        raise BoardError("REPORT_SCOPE_ROW_LIMIT", 503)
    cats = sorted(summary["categories"] or []) if summary else []
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
    (
        "size_qty",
        "尺码问题",
        "number",
        "已接原原因文本识别的观察量；方向冲突归未说明，非全量语义判断。",
    ),
    (
        "quality_qty",
        "质量问题",
        "number",
        "已接原原因文本识别的观察量；任一标签命中时每原行Q只计一次，细标签合计不是总量。",
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


def reasons(rows, parameters=None):
    if rows is None:
        return []
    counts = defaultdict(list)
    for r, result in classify_rows(rows, parameters):
        counts[result["primary_reason"]].append(r)
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
    parameters = repo.classification_parameters()
    values = summarize(*blocks, parameters=parameters)
    observed = observations(blocks[0], parameters)
    metrics = [metric(key, label, type, values[key], note) for key, label, type, note in METRICS]
    if repo.is_rpa():
        next(m for m in metrics if m["key"] == "records")["note"] = (
            "RPA售后商品×原因的期间原行数，不是订单数；不含销售报表合计行。"
        )
    details = amount_details(*blocks)
    for item in metrics:
        item["detail"] = details.get(item["key"], "")
    report = repo.report
    scope = (
        "关键词/重点先选完整对象，指标和原因统计所选对象的全部明细；"
        "金额/比率为已观察参考，完整日未知。"
    )
    if q.focus != "all" and not enabled(parameters, q.focus):
        scope += " 尺码/质量分类规则尚未核实，此重点不返回推测匹配结果。"
    elif q.focus != "all":
        scope += " 问题重点按固定词典选中对象，保留这些对象的全部原因及销售分母。"
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
    previous_blocks = None
    previous_parameters = {}
    previous = repo.by_id.get(report["previous_report_id"]) if report else None
    if previous and report["previous_comparison_status"] == "available":
        previous_blocks = roles(repo, q, previous)
        previous_parameters = repo.classification_parameters(previous)
        old = summarize(*previous_blocks, parameters=previous_parameters)
        if values["total_rate"] is not None and old["total_rate"] is not None:
            next(m for m in risk_metrics if m["key"] == "change_pp").update(
                value=(values["total_rate"] - old["total_rate"]) * 100, status="observed_reference"
            )
    trend = []
    for r in reversed(history_reports(repo, q)[:8]):
        returns = repo.source_summary("j1_returns", r)
        trend.append(
            dict(
                id=r["id"],
                label=public_report(r)["label"],
                period_start=str(r["period_start"]),
                period_end=str(r["period_end"]),
                qty=returns["actual_returned_qty"] if returns else None,
            )
        )

    def risks(level):
        result = []
        amount_available = values["inbound_amount"] is not None
        field = "inbound_amount" if amount_available else "returned_qty"
        for row in sorted(
            (r for r in groups(blocks, level, parameters) if r["metrics"][field] is not None),
            key=lambda r: (-(r["metrics"][field] or Decimal(0)), str(r["key"])),
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
                    note=(
                        f"本层已观察实退Q={m['returned_qty']}的规模参考；先核金额与覆盖。"
                        "品类与单品分别排序，不混排分数、不构成正式异常或根因。"
                    ),
                )
            )
        return result

    # 旧v1此列实际承载product_aftersale_type；不能将旧“退货”重解释成真实售后分类。
    source = repo.inputs().get("j1_returns") or {}
    verified_refusal_field = source.get("row_schema_revision") in {
        "m5-jst-current-sku-v2",
        "m5-jst-current-style-consensus-v3",
        "m5-jst-current-bundle-consensus-v4",
    }
    full_window = repo.rows("j1_returns") if verified_refusal_field else None
    refusal_available = bool(blocks[0] and full_window) and all(
        isinstance(r.get("source_aftersale_type"), str) and r["source_aftersale_type"].strip()
        for r in full_window or []
    )
    refusal_rows = [r for r in blocks[0] or [] if r.get("source_aftersale_type") == "拒收退货"]
    refusal_qty = (
        (total(refusal_rows, "actual_returned_qty") if refusal_rows else Decimal(0))
        if refusal_available
        else None
    )
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
        dict(
            name="已发货拒收",
            amount=None,
            qty=refusal_qty,
            share=None,
            trend="趋势待补",
            status="observed_reference" if refusal_qty is not None else "unavailable",
            note=(
                "按来源售后分类“拒收退货”的Q观察量；来源标记不是独立物流证据，金额仍待核。"
                if refusal_available
                else "本报告J1未具备已核v2售后分类或全窗字段覆盖不足；拒收未知保留NULL，不补零。"
            ),
        ),
        dict(
            name="签收后退货",
            amount=None,
            qty=None,
            share=None,
            trend="趋势待补",
            status="unavailable",
            note="缺相容签收事件，不用总Q减来源拒收标记反推。",
        ),
    ]
    computed = decisions(blocks, previous_blocks, parameters, previous_parameters)
    result = dict(
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
            note="金额与来源完整性仍有门禁；词典观察按本报告固定包，不生成综合分或人工整改进度。",
        ),
        priorities=[],
        actions=[],
        stages=stages,
        size_breakdown=observed["size_rows"],
        reasons=reasons(blocks[0], parameters),
        trend=trend,
        scope_note=scope,
        quality_note=(
            "原行缺金额阻断对应总额及比率；缺记录/字段不补零，完整组键不匹配时分母不可用。"
            + observed["note"]
        ),
        core_insights=[
            dict(
                label="阅读口径",
                title="实际期间与观察范围分开",
                detail=(
                    "RPA售后按确认期间、销售按支付期间；商品/原因汇总原行不是订单。"
                    if repo.is_rpa()
                    else "J1确认时间、J2/J3支付时间；接收明细行不是去重订单或旧报告汇总行。"
                ),
            ),
            dict(
                label="风险与建议",
                title="当前只作原值参考",
                detail="没有相容比较或有效目标时不称改善/达标，不伪造整改效果。",
            ),
        ],
        category_risks=[] if computed else risks("category"),
        style_risks=[] if computed else risks("style"),
        category_actions=[],
        style_actions=[],
    )

    if computed is not None:
        result.update(computed)
        result["scope_note"] = (
            "本期已交付报告；售后按确认期间、销售按支付期间，不是同一批订单的最终退款率。"
        )
        result["core_insights"][1] = dict(
            label="风险与建议",
            title="按金额影响与SOP趋势核查",
            detail="品类与单款分别排序；分数仅为同层核查顺序，P级按SOP阈值。建议不代表原因已确认或整改已完成。",
        )
        next(m for m in result["risk_metrics"] if m["key"] == "total_rate")["note"] = (
            "(取消销售金额+实际入库金额)÷支付口径GMV"
        )
    return result
