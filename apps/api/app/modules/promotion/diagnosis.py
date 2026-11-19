"""SOP v1.4：沿用已确认汇总，正常无记录不补零，各项规则独立判断。"""

import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from math import ceil
from zoneinfo import ZoneInfo

from .metrics import product_metrics
from .query import (
    dates_between,
    labels,
    matches,
    period_change,
    product_search_names,
    roi_excluded_rows,
)

RULE_VERSION = "sop-v1.4-m2-20261103-online-rating"
RULE_STATES = dict(
    roi_drop="confirmed_per_rule",
    net_roi_drop="confirmed_per_rule",
    spend_growth="confirmed_no_minimum_sample",
    zero_deals="observation_only",
    attribution="source_reported_payment_date",
    human_actions="not_connected",
    ai="not_connected",
)
ROI_MIN_SPEND = Decimal("300")
ROI_ATTENTION_DROP = Decimal("-.15")
ROI_WARNING_DROP = Decimal("-.25")
SPEND_GROWTH = Fraction(1, 5)
# 展示/搜索名称；“观察”的底层规则与旧消费者枚举保持不变。
PRIORITY_LABELS = {"P1": "P1 预警", "P2": "P2 关注", "观察": "P3 观察"}


def is_focused(level):
    """一个商品只计一次；多等级按实际成员识别S/A/专供/新品。"""
    return any(
        re.fullmatch(r"[SA](级)?|专供|新品", member.strip(), re.I)
        for member in (level or "").removesuffix("混合").split("/")
    )


def assess(current, previous, *, current_available=True, previous_available=True):
    """同对象相邻7日原行→指标、逐规则状态；available只指已发布历史范围可覆盖窗口。"""
    now, before = product_metrics(current), product_metrics(previous)
    for key, metric in now.items():
        metric["previous"] = before[key]["value"]
        period_change(
            metric, before[key], history_available=current_available and previous_available
        )
    findings, rule_results = [], []

    def required(keys, *, two_periods=True):
        # 复核提醒沿线上独立合计已知值；部分原行空值不阻断可算结果。
        # 全空、无记录、非正分母仍不可算，原始缺项状态由指标保留。
        periods = [("本期", current, now, current_available)]
        if two_periods:
            periods.append(("前期", previous, before, previous_available))
        for label, rows, metrics, available in periods:
            if not available:
                return label + "可查询历史不足7天"
            if not rows:
                return label + "没有数据"
            for key in keys:
                if metrics[key]["value"] is None:
                    return (
                        label
                        + {
                            "spend": "花费未单独提供",
                            "revenue": "有交易额未提供",
                            "deals": "有成交笔数未提供",
                            "roi": "有交易额未提供",
                            "net_roi": "有净交易额未提供",
                        }[key]
                    )
        return None

    def record(key, label, reason=None, *, triggered=False, applicable=True):
        state = (
            "unavailable"
            if reason
            else "triggered"
            if triggered
            else "clear"
            if applicable
            else "not_applicable"
        )
        rule_results.append(dict(key=key, label=label, state=state, reason=reason))

    for key, label in (("roi", "投产比"), ("net_roi", "净投产")):
        reason = required(("spend",))
        sample = not reason and all(m["spend"]["value"] >= ROI_MIN_SPEND for m in (now, before))
        if sample:
            reason = required((key,))
            if not reason and before[key]["value"] <= 0:
                reason = "前期" + label + "为0或负数，无法计算降幅"
        delta = now[key]["change"]
        exact_delta = None
        if sample and not reason:
            # 阈值由汇总分子分母判断，避免先除法舍入把恰好15%/25%判到下一档。
            current_ratio = Fraction(now[key]["numerator"]) / Fraction(now[key]["denominator"])
            previous_ratio = Fraction(before[key]["numerator"]) / Fraction(
                before[key]["denominator"]
            )
            exact_delta = current_ratio / previous_ratio - 1
        triggered = bool(exact_delta is not None and exact_delta <= ROI_ATTENTION_DROP)
        record(key + "_drop", label + "下降", reason, triggered=triggered, applicable=bool(sample))
        if triggered:
            level = "P1" if exact_delta <= ROI_WARNING_DROP else "P2"
            findings.append(
                dict(
                    level=level,
                    rule=label + "下降" + ("≥25%" if level == "P1" else "≥15%"),
                    evidence=(
                        f"{display_value(before[key]['value'], 'ratio')} → "
                        f"{display_value(now[key]['value'], 'ratio')}"
                        f"（{display_value(delta, 'percent')}）"
                    ),
                    action=(
                        "核对平台净交易额的退款范围与回流时间，再复核关联商品；"
                        "不将净投产等同结算利润。"
                        if key == "net_roi"
                        else (
                            "逐项核查 CPC、点击转化及商品结构，"
                            "结合实际出价、素材和承接记录确认原因。"
                        )
                    ),
                    status="threshold_observed",
                )
            )

    reason = required(("spend", "revenue"))
    if not reason and any(before[k]["value"] <= 0 for k in ("spend", "revenue")):
        reason = "前期花费或交易额为0或负数，无法计算增幅"
    growth_matches = False
    if not reason:
        # 精确比较严格一半边界；不另设花费或成交笔数门槛。
        spend_ratio = Fraction(now["spend"]["value"]) / Fraction(before["spend"]["value"]) - 1
        revenue_ratio = Fraction(now["revenue"]["value"]) / Fraction(before["revenue"]["value"]) - 1
        growth_matches = spend_ratio >= SPEND_GROWTH and revenue_ratio * 2 < spend_ratio
    record("spend_growth", "花费增长与成交承接", reason, triggered=growth_matches)
    if growth_matches:
        findings.append(
            dict(
                level="P1" if any(f["level"] == "P1" for f in findings) else "P2",
                rule="花费增长≥20%，交易额增幅不足花费增幅一半",
                evidence=(
                    f"花费变化 {display_value(now['spend']['change'], 'percent')}；"
                    f"交易额变化 {display_value(now['revenue']['change'], 'percent')}"
                ),
                action="复核新增预算对应的店铺、品类和商品，检查成交承接；先确认原因，再决定预算调整。",
                status="threshold_observed",
            )
        )

    reason = required(("spend", "deals"), two_periods=False)
    sample = not reason and now["spend"]["value"] >= ROI_MIN_SPEND
    zero_deals = bool(sample and now["deals"]["value"] == 0)
    # 已有成交即可排除“成交0笔”，无需知道未分摊的花费；其它规则仍分别判断。
    if current_available and current and (now["deals"]["value"] or 0) > 0:
        reason, sample = None, True
    record("zero_deals", "有花费无成交", reason, triggered=zero_deals, applicable=bool(sample))
    if zero_deals:
        findings.append(
            dict(
                level="观察",
                rule="近7天花费≥300元，成交0笔",
                evidence=f"花费 {display_value(now['spend']['value'], 'currency')}，成交0笔",
                action="核对转化归因延迟与可售状态；归因完整性确认前不升级预警。新品结合测试阶段复核，不直接停投。",
                status="observation_only",
            )
        )

    clues = []
    for key, label, direction, action in (
        ("ctr", "CTR", -1, "检查商品／素材结构、曝光分布和价格活动；当前未接入素材明细。"),
        ("cpc", "CPC", 1, "核查流量结构及真实出价调整记录，确认点击成本上涨原因。"),
        ("cvr", "点击转化", -1, "检查价格优惠、评价售后与可售状态，优先改善承接。"),
    ):
        change = now[key]["change"]
        if change is not None and change * direction > 0:
            clues.append(
                dict(
                    label=label,
                    current=now[key]["value"],
                    previous=before[key]["value"],
                    change=change,
                    evidence="",
                    action=action,
                    status="reference"
                    if now[key]["comparison_status"] == "reference"
                    else "observation_only",
                )
            )
    limited = [r for r in rule_results if r["state"] == "unavailable"]
    state = (
        "rated" if not limited else "unrated" if len(limited) == len(rule_results) else "partial"
    )
    priority = next(
        (p for p in ("P1", "P2", "观察") if any(f["level"] == p for f in findings)),
        "未评级" if state == "unrated" else "部分可判断" if state == "partial" else "未触发",
    )
    return dict(
        current=now,
        previous=before,
        findings=findings,
        clues=clues,
        notes=list(dict.fromkeys(r["reason"] for r in limited)),
        priority=priority,
        evaluation_state=state,
        rule_results=rule_results,
        exclusions=dict(current=roi_excluded_rows(current), previous=roi_excluded_rows(previous)),
        current_days=len({r["date"] for r in current}),
        previous_days=len({r["date"] for r in previous}),
        current_record_count=str(len(current)),
        previous_record_count=str(len(previous)),
    )


DIAGNOSIS_GROUPS = ("products", "flagged_products", "focused_products", "shops", "categories")


def diagnosis(repo, q, *, detail="full"):
    q.validate(repo)
    yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    end = min(q.end, repo.end, yesterday)
    start = end - timedelta(days=6)
    previous_end = start - timedelta(days=1)
    previous_start = start - timedelta(days=7)
    common = dict(
        current_window=dict(start=start, end=end),
        previous_window=dict(start=previous_start, end=previous_end),
        rule_version=RULE_VERSION,
        rule_states=RULE_STATES,
        status="insufficient_evidence",
    )
    if repo.mode == "store":
        return dict(
            **common,
            overall=None,
            products=[],
            flagged_products=[],
            focused_products=[],
            shops=[],
            categories=[],
            counts={},
            coverage=dict(completeness="unknown", complete_through=None),
            status_reason="店铺模式不使用商品SOP",
        )
    rows = repo.rows_for_windows([(previous_start, end)])
    info = labels(repo, rows)
    selected = {lid: item for lid, item in info.items() if matches(item, q)}
    rows = [r for r in rows if r.get("listing_id") in selected]
    current = [r for r in rows if start <= r["date"] <= end]
    previous = [r for r in rows if previous_start <= r["date"] <= previous_end]
    available = dict(
        current_available=repo.start <= start, previous_available=repo.start <= previous_start
    )
    cov = dict(
        observed_through=repo.end,
        source_start=repo.start,
        covered_days=len({r["date"] for r in rows}),
        expected_days=14,
        no_record_dates=[
            d for d in dates_between(previous_start, end) if d not in {r["date"] for r in rows}
        ],
        completeness="unknown",
        complete_through=None,
        **available,
    )
    overall = assess(current, previous, **available)

    def objects(kind):
        buckets = defaultdict(list)
        identities = {}
        for row in rows:
            item = selected[row["listing_id"]]
            key = (
                item["listing_id"]
                if kind == "product"
                else item["shop_id"]
                if kind == "shop"
                else (item["shop_id"], item["category"])
            )
            buckets[key].append(row)
            identities[key] = item
        result = []
        for key, values in buckets.items():
            item = identities[key]
            label = (
                (item["product_name"] or item["product_id"])
                if kind == "product"
                else item["shop_name"] or item["shop_id"]
                if kind == "shop"
                else f"{item['shop_name'] or item['shop_id']} · {item['category'] or '未归属品类'}"
            )
            now = [r for r in values if r["date"] >= start]
            before = [r for r in values if r["date"] <= previous_end]
            assessment = assess(now, before, **available)
            result.append(
                dict(
                    object_key=str(key),
                    object_type=kind,
                    label=label,
                    search_names=product_search_names(item) if kind == "product" else [],
                    listing_id=item["listing_id"] if kind == "product" else None,
                    product_id=item["product_id"] if kind == "product" else None,
                    product_name=item["product_name"] if kind == "product" else None,
                    shop_id=item["shop_id"],
                    shop_name=item["shop_name"],
                    category=item["category"],
                    level=item["level"] if kind == "product" else None,
                    operator=item["operator"] if kind == "product" else None,
                    focus=kind == "product" and is_focused(item["level"]),
                    action_context="原因待核查；目标、截止与复盘待安排",
                    **assessment,
                )
            )
        return sorted(
            result,
            key=lambda r: (
                {"P1": 0, "P2": 1, "观察": 2}.get(r["priority"], 3),
                -(r["current"]["spend"]["value"] or Decimal(0)),
                r["object_key"],
            ),
        )

    products = objects("product")
    flagged = [r for r in products if r["findings"]]
    focused = [r for r in products if r["focus"]]
    shops, categories = objects("shop"), objects("category")
    # 保持运营中心既有状态契约；各项可判断范围由新增计数/规则状态单独表达。
    common["status"] = (
        "ready"
        if current and all(p["evaluation_state"] == "rated" for p in products)
        else "insufficient_evidence"
    )
    return dict(
        **common,
        overall=overall,
        products=products if detail == "full" else [],
        # 摘要只保留首条；中心alerts模式保留完整异常原结果，不另设筛选/分级。
        flagged_products=flagged if detail in ("full", "alerts") else flagged[:1],
        focused_products=focused if detail == "full" else [],
        shops=shops if detail == "full" else [],
        categories=categories if detail == "full" else [],
        coverage=cov,
        counts=dict(
            products=len(products),
            flagged=len(flagged),
            focused=len(focused),
            shops=len(shops),
            categories=len(categories),
            p1=sum(r["priority"] == "P1" for r in flagged),
            p2=sum(r["priority"] == "P2" for r in flagged),
            observation=sum(r["priority"] == "观察" for r in flagged),
            limited=sum(r["evaluation_state"] != "rated" for r in products),
            fully_assessed=sum(r["evaluation_state"] == "rated" for r in products),
            partial=sum(r["evaluation_state"] == "partial" for r in products),
            unrated=sum(r["evaluation_state"] == "unrated" for r in products),
            unmatched_records=sum(
                any(not selected[r["listing_id"]].get(f) for f in ("category", "level", "operator"))
                for r in current
            ),
            missing_product_ids=sum(not r.get("source_product_id") for r in current),
            current_records=len(current),
        ),
    )


def display_value(value, kind):
    """与推广前端一致的显示舍入，仅供清单检索，不改变原指标值。"""
    if value is None:
        return "—"
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + 10)
        digits = 0 if kind == "number" else 2
        scaled = value * (100 if kind == "percent" else 1)
        rounded = scaled.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        if rounded == 0:
            rounded = abs(rounded)
        grouping = "," if kind in ("number", "currency") else ""
        text = format(rounded, f"{grouping}.{digits}f")
        return "¥" + text if kind == "currency" else text + "%" if kind == "percent" else text


def clue_text(clue):
    """保持原清单可见线索的检索能力，包含格式化的涨跌百分比。"""
    kind = (
        "currency"
        if clue["label"] == "CPC"
        else "percent"
        if clue["label"] in ("CTR", "点击转化")
        else None
    )
    if kind is None:
        return clue["evidence"]
    change = clue["change"]
    if change is None:
        delta = "不可比"
    else:
        raw = format(change, "f")
        delta = ("" if raw.startswith("-") or raw == "0" else "+") + display_value(
            change, "percent"
        )
    previous = display_value(clue["previous"], kind)
    current = display_value(clue["current"], kind)
    return f"{previous} → {current}（{delta}）"


def object_matches(item, search):
    """清单文字搜索完整对象组；原指标仅格式化为检索文本，不参与重新计算。"""
    needle = search.strip().casefold()
    if not needle:
        return True
    values = [
        str(item.get(k) or "")
        for k in ("label", "product_id", "shop_name", "operator", "category", "level", "priority")
    ]
    values.extend(item.get("search_names", []))
    values.append(PRIORITY_LABELS.get(item.get("priority"), item.get("priority", "")))
    level = item.get("level") or "未匹配"
    if "/" in level and level.endswith("混合"):
        level = "多等级（含" + level.removesuffix("混合").replace("/", "、") + "）"
    values.append(level)
    for group in ("current", "previous"):
        for metric in item[group].values():
            values.extend(
                (
                    metric["label"],
                    str(metric["value"] or ""),
                    display_value(metric["value"], metric["type"]),
                )
            )
    for group in ("findings", "clues"):
        values.extend(str(value) for entry in item[group] for value in entry.values())
    values.extend(PRIORITY_LABELS.get(f["level"], f["level"]) for f in item["findings"])
    values.extend(clue_text(clue) for clue in item["clues"])
    values.extend(item["notes"])
    return needle in " ".join(values).casefold()


def diagnosis_objects(repo, q, group, search=""):
    """固定同一版本与经营范围，先搜索完整分组再分页，保留诊断原排序和全部证据。"""
    data = diagnosis(repo, q)
    rows = data[group]
    selected = [item for item in rows if object_matches(item, search)]
    total = len(selected)
    return dict(
        items=selected[(q.page - 1) * q.size : q.page * q.size],
        total=total,
        group_total=len(rows),
        page=q.page,
        size=q.size,
        total_pages=max(1, ceil(total / q.size)),
    )
