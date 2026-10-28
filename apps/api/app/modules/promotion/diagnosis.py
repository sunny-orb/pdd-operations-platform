"""SOP v1.4确定性证据：完整性未知暂停评级，保留真实观察及变化线索。"""

import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

from app.modules.product_board.metrics import compare

from .query import aggregate, coverage, dates_between, labels, matches

RULE_VERSION = "sop-v1.4-m2-20261028-source-payment-date"
RULE_STATES = dict(
    roi_drop="confirmed_threshold_inputs_pending",
    net_roi_drop="confirmed_threshold_inputs_pending",
    spend_growth="confirmed_no_minimum_sample_inputs_pending",
    attribution="source_reported_payment_date",
    human_actions="not_connected",
    ai="not_connected",
)


def assess(current, previous, *, complete=False):
    """输入同一对象的相邻7日原行，输出精确指标/线索及有门禁的规则证据。"""
    now, before = aggregate(current), aggregate(previous)
    end = max((r["date"] for r in current), default=None)
    calendar_covered = bool(
        end
        and {r["date"] for r in current} == set(dates_between(end - timedelta(days=6), end))
        and {r["date"] for r in previous}
        == set(dates_between(end - timedelta(days=13), end - timedelta(days=7)))
    )
    complete = complete and calendar_covered
    findings = []
    notes = []
    if not complete:
        notes.append("完整日未获证明或对象日历覆盖不足，暂停趋势评级")
    if not current:
        notes.append("本期无记录，不视为零业绩")
    if not previous:
        notes.append("基期无记录，不计算增长率")
    sample = (
        complete
        and current
        and previous
        and all(
            m["spend"]["value"] is not None
            and m["spend"]["value"] >= 300
            and m["spend"]["status"] == "observed"
            for m in (now, before)
        )
    )
    for key, label in (("roi", "实际投产"), ("net_roi", "净投产")):
        delta = compare(now[key]["value"], before[key]["value"])
        if (
            sample
            and now[key]["status"] == before[key]["status"] == "observed"
            and delta is not None
            and delta <= Decimal("-.15")
        ):
            level = "P1" if delta <= Decimal("-.25") else "P2"
            findings.append(
                dict(
                    level=level,
                    rule=label + "下降" + ("≥25%" if level == "P1" else "≥15%"),
                    evidence=f"{before[key]['value']} → {now[key]['value']}；变化 {delta}",
                    action="复核点击成本、转化、退款范围与真实投放记录；确认原因后评估预算",
                    status="threshold_observed",
                )
            )
    if not sample:
        notes.append("投产趋势评级需两期花费均≥300元、正有效基数及完整覆盖")
    clues = []
    for key, label, direction, action in (
        ("ctr", "CTR", -1, "检查商品与素材结构、曝光分布及价格活动"),
        ("cpc", "CPC", 1, "复核流量结构与实际出价调整记录"),
        ("cvr", "点击转化", -1, "核对价格优惠、评价售后和可售状态"),
    ):
        change = compare(now[key]["value"], before[key]["value"])
        if current and previous and change is not None and change * direction > 0:
            clues.append(
                dict(
                    label=label,
                    current=now[key]["value"],
                    previous=before[key]["value"],
                    change=change,
                    evidence=f"{before[key]['value']} → {now[key]['value']}",
                    action=action,
                    status="observation_only",
                )
            )
    spend_delta = compare(now["spend"]["value"], before["spend"]["value"])
    revenue_delta = compare(now["revenue"]["value"], before["revenue"]["value"])
    growth_inputs = (
        bool(current and previous)
        and all(
            m[key]["status"] == "observed" for m in (now, before) for key in ("spend", "revenue")
        )
        and spend_delta is not None
        and revenue_delta is not None
    )
    growth_matches = False
    if growth_inputs:
        # 用精确分数判断严格边界，避免循环小数舍入把“恰好一半”判成不足。
        spend_ratio = Fraction(now["spend"]["value"]) / Fraction(before["spend"]["value"]) - 1
        revenue_ratio = Fraction(now["revenue"]["value"]) / Fraction(before["revenue"]["value"]) - 1
        growth_matches = spend_ratio >= Fraction(1, 5) and revenue_ratio * 2 < spend_ratio
    if growth_matches:
        evidence = f"花费变化 {spend_delta}；交易额变化 {revenue_delta}"
        action = "核查新增花费的成交承接，复核流量与转化变化后评估预算"
        if complete:
            findings.append(
                dict(
                    level="P1" if any(f["level"] == "P1" for f in findings) else "P2",
                    rule="花费增长≥20%，成交承接不足花费增幅一半",
                    evidence=evidence,
                    action=action,
                    status="threshold_observed",
                )
            )
        else:
            clues.append(
                dict(
                    label="花费增长组合（覆盖待核，观察）",
                    current=now["spend"]["value"],
                    previous=before["spend"]["value"],
                    change=spend_delta,
                    evidence=evidence,
                    action=action + "；完整性未证，仅供观察",
                    status="observation_only",
                )
            )
    if (
        complete
        and current
        and now["spend"]["value"] is not None
        and now["spend"]["value"] >= 300
        and now["deals"]["value"] == 0
        and now["deals"]["status"] == "observed"
    ):
        findings.append(
            dict(
                level="观察",
                rule="支付日有花费无成交，仅作观察",
                evidence=f"花费 {now['spend']['value']}，成交0笔",
                action="当日花费与成交并非同批点击；核对来源修订、可售状态及新品测试阶段，不直接停投",
                status="observation_only",
            )
        )
    if any(m["net_roi"]["value"] is None for m in (now, before)):
        notes.append("净额输入缺失或花费分母无效；真实净额零可以计算零净投产")
    priority = (
        "P1"
        if any(f["level"] == "P1" for f in findings)
        else "P2"
        if any(f["level"] == "P2" for f in findings)
        else "观察"
    )
    return dict(
        current=now,
        previous=before,
        findings=findings,
        clues=clues,
        notes=notes,
        priority=priority,
        current_days=len({r["date"] for r in current}),
        previous_days=len({r["date"] for r in previous}),
        current_record_count=str(len(current)),
        previous_record_count=str(len(previous)),
    )


def diagnosis(repo, q):
    q.validate(repo)
    end = q.end
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
    rows = [r for r in repo.rows if previous_start <= r["date"] <= end]
    info = labels(repo, rows)
    selected = {lid: item for lid, item in info.items() if matches(item, q)}
    rows = [r for r in rows if r.get("listing_id") in selected]
    current = [r for r in rows if start <= r["date"] <= end]
    previous = [r for r in rows if previous_start <= r["date"] <= previous_end]
    window_q = replace(q, start=previous_start, end=end)
    cov = coverage(repo, window_q, rows)
    yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    cov.update(
        stale=repo.end < yesterday,
        observed_through=repo.end,
        covered_days=len({r["date"] for r in rows}),
        expected_days=14,
        missing_dates=[
            d for d in dates_between(previous_start, end) if d not in {r["date"] for r in rows}
        ],
    )
    overall = assess(current, previous)

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
                f"{item['product_id']} · {item['product_name'] or '名称缺失'}"
                if kind == "product"
                else item["shop_name"] or item["shop_id"]
                if kind == "shop"
                else f"{item['shop_name'] or item['shop_id']} · {item['category'] or '未归属品类'}"
            )
            now = [r for r in values if r["date"] >= start]
            before = [r for r in values if r["date"] <= previous_end]
            assessment = assess(now, before)
            result.append(
                dict(
                    object_key=str(key),
                    object_type=kind,
                    label=label,
                    listing_id=item["listing_id"] if kind == "product" else None,
                    product_id=item["product_id"] if kind == "product" else None,
                    product_name=item["product_name"] if kind == "product" else None,
                    shop_id=item["shop_id"],
                    shop_name=item["shop_name"],
                    category=item["category"],
                    level=item["level"] if kind == "product" else None,
                    operator=item["operator"] if kind == "product" else None,
                    focus=kind == "product"
                    and bool(re.search(r"^(S|A)(级)?$|专供|新品", item["level"] or "", re.I)),
                    action_context="原因待核查；目标、截止与复盘待安排",
                    **assessment,
                )
            )
        return sorted(
            result,
            key=lambda r: (
                {"P1": 0, "P2": 1, "观察": 2}[r["priority"]],
                -(r["current"]["spend"]["value"] or Decimal(0)),
                r["object_key"],
            ),
        )

    products = objects("product")
    flagged = [r for r in products if r["findings"]]
    focused = [r for r in products if r["focus"]]
    shops, categories = objects("shop"), objects("category")
    return dict(
        **common,
        overall=overall,
        products=products,
        flagged_products=flagged,
        focused_products=focused,
        shops=shops,
        categories=categories,
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
            limited=sum(bool(r["notes"]) for r in products),
            unmatched_records=sum(
                any(not selected[r["listing_id"]].get(f) for f in ("category", "level", "operator"))
                for r in current
            ),
            missing_product_ids=sum(not r.get("source_product_id") for r in current),
            current_records=len(current),
        ),
    )
