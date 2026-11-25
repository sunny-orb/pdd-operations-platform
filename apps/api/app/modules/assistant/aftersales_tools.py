"""助手售后只读投影：复用固定报告与领域口径，结果有界且不猜跨页身份。"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.modules.aftersales.analysis import analysis
from app.modules.aftersales.query import AfterQuery, options, overview
from app.modules.aftersales.repository import BoardError, public_report, snapshot
from app.modules.aftersales.targets import add_target_ranges

from .business import fact, report, table

CAPABILITIES = ("aftersales_overview", "aftersales_analysis", "aftersales_options")
VIEWS = {"category", "style", "mom", "yoy", "quality", "history"}
FOCUSES = {"all", "size", "quality"}
UNITS = {"currency", "number", "percent", "ratio", "text"}
BOUNDARY = (
    "售后按固定报告实际期间查询；确认期间与销售支付期间不是同一批订单。",
    "售后款式/SKU尚无店铺商品链接映射，不能按同名推定某店商品的品质或退款归因。",
    "报告可能为滚动30天，重叠期间不能相加；完整截至日未确认。",
    "规则建议不代表已确认根因、已指派负责人或实际整改进度。",
)


def _parameters(params):
    """只接收售后支持的报告/筛选；拒绝商品日期与身份，防止静默扩大范围。"""
    allowed = {
        "data_version", "report_id", "category", "keyword", "focus", "view", "limit", "offset"
    }
    if not isinstance(params, dict) or set(params) - allowed:
        raise BoardError("INVALID_SCOPE")
    limit, offset = params.get("limit", 10), params.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 25 or type(offset) is not int or offset < 0:
        raise BoardError("INVALID_SCOPE")
    for key, maximum in (("data_version", 128), ("report_id", 200), ("category", 200),
                         ("keyword", 120), ("focus", 20), ("view", 20)):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise BoardError("INVALID_SCOPE")
    focus, view = params.get("focus", "all"), params.get("view", "category")
    if focus not in FOCUSES or view not in VIEWS:
        raise BoardError("INVALID_SCOPE")
    q = AfterQuery(
        report_id=params.get("report_id") or None,
        category=params.get("category") or "",
        keyword=params.get("keyword") or "",
        focus=focus,
        view=view,
        page=offset // 25 + 1,
        size=25,
    )
    return q, limit, offset


def _catalog(repo, q, *, limit=25, offset=0, keyword=""):
    """完整目录先筛后切片，目录仅包含真实已发布报告，保留旧来源/修订供定位。"""
    values = options(repo, q)
    reports = [
        dict(
            id=item["id"], label=item["label"], date_from=item["period_start"],
            date_to=item["period_end"], report_type=item["report_type"],
        )
        for item in (public_report(r) for r in repo.reports)
    ]
    categories = values["categories"]
    if keyword:
        needle = keyword.casefold()
        reports = [r for r in reports if needle in " ".join(r.values()).casefold()]
        categories = [c for c in categories if needle in c.casefold()]
    chosen = public_report(repo.report)
    return dict(
        domain="aftersales", data_version=repo.token,
        date_from=chosen["period_start"] if chosen else None,
        date_to=chosen["period_end"] if chosen else None,
        default_report_id=repo.defaults[0]["id"] if repo.defaults else None,
        selected_report_id=chosen["id"] if chosen else None,
        reports=reports[offset:offset + limit], reports_total=len(reports),
        categories=categories[offset:offset + limit], categories_total=len(categories),
        limit=limit, offset=offset, views=sorted(VIEWS), focuses=sorted(FOCUSES),
        capabilities=list(CAPABILITIES), notes=[values["note"], *BOUNDARY],
    )


def describe(engine, domain="aftersales"):
    """发现默认报告真实期间及最多25项报告/品类；更多目录通过options分页。"""
    if domain != "aftersales":
        raise BoardError("INVALID_SCOPE")
    with snapshot(engine) as repo:
        return _catalog(repo, AfterQuery())


def _scalar(value, unit="text"):
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is not None and unit in {"number", "currency", "percent", "ratio"}:
        return format(Decimal(str(value)), "f")
    return value


def _unit(unit):
    return unit if unit in UNITS else "number" if unit == "points" else "text"


def _columns(columns):
    return [
        (c["key"], ("平台封版时间" if c["key"] == "generated_at" else c["label"])
         + ("（百分点）" if c["type"] == "points" else ""),
         _unit(c["type"]))
        for c in columns
    ]


def _items_text(items):
    """原因/尺码/质量子项转为有限行内文字，保留数量与比例而不交给模型嵌套行集。"""
    result = []
    for item in items:
        parts = []
        for key, label in (("name", ""), ("qty", "数量="), ("share", "占比（原比例）=")):
            if key in item:
                value = item[key]
                parts.append(label + (str(_scalar(value)) if value is not None else "未提供"))
        result.append("，".join(parts))
    return "；".join(result)


def _analysis_table(repo, q, limit, offset):
    """任意offset最多读取两个25项领域页；总体计算始终由领域先完成再分页。"""
    data = analysis(repo, q)
    rows = data["rows"]
    start = offset % 25
    if start + limit > 25 and q.page * 25 < data["total"]:
        next_query = AfterQuery(**{**q.__dict__, "page": q.page + 1})
        rows = rows + analysis(repo, next_query)["rows"]
    selected = rows[start:start + limit]
    columns = _columns(data["columns"])
    notes = [data["scope_note"], "分析状态：" + data["status"]]
    flattened = []
    for row in selected:
        values = {"id": row["id"]}
        details = []
        for key, label, unit in columns:
            cell = row["cells"].get(key, {})
            values[key] = (
                _items_text(cell["items"]) if "items" in cell else _scalar(cell.get("value"), unit)
            )
            if cell.get("detail"):
                details.append(label + "：" + cell["detail"])
        if details:
            notes.append(f"明细{row['id']}：" + "；".join(details))
        flattened.append(values)
    return table(
        "售后分析明细", columns, flattened, total=data["total"], offset=offset
    ), notes


def _bounded_table(title, columns, rows, limit, offset):
    chosen = rows[offset:offset + limit]
    units = dict((key, unit) for key, _, unit in columns)
    return table(
        title, columns,
        [{key: _scalar(row.get(key), unit) for key, unit in units.items()} for row in chosen],
        total=len(rows), offset=offset,
    )


def _point_comparison(gap, reference):
    """领域差值已是本期减参照的百分点，按未舍入符号确定方向。"""
    if gap == 0:
        return f"等于{reference}"
    direction = "高于" if gap > 0 else "低于"
    with localcontext(rounding=ROUND_HALF_UP):
        magnitude = format(gap.copy_abs(), ".2f")
    if magnitude == "0.00":
        return f"{direction}{reference}，相差不足 0.01 个百分点"
    return f"{direction}{reference} {magnitude} 个百分点"


def _target_comparison(target):
    """把固定报告的区间状态与上下界差值直接表述为可引用的中文结论。"""
    label, state = target["label"], target["state"]
    if state == "scope_not_applicable":
        return "整体目标只适用于未筛选的滚动30天或完整自然月报告，当前范围不适用。"
    bounds = f"{target['lower_rate']:%}–{target['upper_rate']:%}"
    if state == "metric_unavailable":
        return f"{label}实际值不可用，无法与目标区间（{bounds}）比较。"
    position = {
        "within_range": "位于目标区间内",
        "above_range": "高于目标区间",
        "below_range": "低于目标区间",
    }[state]
    lower = _point_comparison(target["lower_gap_pp"], "下界")
    upper = _point_comparison(target["upper_gap_pp"], "上界")
    return f"{label}{position}（{bounds}），{lower}，{upper}。"


def _overview(repo, q, limit, offset):
    data = add_target_ranges(repo, q, overview(repo, q))
    targets = [dict(row, comparison=_target_comparison(row)) for row in data["target_ranges"]]
    total_target = next(
        (row["comparison"] for row in targets if row["metric_code"] == "total_refund_rate"),
        "本报告的总退款率没有可比较的整体目标。",
    )
    facts, notes, tables = [], [data["scope_note"], data["quality_note"]], []
    for prefix, metrics in (("", data["metrics"]), ("risk_", data["risk_metrics"])):
        for item in metrics:
            key = prefix + item["key"]
            unit = _unit(item["type"])
            label = item["label"]
            reason = "；".join(filter(None, (item.get("note"), item.get("detail"))))
            if key == "risk_target_gap":
                label, reason = "总退款率减整体目标上界", total_target
            elif key == "risk_change_pp":
                label = "总退款率较上期变化"
                if item["value"] is not None:
                    reason = "本期总退款率" + _point_comparison(item["value"], "上期") + "。"
            if item["type"] == "points":
                label += "（百分点）"
            facts.append(fact(
                key, label, _scalar(item["value"], unit), unit,
                status=item["status"], reason=reason,
            ))
    decision = data["decision"]
    facts.extend([
        fact("decision", "重点核查", decision["title"], reason=decision["note"],
             status=decision["status"]),
        fact("decision_status", "核查状态", decision["status"]),
    ])
    for key, label, unit in (
        ("rate", "重点对象总退款率", "percent"),
        ("amount", "重点对象实际入库金额", "currency"),
        ("amount_share", "重点对象金额占比", "percent"),
        ("change_pp", "重点对象变化（百分点）", "number"),
    ):
        value = decision.get(key)
        facts.append(fact(
            "decision_" + key, label, _scalar(value, unit), unit,
            status=decision["status"] if value is not None else "unavailable",
            reason=decision["note"],
        ))
    definitions = [
        ("退款原因", [("name", "原因", "text"), ("qty", "实退数量", "number"),
                  ("share", "占比", "percent")], data["reasons"]),
        ("尺码拆分", [("name", "尺码方向", "text"), ("qty", "实退数量", "number"),
                  ("share", "占比", "percent")], data["size_breakdown"]),
        ("退款阶段", [("name", "阶段", "text"), ("amount", "金额", "currency"),
                  ("qty", "实退数量", "number"), ("share", "占比", "percent"),
                  ("trend", "趋势状态", "text"),
                  ("status", "状态", "text"), ("note", "依据", "text")], data["stages"]),
        ("历史报告趋势（整体）", [("id", "报告ID", "text"), ("label", "报告", "text"),
                  ("period_start", "开始日期", "text"), ("period_end", "结束日期", "text"),
                  ("qty", "整体实退数量", "number")], data["trend"]),
        ("规则建议", [("id", "对象键", "text"), ("status", "规则状态", "text"),
                  ("object", "对象", "text"), ("amount", "影响金额", "currency"),
                  ("risk_score", "本层排序分", "number"), ("hypothesis", "待核假设", "text"),
                  ("action", "建议动作", "text"), ("verification", "验证指标", "text"),
                  ("previous_rate", "上期退款率", "percent"),
                  ("current_rate", "当前退款率", "percent"), ("note", "依据", "text")],
         data["actions"]),
        ("整体目标区间", [("label", "指标", "text"), ("lower_rate", "目标下界", "percent"),
                  ("upper_rate", "目标上界", "percent"), ("current", "本期", "percent"),
                  ("lower_gap_pp", "本期减下界（百分点）", "number"),
                  ("upper_gap_pp", "本期减上界（百分点）", "number"), ("state", "区间状态", "text"),
                  ("comparison", "实际值与整体目标的比较", "text"),
                  ("version_id", "目标版本", "text")], targets),
        ("优先行动", [("id", "建议键", "text"), ("kind", "类型", "text"),
                  ("title", "对象", "text"), ("detail", "建议", "text"),
                  ("verification", "验证指标", "text")], data["priorities"]),
    ]
    for level, label in (("category", "品类"), ("style", "单款")):
        definitions.append((
            label + "风险排序", [("id", "对象键", "text"), ("name", "对象", "text"),
                  ("amount", "实际入库金额", "currency"),
                  ("amount_share", "占当前筛选入库金额", "percent"),
                  ("total_refund_rate", "总退款率", "percent"),
                  ("change_pp", "变化（百分点）", "number"),
                  ("risk_score", "本层排序分", "number"), ("note", "依据", "text")],
            data[level + "_risks"],
        ))
        definitions.append((
            label + "优先核查建议", [("id", "建议键", "text"), ("kind", "类型", "text"),
                  ("title", "对象", "text"), ("detail", "建议", "text"),
                  ("verification", "验证指标", "text")], data[level + "_actions"],
        ))
    for title, columns, rows in definitions:
        tables.append(_bounded_table(title, columns, rows, limit, offset))
    notes.extend(item["title"] + "：" + item["detail"] for item in data["core_insights"])
    return facts, tables, notes


def read(engine, capability, params):
    """读取报告/筛选内的完整汇总或有界分析；不读取上游、不写库、不重算业务规则。"""
    if capability not in CAPABILITIES:
        raise BoardError("INVALID_SCOPE")
    q, limit, offset = _parameters(params)
    with snapshot(engine, q.report_id, params.get("data_version")) as repo:
        chosen = public_report(repo.report)
        period = (
            dict(date_from=chosen["period_start"], date_to=chosen["period_end"])
            if chosen else None
        )
        scope = dict(
            report_id=chosen["id"] if chosen else None,
            report_label=chosen["label"] if chosen else None,
            report_type=chosen["report_type"] if chosen else None,
            rule_set_id=repo.report["rule_set_id"] if repo.report else None,
            category=q.category, keyword=q.keyword, focus=q.focus, view=q.view,
            limit=limit, offset=offset,
        )
        notes = list(BOUNDARY)
        facts, tables = [], []
        if capability == "aftersales_options":
            catalog = _catalog(repo, q, limit=limit, offset=offset, keyword=q.keyword)
            tables = [
                table("报告目录", [("id", "报告ID", "text"), ("label", "报告", "text"),
                    ("date_from", "开始日期", "text"), ("date_to", "结束日期", "text"),
                    ("report_type", "报告类型", "text")], catalog["reports"],
                    total=catalog["reports_total"], offset=offset),
                table("品类目录", [("category", "品类", "text")],
                    [{"category": c} for c in catalog["categories"]],
                    total=catalog["categories_total"], offset=offset),
            ]
        elif capability == "aftersales_analysis":
            result, details = _analysis_table(repo, q, limit, offset)
            tables.append(result)
            notes.extend(details)
        else:
            facts, tables, details = _overview(repo, q, limit, offset)
            notes.extend(details)
        meta = repo.metadata()
        if chosen is None:
            notes.append("当前没有可用的已发布售后报告，不将缺报告解释为零。")
        else:
            for prefix, label in (("previous", "上期"), ("yoy", "去年同期")):
                state = repo.report[prefix + "_comparison_status"]
                reason = repo.report[prefix + "_comparison_reason"]
                notes.append(label + "比较状态：" + state + ("；" + reason if reason else ""))
        for source in meta["sources"]:
            notes.append(source["label"] + "：" + source["status"] + "；" + source["note"])
        tables.append(_bounded_table(
            "报告来源与覆盖", [("label", "来源", "text"), ("status", "状态", "text"),
                ("observed_from", "实际观察开始", "text"),
                ("observed_to", "实际观察截至", "text"),
                ("last_success_at", "本输入版最近成功", "text"),
                ("last_attempt_at", "最近尝试", "text"), ("error_code", "错误码", "text"),
                ("note", "依据", "text")], meta["sources"], limit, offset,
        ))
        source = "RPA售后固定期间报表" if repo.is_rpa() else "聚水潭售后固定期间报告"
        return report(
            "aftersales", capability, chosen["label"] if chosen else "售后分析", source,
            repo.token, period=period, scope=scope, facts=facts, tables=tables, notes=notes,
        )
