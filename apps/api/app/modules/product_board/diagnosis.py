"""经营诊断复用七卡同比；只给检查方向，不借用同款健康度或推定缺失为0。"""


def operating_diagnosis(metrics):
    """输入同一筛选范围七卡，输出线上三段式标题和动作；金额仍由卡片提供。"""
    changes = {key: metrics[key]["yoy"] for key in ("revenue", "visitors", "conversion", "aov")}
    revenue, visitors, conversion, aov = (changes[key] for key in changes)
    growth_title = (
        "交易表现"
        if revenue is None
        else "交易增长"
        if revenue > 0
        else "交易承压"
        if revenue < 0
        else "交易持平"
    )
    traffic_title = "流量风险" if visitors is not None and visitors < 0 else "流量变化"
    if conversion is not None and conversion < 0:
        action = "优先复核商品主图、价格与详情页承接，先改善支付转化。"
    elif aov is not None and aov < 0:
        action = "建议检查商品组合与关联推荐，改善客单价。"
    elif visitors is not None and visitors < 0:
        action = "先检查近期活动、素材和流量入口，关注访客减少的商品。"
    elif revenue is not None and revenue < 0:
        action = "先查看成交下滑的商品，结合流量、转化和价格逐款检查。"
    elif all(value is not None and value >= 0 for value in changes.values()):
        action = "流量、转化和客单价未下降，可小步增加高效商品流量并观察成交。"
    else:
        action = "先结合现有成交和流量表现观察，暂不据此判断是否放量。"
    return dict(
        generation_method="rules",
        growth_title=growth_title,
        traffic_title=traffic_title,
        action=action,
    )
