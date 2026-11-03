"""报告固定v1词典的原原因观察；有限字面匹配不冒充完整语义或物流事实。"""

from decimal import Decimal, localcontext

from .rowset import record_count

EVIDENCE = "docs/source-evidence.md#m5-live-sources-20261028"
SIZE_LABELS = {
    "larger": "尺码偏大",
    "smaller": "尺码偏小",
    "unspecified": "尺码不合适未说明",
}
QUALITY_LABELS = {
    "quality": "质量问题/污渍等",
    "workmanship": "做工瑕疵",
    "damage": "破损/破洞/开裂",
}
DICTIONARIES = {
    "size_issue": ("size_dictionary", "single_or_conflict", set(SIZE_LABELS)),
    "quality_issue": ("quality_dictionary", "collect_distinct_labels", set(QUALITY_LABELS)),
    "primary_reason": (
        "primary_reason",
        "first_ordered_match",
        {"size", "quality", "product_mismatch", "logistics", "price", "style", "original_reason"},
    ),
    "refusal_clue": ("refusal_dictionary", "collect_distinct_labels", {"refusal_clue"}),
}


def rule_parameters(*, targets_available=False):
    """新报告冻结的完整v1包；旧报告从自身FK重读，不调用此函数补默认词典。"""
    size = [
        ("larger", ["偏大", "太大", "买大", "码大", "大了"], []),
        ("smaller", ["偏小", "太小", "买小", "码小", "小了", "太紧", "夹脚", "顶脚"], []),
        (
            "unspecified",
            ["尺码不合适", "尺寸不合适", "大小不合适", "尺码没选对", "尺码拍错"],
            [],
        ),
    ]
    quality = [
        (
            "quality",
            ["质量", "污渍", "脏污", "异味", "掉色", "褪色", "起球", "掉毛", "色差"],
            ["不是质量", "非质量", "无质量问题", "没有质量问题"],
        ),
        (
            "workmanship",
            ["做工", "瑕疵", "脱线", "开线", "线头", "缝线", "拉链", "纽扣", "胶水", "压痕"],
            ["没有瑕疵", "无瑕疵", "不是做工问题"],
        ),
        ("damage", ["破损", "破洞", "开裂", "撕裂", "坏了"], ["没有破损", "无破损", "未破损"]),
    ]

    def entries(code, groups):
        result = []
        for output, terms, negative in groups:
            for term in terms:
                order = len(result) + 1
                result.append(
                    dict(
                        entry_code=f"{code}_{order}",
                        order=order,
                        term=term,
                        output_code=output,
                        negative_terms=list(negative),
                    )
                )
        return result

    primary = [
        ("size", [term for _, terms, _ in size for term in terms], []),
        ("quality", [term for _, terms, _ in quality for term in terms], ["不是质量", "非质量"]),
        ("product_mismatch", ["描述不符", "发错", "错发", "少发", "漏发", "货不对版"], []),
        ("logistics", ["物流", "拒收", "快递", "未收到", "派送", "丢件"], []),
        ("price", ["价格", "价保", "降价", "买贵", "差价"], []),
        ("style", ["款式", "颜色", "不好看", "不喜欢", "版型", "图案"], []),
    ]
    enabled = {"quality_quantity", "primary_reason", "size_dictionary", "quality_dictionary"}
    if targets_available:
        enabled.add("target_application")
    thresholds = {
        "refund_rate_change": dict(
            attention_change_gte="0.03",
            warning_change_gte="0.05",
            current_sales_denominator_min="5000",
            previous_sales_denominator_min="5000",
            current_refund_numerator_min="1000",
        ),
        "issue_share_change": dict(
            attention_change_gte="0.03",
            warning_change_gte="0.05",
            current_return_qty_min="30",
            previous_return_qty_min="30",
            current_issue_qty_min="10",
        ),
        "single_style_amount_review": dict(top_limit=10, priority="review"),
    }
    branch_codes = [
        "refund_rate_change",
        "issue_share_change",
        "single_style_amount_review",
        "mixed_risk_ranking",
        "quality_quantity",
        "target_application",
        "primary_reason",
        "size_dictionary",
        "quality_dictionary",
        "refusal_dictionary",
    ]
    templates = {
        "quality_quantity": "质量任一标签命中时每原行Q只计一次；细标签可以重叠，不相加当总量。",
        "primary_reason": "保留非空原退货原因；缺失且无已核辅助文本时留未填写，不从商品名推断。",
        "size_dictionary": "原原因字面识别仅为观察；方向冲突留未说明，先核尺码说明和真实退货反馈。",
        "quality_dictionary": (
            "原原因字面识别仅为观察；结合真实反馈复核做工和破损，不据标签编造根因。"
        ),
    }
    if targets_available:
        templates["target_application"] = (
            "总体金额区间目标分别保留上下界；仅适用已绑定目标的整体滚动30日/自然月报告，"
            "不替代细类目标，不将观察值称业务已达标。"
        )
    branches = [
        dict(
            code=code,
            enabled=code in enabled,
            confirmation_status="pending_confirmation"
            if code == "target_application" and not targets_available
            else "confirmed",
            validation_status="verified_in_scope" if code in enabled else "pending_evidence",
            contract_ref="docs/data-contract.md#business-metric-questions",
            evidence_ref=(
                "docs/data-contract.md#metric-m5-03" if code == "target_application" else EVIDENCE
            )
            if code in enabled
            else None,
            depends_on_question_codes=[],
            predicate_code=code,
            thresholds=thresholds.get(code, {}),
            template_code="tpl_" + code if code in enabled else None,
        )
        for code in branch_codes
    ]

    def dictionary(code, branch, mode, groups, active=True):
        return dict(
            dictionary_code=code,
            enabled=active,
            validation_status="verified_in_scope" if active else "pending_evidence",
            field_codes=["source_return_reason"] if active else [],
            entries=entries(code, groups) if active else [],
            match_mode="literal_substring",
            conflict_policy_code=mode,
            branch_code=branch,
            evidence_ref=EVIDENCE if active else None,
        )

    return dict(
        policy=dict(
            comparison_policy_code="frozen_report_periods",
            grouping_policy_code="full_source_groups_unique_denominator",
        ),
        branches=branches,
        dictionaries=[
            dictionary("size_issue", "size_dictionary", "single_or_conflict", size),
            dictionary("quality_issue", "quality_dictionary", "collect_distinct_labels", quality),
            dictionary("primary_reason", "primary_reason", "first_ordered_match", primary),
            dictionary("refusal_clue", "refusal_dictionary", "collect_distinct_labels", [], False),
        ],
        suggestions=[
            dict(
                template_code="tpl_" + code,
                branch_codes=[code],
                text=text,
                verification_metric_codes=[],
                execution_mode="display_only",
            )
            for code, text in templates.items()
        ],
    )


class PreparedParameters(dict):
    """一个报告读取快照内的规则副本；词典校验只做一次，不跨请求共享。"""

    def __init__(self, parameters):
        from copy import deepcopy

        raw = deepcopy(parameters)
        super().__init__(raw)
        self.dictionaries = {code: dictionary(raw, code) for code in DICTIONARIES}


def branch_enabled(parameters, code):
    if not isinstance(parameters, dict):
        return False
    branches = [b for b in parameters.get("branches", []) if b.get("code") == code]
    return bool(
        len(branches) == 1
        and branches[0].get("enabled")
        and branches[0].get("validation_status") == "verified_in_scope"
        and branches[0].get("confirmation_status") == "confirmed"
        and branches[0].get("evidence_ref")
    )


def dictionary(parameters, code):
    """只启用报告包中已验且字段封闭的词典；旧空包或不完整包不补当前默认值。"""
    if isinstance(parameters, PreparedParameters):
        return parameters.dictionaries.get(code)
    if not isinstance(parameters, dict):
        return None
    candidates = [d for d in parameters.get("dictionaries", []) if d.get("dictionary_code") == code]
    if len(candidates) != 1:
        return None
    d = candidates[0]
    branch, conflict, outputs = DICTIONARIES[code]
    if (
        not branch_enabled(parameters, branch)
        or d.get("branch_code") != branch
        or d.get("conflict_policy_code") != conflict
        or not d.get("enabled")
        or d.get("validation_status") != "verified_in_scope"
        or not d.get("evidence_ref")
        or d.get("field_codes") != ["source_return_reason"]
        or d.get("match_mode") not in ("literal_substring", "exact_text")
        or not d.get("entries")
    ):
        return None
    codes = set()
    for order, entry in enumerate(sorted(d["entries"], key=lambda e: e.get("order", 0)), 1):
        if (
            set(entry) != {"entry_code", "order", "term", "output_code", "negative_terms"}
            or entry["order"] != order
            or isinstance(entry["order"], bool)
            or not isinstance(entry["entry_code"], str)
            or not entry["entry_code"]
            or entry["entry_code"] in codes
            or not isinstance(entry["term"], str)
            or not entry["term"]
            or entry["output_code"] not in outputs
            or not isinstance(entry["negative_terms"], list)
            or any(not isinstance(t, str) or not t for t in entry["negative_terms"])
        ):
            return None
        codes.add(entry["entry_code"])
    return d


def enabled(parameters, focus):
    ready = dictionary(parameters, "size_issue" if focus == "size" else "quality_issue") is not None
    return ready and (focus == "size" or branch_enabled(parameters, "quality_quantity"))


def compatible(parameters, previous, focus):
    code = "size_issue" if focus == "size" else "quality_issue"
    return bool(
        enabled(parameters, focus)
        and enabled(previous, focus)
        and dictionary(parameters, code) == dictionary(previous, code)
    )


def match_entries(d, text):
    """按固定entries和同字段否定词匹配，不分词、不正则、不扩展同义词。"""
    if not d or not text:
        return []
    outputs = []
    for entry in sorted(d["entries"], key=lambda e: e["order"]):
        term = entry["term"]
        hit = text == term if d["match_mode"] == "exact_text" else term in text
        if hit and not any(negative in text for negative in entry["negative_terms"]):
            if entry["output_code"] not in outputs:
                outputs.append(entry["output_code"])
            if d["conflict_policy_code"] == "first_ordered_match":
                break
    return outputs


def classify_rows(rows, parameters):
    """同请求按原原因复用字面结果；保留每个原行供Q去重，不合并事实行。"""
    size = dictionary(parameters, "size_issue")
    quality = dictionary(parameters, "quality_issue")
    primary = dictionary(parameters, "primary_reason")
    cache = {}
    for row in rows:
        raw = row.get("source_return_reason")
        text = raw if isinstance(raw, str) and raw.strip() else ""
        if text not in cache:
            sizes = match_entries(size, text)
            qualities = match_entries(quality, text)
            fallback = match_entries(primary, text) if not text else []
            cache[text] = dict(
                primary_reason=text or (fallback[0] if fallback else "未填写原因"),
                size="conflict" if len(sizes) > 1 else sizes[0] if sizes else None,
                quality=qualities,
                has_text=bool(text),
            )
        yield row, cache[text]


def classify(row, parameters):
    return next(classify_rows([row], parameters))[1]


def quantity(rows):
    if not rows or any(r.get("actual_returned_qty") is None for r in rows):
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return sum((r["actual_returned_qty"] for r in rows), Decimal(0))


def share(a, b):
    with localcontext() as ctx:
        ctx.prec = 50
        return a / b if a is not None and b is not None and b > 0 else None


def observations(rows, parameters):
    """原原因可见覆盖内的Q观察量；质量总量按原行去重，细标签份额统一以全部Q为分母。"""
    sizes = {code: [] for code in SIZE_LABELS}
    qualities = {code: [] for code in QUALITY_LABELS}
    size_rows, quality_rows = [], []
    missing, conflicts, text_rows = 0, 0, 0
    for row, result in classify_rows(rows or [], parameters):
        if result["has_text"]:
            text_rows += row.get("_record_count", 1)
        else:
            missing += row.get("_record_count", 1)
        if result["size"]:
            size_rows.append(row)
            code = result["size"]
            if code == "conflict":
                code = "unspecified"
                conflicts += row.get("_record_count", 1)
            sizes[code].append(row)
        if result["quality"]:
            quality_rows.append(row)
            for code in result["quality"]:
                qualities[code].append(row)
    denominator = quantity(rows)
    size_enabled, quality_enabled = enabled(parameters, "size"), enabled(parameters, "quality")

    def observed_qty(selected, active):
        if not active or not text_rows or denominator is None:
            return None
        return quantity(selected) if selected else Decimal(0)

    return dict(
        size_qty=observed_qty(size_rows, size_enabled),
        quality_qty=observed_qty(quality_rows, quality_enabled),
        size_rows=[
            dict(
                name=label,
                qty=(q := observed_qty(sizes[code], size_enabled)),
                share=share(q, denominator),
            )
            for code, label in SIZE_LABELS.items()
        ],
        quality_rows=[
            dict(name=label, qty=(q := quantity(qualities[code])), share=share(q, denominator))
            for code, label in QUALITY_LABELS.items()
            if qualities[code]
        ],
        note=(
            "已接原原因文本识别的观察量，有限词典不代表完整语义；"
            f"未填写原因{missing}/{record_count(rows)}行，当前无已核辅助备注，覆盖不足不表示无问题。"
            "质量任一标签命中则每原行Q只计一次，细标签可重叠、合计不是质量总量；"
            f"尺码方向冲突{conflicts}行归未说明，份额以当前范围全部实退Q为分母。"
            if size_enabled or quality_enabled
            else "所选报告固定词典尚未验证；尺码/质量保留NULL，不用最新配置重解释旧报告。"
        ),
    )
