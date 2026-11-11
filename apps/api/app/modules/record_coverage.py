"""记录存在性的纯集合运算；不解释业务原因，不读取来源，不改变任何指标。"""

from datetime import timedelta


def missing_shop_days(observed, start, end, shops):
    """给定已核店铺范围与闭区间，返回缺失店铺日；NULL和真实零都算存在。"""
    return [
        (shop, start + timedelta(days=i))
        for shop in shops
        for i in range((end - start).days + 1)
        if (shop, start + timedelta(days=i)) not in observed
    ]
