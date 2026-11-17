"""M5六标签投影与分页；完整对象配对，不从Top结果重建总体。"""

import hashlib
from decimal import Decimal

from app.modules.sales.datasets import REFUND_SHOPS
from app.modules.sales.source_policy import display_shop_name

from .classification import compatible, enabled, observations
from .metrics import groups, ratio, summarize
from .query import METRICS, history_reports, reasons, report_totals, roles
from .repository import public_report

CATEGORY = [
    ("category", "品类", "text"),
    ("returned_qty", "实退数量", "number"),
    ("inbound_amount", "实际入库金额", "currency"),
    ("gmv", "GMV", "currency"),
    ("presale_rate", "售前退款率", "percent"),
    ("aftersale_rate", "售后退款率", "percent"),
    ("total_rate", "总退款率", "percent"),
    ("qty_share", "数量占比", "percent"),
    ("amount_share", "金额占比", "percent"),
    ("reasons", "原因Top", "list"),
    ("size", "尺码拆分", "list"),
    ("quality", "质量Top", "list"),
]
STYLE = [
    ("category", "品类", "text"),
    ("rank", "排名", "number"),
    ("style_code", "款式编码", "text"),
    ("product", "商品", "text"),
    *CATEGORY[1:7],
    *CATEGORY[9:],
]
COMPARISON = [
    ("type", "类型", "text"),
    ("object", "指标/对象", "text"),
    ("current", "本期", "text"),
    ("previous", "上期", "text"),
    ("change", "变化", "text"),
    ("rate_change", "总退款率变化", "points"),
    ("reasons", "本期原因Top", "list"),
]
QUALITY = [("name", "质量问题", "text"), ("qty", "数量", "number"), ("share", "占比", "percent")]
HISTORY = [
    ("label", "报表", "text"),
    ("type", "类型", "text"),
    ("period", "周期", "text"),
    ("returned_qty", "实退数量", "number"),
    ("inbound_amount", "实际入库金额", "currency"),
    ("gmv", "GMV", "currency"),
    ("presale_rate", "售前退款率", "percent"),
    ("aftersale_rate", "售后退款率", "percent"),
    ("total_rate", "总退款率", "percent"),
    ("generated_at", "生成时间", "text"),
]


def cell(value=None, detail=None, items=None):
    result = {"value": str(value) if value is not None else None}
    if detail:
        result["detail"] = detail
    if items is not None:
        result["items"] = items
    return result


def row_id(key):
    return hashlib.sha256(str(key).encode()).hexdigest()


def missing_metric_note(key, metrics, returns):
    if key in ("returned_qty", "inbound_amount"):
        if not returns:
            return "报表里没有这组退货记录。"
        if key == "returned_qty":
            return "有退货记录缺少实退数量。"
        return "实际入库金额缺失或还没核实。"
    if key == "gmv":
        return "缺少可对应的已发货或已取消销售金额。"
    denominator = metrics["shipped_amount" if key == "aftersale_rate" else "gmv"]
    if denominator is not None and denominator <= 0:
        amount = "实发金额" if key == "aftersale_rate" else "支付口径GMV"
        return f"{amount}为 0 或负数，算不出退款率。"
    if key == "presale_rate":
        return "缺少可对应的已发货或已取消销售金额，算不出退款率。"
    return "缺少可对应的退货或销售金额，算不出退款率。"


def object_rows(repo, q, level):
    blocks = roles(repo, q)
    parameters = repo.classification_parameters()
    whole = summarize(*blocks, parameters=parameters)
    all_values = groups(blocks, level, parameters)
    order_field = "inbound_amount" if whole["inbound_amount"] is not None else "returned_qty"
    all_values.sort(key=lambda r: (-(r["metrics"][order_field] or Decimal(0)), str(r["key"])))
    result = []
    offset = (q.page - 1) * q.size
    for rank, row in enumerate(all_values[offset : offset + q.size], offset + 1):
        m = row["metrics"]
        e = row["example"]
        issues = observations(row["roles"][0], parameters)
        cells = {
            k: cell(
                m.get(k),
                missing_metric_note(k, m, row["roles"][0]) if m.get(k) is None else None,
            )
            for k in (
                "returned_qty",
                "inbound_amount",
                "gmv",
                "presale_rate",
                "aftersale_rate",
                "total_rate",
            )
        }
        cells.update(
            category=cell(e.get("source_product_category") or "待核对品类"),
            rank=cell(rank),
            style_code=cell(
                e.get("source_style_code"),
                "报表没提供款式编码。" if not e.get("source_style_code") else None,
            ),
            product=cell(
                e.get("source_product_name"),
                "；".join(
                    str(
                        f"kk树-{display_shop_name('spitztx', e[k])}-配饰"
                        if k == "source_shop_name" and REFUND_SHOPS.get(e[k]) == "spitztx"
                        else e[k]
                    )
                    for k in (
                        "source_shop_name",
                        "source_subcategory",
                        "source_color_specification",
                    )
                    if e.get(k)
                ),
            ),
            qty_share=cell(ratio(m["returned_qty"], whole["returned_qty"])),
            amount_share=cell(ratio(m["inbound_amount"], whole["inbound_amount"])),
            reasons=cell(items=reasons(row["roles"][0], parameters)[:5]),
            size=cell(items=issues["size_rows"]),
            quality=cell(items=issues["quality_rows"]),
        )
        result.append(dict(id=row_id(row["key"]), cells=cells))
    return result, len(all_values)


def comparison_text(value, kind):
    if value is None:
        return None
    if kind == "percent":
        return f"{value * 100:.2f}%"
    if kind == "currency":
        return f"¥{value:,.2f}"
    return format(value, "f")


def change_text(current, previous, kind):
    if current is None or previous is None:
        return None
    difference = current - previous
    if kind == "percent":
        return f"{difference * 100:+.2f}个百分点"
    absolute = comparison_text(difference, kind)
    if previous <= 0:
        base = "是 0" if previous == 0 else "为负数"
        return f"{absolute}（对比值{base}，算不出涨跌幅）"
    return f"{absolute}（{difference / previous * 100:+.2f}%）"


def comparison(repo, q):
    r = repo.report
    if not r:
        return [], 0, "当前没有可用的报告。"
    prefix = "yoy" if q.view == "yoy" else "previous"
    previous = repo.by_id.get(r[prefix + "_report_id"])
    if r[prefix + "_comparison_status"] != "available" or previous is None:
        label = "去年同期" if q.view == "yoy" else "上一期"
        return [], 0, f"暂无可对比的{label}报告。"
    rolling_weeks = all(
        report["report_type"] == "weekly"
        and (report["period_end"] - report["period_start"]).days == 29
        for report in (r, previous)
    )
    parameters = repo.classification_parameters()
    previous_parameters = repo.classification_parameters(previous)
    current_blocks = roles(repo, q)
    previous_blocks = roles(repo, q, previous)
    now = summarize(*current_blocks, parameters=parameters)
    old = summarize(*previous_blocks, parameters=previous_parameters)
    result = []
    for k, label, kind, _ in METRICS:
        if k == "records":
            continue
        a, b = now[k], old[k]
        if k in ("size_qty", "quality_qty") and not compatible(
            parameters, previous_parameters, "size" if k == "size_qty" else "quality"
        ):
            b = None
        result.append(
            dict(
                id=k,
                cells=dict(
                    type=cell("总览"),
                    object=cell(label),
                    current=cell(comparison_text(a, kind)),
                    previous=cell(comparison_text(b, kind)),
                    change=cell(change_text(a, b, kind)),
                    rate_change=cell(None),
                    reasons=cell(items=[]),
                ),
            )
        )
    start = (q.page - 1) * q.size
    stop = start + q.size
    total = len(result)
    result = result[start:stop]
    for level in ("category", "style"):
        curr = {str(x["key"]): x for x in groups(current_blocks, level, parameters)}
        prev = {str(x["key"]): x for x in groups(previous_blocks, level, previous_parameters)}
        keys = sorted(set(curr) | set(prev))
        for key in keys[max(0, start - total) : max(0, stop - total)]:
            a = curr.get(key)
            b = prev.get(key)
            am = a["metrics"]["inbound_amount"] if a else None
            bm = b["metrics"]["inbound_amount"] if b else None
            ar = a["metrics"]["total_rate"] if a else None
            br = b["metrics"]["total_rate"] if b else None
            result.append(
                dict(
                    id=row_id((level, key)),
                    cells=dict(
                        type=cell("品类" if level == "category" else "单款"),
                        object=cell(key),
                        current=cell(comparison_text(am, "currency")),
                        previous=cell(comparison_text(bm, "currency")),
                        change=cell(change_text(am, bm, "currency")),
                        rate_change=cell(
                            (ar - br) * 100 if ar is not None and br is not None else None
                        ),
                        reasons=cell(items=reasons(a["roles"][0], parameters)[:5] if a else []),
                    ),
                )
            )
        total += len(keys)
    return (
        result,
        total,
        f"对比 {previous['period_start']} 至 {previous['period_end']}。"
        + ("两份周报都是滚动 30 天。" if rolling_weeks else "")
        + "实际入库金额按同品类、同品类下的同款号比较。算不出的项显示 —。",
    )


def analysis(repo, q):
    parameters = repo.classification_parameters()
    columns = {
        "category": CATEGORY,
        "style": STYLE,
        "mom": COMPARISON,
        "yoy": COMPARISON,
        "quality": QUALITY,
        "history": HISTORY,
    }[q.view]
    if q.view in ("category", "style"):
        note = (
            "只有销售记录时，退货数据显示 —；已有退货记录里的空白金额和数量按 0 计"
            if repo.is_rpa()
            else "只有销售记录时，退货数据显示 —"
        )
        rows, total = object_rows(repo, q, q.view)
    elif q.view in ("mom", "yoy"):
        rows, total, note = comparison(repo, q)
        if q.view == "yoy":
            columns = [(k, "去年同期" if k == "previous" else label, t) for k, label, t in columns]
    elif q.view == "quality":
        returns = roles(repo, q)[0]
        issues = observations(returns, parameters)
        rows = [
            dict(id=row_id(r["name"]), cells={k: cell(v) for k, v in r.items()})
            for r in issues["quality_rows"]
        ]
        note = (
            "按退货原因归类，占比按当前筛选的实退总件数计算。"
            "同一记录可能归入多个细类，细类数量不能直接相加。"
        )
        if rows and any(
            item["qty"] is None or item["share"] is None for item in issues["quality_rows"]
        ):
            note += "缺数量或实退总件数为 0 时，算不出的项显示 —。"
        if not rows:
            if not repo.report:
                note = "当前没有可用的报告。"
            elif not enabled(parameters, "quality"):
                note = "这份报告暂时无法按质量问题分类。"
            elif returns is None:
                note = "缺少退货数据，暂时无法统计质量问题。"
            elif not returns:
                note = "当前筛选没有退货记录。"
            elif issues["quality_qty"] is None:
                note = "缺少可用的退货原因或数量，暂时无法统计。"
            else:
                note = "当前筛选的退货原因里没有识别到质量问题。"
    else:
        rows = []
        note = "这里展示整份报表的数据。重叠周期不能相加。"
        for r in history_reports(repo, q):
            m = report_totals(repo, r)
            pr = public_report(r)
            cells = {k: cell(v) for k, v in m.items()}
            cells.update(
                label=cell(pr["label"]),
                type=cell(r["report_type"]),
                period=cell(f"{r['period_start']}—{r['period_end']}"),
                generated_at=cell(pr["generated_at"]),
            )
            rows.append(dict(id=r["id"], cells=cells))
    if q.view in ("quality", "history"):
        total = len(rows)
        rows = rows[(q.page - 1) * q.size : q.page * q.size]
    pending_focus = q.focus != "all" and q.view != "history" and not enabled(parameters, q.focus)
    if pending_focus and repo.report and (q.view not in ("mom", "yoy") or total):
        focus = "尺码" if q.focus == "size" else "质量"
        note = f"这份报告暂时无法按{focus}问题筛选。"
    status = "rule_pending" if pending_focus else "observed_reference" if total else "unavailable"
    return dict(
        status=status,
        scope_note=note,
        columns=[dict(key=k, label=label, type=t) for k, label, t in columns],
        rows=rows,
        total=total,
        page=q.page,
        size=q.size,
    )
