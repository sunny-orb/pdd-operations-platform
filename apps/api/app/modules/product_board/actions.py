"""商品建议只复用两张卡各自前三及线上文字，不另算指标或查询来源。"""

from datetime import timedelta

LIMIT = 3
FIELD_NAMES = dict(visitors="商品访客", orders="支付订单", product_revenue="交易金额")


def _missing_notices(info, groups, end):
    """返回有界缺项说明和实际受影响的段落，不把新品金额缺项套到风险段。"""
    examples, affected, missing_kinds = [], 0, set()
    for lid, item in info.items():
        requirements = []
        if item.get("risk_eligible"):
            requirements.extend(
                ("risk", day, field) for day in range(8) for field in ("visitors", "orders")
            )
        if item.get("level") == "新品":
            requirements.extend(("opportunity", day, "product_revenue") for day in range(14))
        byday = {r["business_date"]: r for r in groups.get(lid, [])}
        missing_dates, missing_fields = set(), {}
        for kind, days, field in requirements:
            day = end - timedelta(days=days)
            if day not in byday:
                missing_kinds.add(kind)
                missing_dates.add(day)
                continue
            value = byday.get(day, {}).get(field)
            if value is None or value < 0:
                missing_kinds.add(kind)
                missing_fields[day, field] = value is None
        if not (missing_dates or missing_fields):
            continue
        affected += 1
        if len(examples) < LIMIT:
            name = item.get("product_name") or item["product_id"]
            shop = item.get("shop_name") or item["shop_id"]
            if missing_dates:
                detail = f"在{max(missing_dates)}暂无该商品的日经营记录"
                count = f"本次比较共缺{len(missing_dates)}天记录"
                if missing_fields:
                    count += f"，另有{len(missing_fields)}处字段缺失或无效"
            else:
                (day, field), missing = next(iter(missing_fields.items()))
                state = "缺少" if missing else "没有有效的"
                detail = f"在{day}{state}{FIELD_NAMES[field]}数据"
                count = f"共{len(missing_fields)}处字段缺失或无效"
            examples.append(f"{name}（{shop}）{detail}，相关规则暂不能判断（{count}）。")
    if affected > len(examples):
        examples.append(
            f"另有{affected - len(examples)}款商品未满足规则所需的数据条件；缺项不代表真实为0。"
        )
    return examples, missing_kinds


def _names(items, selected):
    """同名跨店补店名，同店同名不同链接再补ID；无歧义时保持线上短名称。"""
    names = []
    for item in items:
        name = item.get("product_name") or item["product_id"]
        peers = [r for r in selected if (r.get("product_name") or r["product_id"]) == name]
        if len({r["listing_id"] for r in peers}) > 1:
            shop = item.get("shop_name") or item["shop_id"]
            suffix = (
                f"{shop}·{item['product_id']}"
                if len({r["listing_id"] for r in peers if r["shop_id"] == item["shop_id"]}) > 1
                else shop
            )
            name += f"（{suffix}）"
        names.append(f"「{name}」")
    return "、".join(names)


def build_actions(info, groups, risks, rising, end):
    """输入为同版已排序候选；各取前三生成线上两段文字，保留缺数/无记录状态。

    两组各自取值，同款可出现在两段中；不再合计限3款、合并商品或展开指标。
    """
    risks, rising = risks[:LIMIT], rising[:LIMIT]
    selected = [*risks, *rising]
    notices, missing_kinds = _missing_notices(info, groups, end)
    risk_names, new_names = _names(risks, selected), _names(rising, selected)
    risk_text = (
        f"优先处理 {risk_names}：流量下滑先检查活动、素材与流量入口；"
        "转化下滑或有流量无成交先复核价格、优惠、评价、详情页与商品可售状态。"
        if risks
        else "当前观察数据没有达到异常阈值的商品，持续监控流量、转化和客单价波动。"
    )
    new_text = (
        f"对 {new_names} 补充素材与关联推荐，分阶段放大有效流量并观察转化承接。"
        if rising
        else "当前范围暂无符合条件的新品起势款，建议从新品中优先测试高点击素材。"
    )
    if not risks and "risk" in missing_kinds:
        risk_text = "当前观察数据暂未筛出风险款；部分比较数据不足，请先核对数据更新。"
    if not rising and "opportunity" in missing_kinds:
        new_text = "当前观察数据暂未筛出新品起势款；部分比较数据不足，请先核对数据更新。"
    if not info:
        status, message = "empty", "当前筛选范围没有商品日记录。"
    elif selected:
        status, message = "observed_reference", "按已观察数据生成；来源完整日未知。"
    elif notices:
        status, message = (
            "insufficient_data",
            "部分规则所需数据不足，暂不能判断，请先核对数据更新。",
        )
    else:
        status, message = "no_match", "当前筛选范围未触发现有规则提醒，继续观察；来源完整日未知。"
    return dict(
        generation_method="rules",
        ai_status="not_connected",
        status=status,
        as_of_date=end,
        message=message,
        items=[
            dict(kind="risk", listing_ids=[r["listing_id"] for r in risks], text=risk_text),
            dict(kind="opportunity", listing_ids=[r["listing_id"] for r in rising], text=new_text),
        ]
        if info
        else [],
        notices=notices,
    )
