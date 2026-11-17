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


def object_rows(repo, q, level):
    blocks = roles(repo, q)
    parameters = repo.classification_parameters()
    whole = summarize(*blocks, parameters=parameters)
    all_values = groups(blocks, level, parameters)
    order_field = "inbound_amount" if whole["inbound_amount"] is not None else "returned_qty"
    all_values.sort(key=lambda r: (-(r["metrics"][order_field] or Decimal(0)), str(r["key"])))
    result = []
    for rank, row in enumerate(all_values, 1):
        m = row["metrics"]
        e = row["example"]
        issues = observations(row["roles"][0], parameters)
        cells = {
            k: cell(
                m.get(k),
                "缺角色/缺记录/缺金额或组键不相容时不可计算。" if m.get(k) is None else None,
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
            style_code=cell(e.get("source_style_code"), "保留来源原款码，不按商品编码猜测。"),
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
                )
                or "分组所需字段未齐，分母待核。",
            ),
            qty_share=cell(ratio(m["returned_qty"], whole["returned_qty"])),
            amount_share=cell(ratio(m["inbound_amount"], whole["inbound_amount"])),
            reasons=cell(items=reasons(row["roles"][0], parameters)[:5]),
            size=cell(items=issues["size_rows"], detail=issues["note"]),
            quality=cell(items=issues["quality_rows"], detail=issues["note"]),
        )
        result.append(dict(id=row_id(row["key"]), cells=cells))
    return result


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
        return f"{absolute}；相对变化不可比"
    return f"{absolute}；{difference / previous * 100:+.2f}%"


def comparison(repo, q):
    r = repo.report
    if not r:
        return [], "尚无可用报告，无法比较。"
    prefix = "yoy" if q.view == "yoy" else "previous"
    previous = repo.by_id.get(r[prefix + "_report_id"])
    if r[prefix + "_comparison_status"] != "available" or previous is None:
        return [], r[prefix + "_comparison_reason"] or "比较报告不可用。"
    parameters = repo.classification_parameters()
    previous_parameters = repo.classification_parameters(previous)
    now = summarize(*roles(repo, q), parameters=parameters)
    old = summarize(*roles(repo, q, previous), parameters=previous_parameters)
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
    for level in ("category", "style"):
        curr = {str(x["key"]): x for x in groups(roles(repo, q), level, parameters)}
        prev = {
            str(x["key"]): x for x in groups(roles(repo, q, previous), level, previous_parameters)
        }
        for key in sorted(set(curr) | set(prev)):
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
    return (
        result,
        f"比较实际期间 {previous['period_start']}—{previous['period_end']}，"
        "完整组键配对；缺行不推断零。",
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
        rows = object_rows(repo, q, q.view)
    elif q.view in ("mom", "yoy"):
        rows, note = comparison(repo, q)
        if q.view == "yoy":
            columns = [(k, "去年同期" if k == "previous" else label, t) for k, label, t in columns]
    elif q.view == "quality":
        issues = observations(roles(repo, q)[0], parameters)
        rows = [
            dict(id=row_id(r["name"]), cells={k: cell(v) for k, v in r.items()})
            for r in issues["quality_rows"]
        ]
        note = issues["note"]
    else:
        rows = []
        note = "整份报告范围；品类、关键词、问题重点不改变历史报告总体，不把重叠期间相加。"
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
    pending_focus = q.focus != "all" and q.view != "history" and not enabled(parameters, q.focus)
    if pending_focus:
        note = "尺码/质量分类规则未核，此重点筛选不可计算；空列表不表示无问题。 " + note
    return dict(
        status="rule_pending" if pending_focus else "observed_reference" if rows else "unavailable",
        scope_note=note,
        columns=[dict(key=k, label=label, type=t) for k, label, t in columns],
        rows=rows[(q.page - 1) * q.size : q.page * q.size],
        total=len(rows),
        page=q.page,
        size=q.size,
    )
