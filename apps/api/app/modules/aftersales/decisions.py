"""报告固定规则→同层核查顺序、SOP触发及建议；不保存或推断人工执行事实。"""

from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

from .classification import branch_enabled, classify, compatible, observations
from .metrics import groups, ratio, summarize

D = Decimal


def thresholds(parameters, code):
    return next(b["thresholds"] for b in parameters["branches"] if b["code"] == code)


def change(current, previous):
    return current - previous if current is not None and previous is not None else None


def meets_change(numerator, denominator, previous_numerator, previous_denominator, threshold):
    """用精确有理数比较阈值，避免循环小数把恰好3/5个百分点降级。"""
    delta = Fraction(numerator) / Fraction(denominator) - Fraction(previous_numerator) / Fraction(
        previous_denominator
    )
    return delta >= Fraction(D(threshold))


def trigger_status(current, previous, observed, prior_observed, parameters, previous_parameters):
    """各指标独立评级；缺前期或样本不足不阻断其他可成立触发。"""
    severity, evidence = 0, []
    if previous is None:
        return severity, ["缺少同口径上期，仍可按本期金额及原因核查"]
    if branch_enabled(parameters, "refund_rate_change"):
        t = thresholds(parameters, "refund_rate_change")
        for label, key, numerator, denominator in (
            ("售前退款率", "presale_rate", "presale_amount", "gmv"),
            ("售后退款率", "aftersale_rate", "inbound_amount", "shipped_amount"),
            ("总退款率", "total_rate", "total_amount", "gmv"),
        ):
            delta = change(current[key], previous[key])
            if delta is None:
                continue
            enough = (
                current[denominator] >= D(t["current_sales_denominator_min"])
                and previous[denominator] >= D(t["previous_sales_denominator_min"])
                and current[numerator] >= D(t["current_refund_numerator_min"])
            )
            if not enough:
                evidence.append(f"{label}样本不足，不评级")
            elif meets_change(
                current[numerator],
                current[denominator],
                previous[numerator],
                previous[denominator],
                t["attention_change_gte"],
            ):
                level = (
                    2
                    if meets_change(
                        current[numerator],
                        current[denominator],
                        previous[numerator],
                        previous[denominator],
                        t["warning_change_gte"],
                    )
                    else 1
                )
                severity = max(severity, level)
                evidence.append(f"{label}上升{delta * 100:.2f}个百分点")
    if branch_enabled(parameters, "issue_share_change"):
        t = thresholds(parameters, "issue_share_change")
        for kind, label in (("size", "尺码"), ("quality", "质量")):
            if not compatible(parameters, previous_parameters, kind):
                continue
            q, oq = current["returned_qty"], previous["returned_qty"]
            issue, old = observed[kind + "_qty"], prior_observed[kind + "_qty"]
            delta = change(ratio(issue, q), ratio(old, oq))
            if delta is None:
                continue
            if (
                q < D(t["current_return_qty_min"])
                or oq < D(t["previous_return_qty_min"])
                or issue < D(t["current_issue_qty_min"])
            ):
                evidence.append(f"{label}问题样本不足，不评级")
            elif meets_change(issue, q, old, oq, t["attention_change_gte"]):
                severity = max(
                    severity, 2 if meets_change(issue, q, old, oq, t["warning_change_gte"]) else 1
                )
                evidence.append(f"{label}问题占比上升{delta * 100:.2f}个百分点")
    return severity, evidence


def leading_reason(rows):
    quantities = {}
    for r in rows or []:
        reason = r.get("source_return_reason")
        qty = r.get("actual_returned_qty")
        if reason and qty is not None:
            quantities[reason] = quantities.get(reason, D(0)) + qty
    return min(quantities, key=lambda k: (-quantities[k], k)) if quantities else None


def advice_for(reason, parameters, templates):
    tags = classify({"source_return_reason": reason}, parameters)
    if tags["size"]:
        return templates["size"]
    if tags["quality"]:
        return templates["quality"]
    for kind, terms in (
        ("mismatch", ("不符", "发错", "错发")),
        ("logistics", ("物流", "拒收", "快递")),
    ):
        if any(term in (reason or "") for term in terms):
            return templates[kind]
    return templates["other"]


def select_priorities(items):
    """复用旧版尺码/质量各保留一项，再按同层优先值补足三项。"""
    items = sorted(items, key=lambda i: (-i["_priority"], i["id"]))
    selected = []
    for kind in ("尺码", "质量"):
        match = next((i for i in items if i["kind"] == kind), None)
        if match:
            selected.append(match)
    selected += [i for i in items if i not in selected][: 3 - len(selected)]
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in selected]


def decisions(blocks, previous_blocks, parameters, previous_parameters):
    if not branch_enabled(parameters, "separate_risk_ranking"):
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return _decisions(blocks, previous_blocks, parameters, previous_parameters)


def _decisions(blocks, previous_blocks, parameters, previous_parameters):
    overall = summarize(*blocks, parameters=parameters)
    config = thresholds(parameters, "separate_risk_ranking")
    templates, effect = config["templates"], config["effect_window"]
    baseline = max(overall["total_rate"] or D(0), D(config["baseline_floor"]))
    result, all_actions = {}, []
    for level, label in (("category", "品类"), ("style", "单款")):
        old = {str(g["key"]): g for g in groups(previous_blocks, level)} if previous_blocks else {}
        candidates = []
        for group in groups(blocks, level):
            m, key, e = group["metrics"], str(group["key"]), group["example"]
            previous = old.get(key)
            pm = previous["metrics"] if previous else None
            observed = observations(group["roles"][0], parameters)
            prior = observations(previous["roles"][0], previous_parameters) if previous else None
            severity, evidence = trigger_status(
                m, pm, observed, prior, parameters, previous_parameters
            )
            share = ratio(m["inbound_amount"], overall["inbound_amount"])
            delta = change(m["total_rate"], pm["total_rate"] if pm else None)
            raw = None
            if (
                share is not None
                and share >= D(config[level + "_min_share"])
                and m["inbound_amount"] > 0
                and m["total_rate"] is not None
                and m["returned_qty"] is not None
                and m["returned_qty"] >= 0
            ):
                excess = max(m["total_rate"] - baseline, D(0)) / baseline
                worsening = max(delta or D(0), D(0)) / baseline
                sample = min(m["returned_qty"] / D(config["sample_cap"]), D(1))
                raw = share * (1 + excess) * (1 + worsening) * (D(".5") + sample * D(".5"))
            name = (
                e.get("source_product_category") or "未分类"
                if level == "category"
                else " ".join(
                    filter(None, (e.get("source_style_code"), e.get("source_product_name")))
                )
                or "未提供款号"
            )
            candidates.append(
                dict(
                    id=level + ":" + key,
                    name=name,
                    metrics=m,
                    previous=pm,
                    observed=observed,
                    roles=group["roles"],
                    amount=m["inbound_amount"],
                    amount_share=share,
                    total_refund_rate=m["total_rate"],
                    change_pp=delta * 100 if delta is not None else None,
                    raw=raw,
                    severity=severity,
                    evidence=evidence,
                    risk_score=None,
                )
            )
        ranked = [i for i in candidates if i["raw"] is not None]
        maximum = max((i["raw"] for i in ranked), default=D(0))
        for item in ranked:
            item["risk_score"] = (
                (item["raw"] / maximum * 100).quantize(D(1), rounding=ROUND_HALF_UP)
                if maximum
                else D(0)
            )
        ranked.sort(key=lambda i: (-i["risk_score"], -i["amount"], i["id"]))
        result[level + "_risks"] = [
            dict(
                (k, i[k])
                for k in (
                    "id",
                    "name",
                    "amount",
                    "amount_share",
                    "total_refund_rate",
                    "change_pp",
                    "risk_score",
                )
            )
            | dict(note="本层相对排序分，不是预警级别；" + "；".join(i["evidence"]))
            for i in ranked[:5]
        ]
        top_amount = set()
        if level == "style" and branch_enabled(parameters, "single_style_amount_review"):
            top_amount = {
                i["id"]
                for i in sorted(
                    (i for i in candidates if i["amount"] is not None and i["amount"] > 0),
                    key=lambda i: (-i["amount"], i["id"]),
                )[: int(thresholds(parameters, "single_style_amount_review")["top_limit"])]
            }
        priorities = []
        for i in candidates:
            reason = leading_reason(i["roles"][0])
            issue_items = []
            for kind in ("size", "quality"):
                quantity = i["observed"][kind + "_qty"]
                if quantity is not None and quantity > 0:
                    title, advice, verification = templates[kind]
                    issue_items.append(
                        dict(
                            id=i["id"] + ":" + kind,
                            kind=title,
                            title=f"{title}：{label} {i['name']}",
                            detail=f"{title}问题{quantity:,f}件；建议{advice}",
                            verification=verification + "。" + effect,
                            _priority=quantity
                            * max(i["amount_share"] or D(0), D(".01"))
                            * (1 + max((i["change_pp"] or D(0)) / 100, D(0))),
                        )
                    )
            priorities.extend(issue_items)
            if not (i["raw"] is not None or i["severity"] or i["id"] in top_amount or issue_items):
                continue
            status = (
                "预警 P1"
                if i["severity"] == 2
                else "关注 P2"
                if i["severity"] == 1
                else "金额复核"
                if i["id"] in top_amount
                else "建议核查"
            )
            _, advice, verification = advice_for(reason, parameters, templates)
            advice_list = [advice]
            for kind in ("size", "quality"):
                if (i["observed"][kind + "_qty"] or D(0)) > 0 and templates[kind][
                    1
                ] not in advice_list:
                    advice_list.append(templates[kind][1])
            if any("售前退款率上升" in e for e in i["evidence"]):
                advice_list.append("核查取消原因、等待发货及价格活动，再调整履约或表达。")
            all_actions.append(
                dict(
                    id=i["id"],
                    status=status,
                    object=f"{label} {i['name']}",
                    amount=i["amount"],
                    amount_share=i["amount_share"],
                    risk_score=i["risk_score"],
                    hypothesis=f"首要填写原因为“{reason}”；需抽查实际场景，不直接认定根因。"
                    if reason
                    else "需结合申请说明、客服备注及实物验证原因。",
                    action="建议" + "".join(advice_list),
                    verification=verification + "。" + effect,
                    previous_rate=i["previous"]["total_rate"] if i["previous"] else None,
                    current_rate=i["total_refund_rate"],
                    note="；".join(
                        i["evidence"]
                        + (["单款实际入库金额Top10，仅复核"] if i["id"] in top_amount else [])
                        + ["分数仅在本层比较"]
                    ),
                    _level=level,
                    _severity=i["severity"],
                )
            )
        result[level + "_actions"] = select_priorities(priorities)
    # 整改单保留全部对象。先品类再单款；同层先预警级别再相对分、金额。
    all_actions.sort(
        key=lambda i: (
            i["_level"] == "style",
            -i["_severity"],
            -(i["risk_score"] or D(0)),
            -(i["amount"] or D(0)),
            i["id"],
        )
    )
    result["actions"] = [{k: v for k, v in i.items() if not k.startswith("_")} for i in all_actions]
    main = min(
        all_actions,
        key=lambda i: (-i["_severity"], i["amount"] is None, -(i["amount"] or D(0)), i["id"]),
        default=None,
    )
    result["decision"] = dict(
        title=main["object"].split(" ", 1)[1] if main else "本期暂无重点核查项",
        status=main["status"] if main else "暂无重点项",
        rate=main["current_rate"] if main else overall["total_rate"],
        amount=main["amount"] if main else None,
        amount_share=main["amount_share"] if main else None,
        change_pp=(main["current_rate"] - main["previous_rate"]) * 100
        if main and main["current_rate"] is not None and main["previous_rate"] is not None
        else None,
        note=main["note"] if main else "按本期已接数据与固定规则展示",
    )
    result["priorities"] = (result["category_actions"] + result["style_actions"])[:3]
    return result
