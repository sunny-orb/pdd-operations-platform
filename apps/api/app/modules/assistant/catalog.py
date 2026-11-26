"""可维护的业务能力目录。工具只按域转发到已有的只读领域查询。"""

from typing import Literal

Domain = Literal[
    "all",
    "products",
    "promotion_store",
    "promotion_product",
    "sales",
    "aftersales",
    "weather",
    "data_status",
    "definitions",
]
Capability = Literal[
    "products_overview",
    "products_risks",
    "products_scores",
    "products_days",
    "products_catalog",
    "promotion_overview",
    "promotion_days",
    "promotion_diagnosis",
    "promotion_objects",
    "promotion_catalog",
    "sales_performance",
    "sales_trend",
    "sales_shops",
    "sales_targets",
    "sales_ratings",
    "sales_regions",
    "sales_product_regions",
    "sales_product_options",
    "aftersales_overview",
    "aftersales_analysis",
    "aftersales_options",
    "weather",
    "data_status",
    "metric_definitions",
    "rule_definitions",
]
CAPABILITIES = {
    "rule_definitions": ("definitions", "生效规则的阈值、样本门槛、窗口、优先级、评分及例外"),
    "metric_definitions": ("definitions", "指标分子分母、经营/评分/推广口径差异"),
    "products_overview": ("products", "商品经营汇总、排行、变化与逐日趋势"),
    "products_risks": ("products", "经营风险和已有SOP核查建议"),
    "products_scores": ("products", "已发布新品评分及分项依据"),
    "products_days": ("products", "商品逐日明细表；走势可用products_overview"),
    "products_catalog": ("products", "按条件查商品目录"),
    "promotion_overview": ("promotion", "店铺或商品推广汇总与排行"),
    "promotion_days": ("promotion", "推广逐日走势"),
    "promotion_diagnosis": ("promotion", "推广SOP诊断及实际评估窗口"),
    "promotion_objects": ("promotion", "命中推广诊断的明细对象"),
    "promotion_catalog": ("promotion", "推广可选对象与筛选目录"),
    "sales_performance": ("sales", "店铺经营、流量、转化、退款与推广"),
    "sales_trend": ("sales", "店铺经营逐日趋势"),
    "sales_shops": ("sales", "全部店铺贡献和排名"),
    "sales_targets": ("sales", "月度GMV目标及达成"),
    "sales_ratings": ("sales", "店铺评级和评分"),
    "sales_regions": ("sales", "店铺地区成交分布"),
    "sales_product_regions": ("sales", "产品地区实发实退，独立于成交口径"),
    "sales_product_options": ("sales", "地区查询可选产品编码"),
    "aftersales_overview": ("aftersales", "售后报告三率、原因与规则建议"),
    "aftersales_analysis": ("aftersales", "品类/款式/环比/同比/品质/历史分析"),
    "aftersales_options": ("aftersales", "可选报告、品类与款式目录"),
    "weather": ("weather", "已发布全国天气及预警"),
    "data_status": ("data_status", "本地数据覆盖、最近更新和失败状态"),
}


def domain_for(capability, mode="product"):
    domain = CAPABILITIES[capability][0]
    return f"promotion_{mode}" if domain == "promotion" else domain


def catalog(domain="all"):
    hints = {
        "sales_trend": (
            "view选shop_revenue/shop_visitors/buyer_conversion/aov/"
            "promotion_spend/promotion_ctr/promotion_roi/refund_rate"
        ),
        "aftersales_analysis": (
            "view选category/style/mom/yoy/quality/history；focus选all/size/quality"
        ),
        "data_status": (
            "view选datasets(截至日)/sources/timeliness/history/reconciliation/processing"
        ),
        "promotion_objects": "view选category/product/shop/flagged/focused",
        "weather": "view可选已发现的publication_id，不传取最新",
        "metric_definitions": "keyword按指标名筛选，比较经营/评分转化率时可用转化率",
        "rule_definitions": (
            "view选products/promotion_store/promotion_product/sales/aftersales/all；"
            "keyword可选具体规则名或ID；售后可带report_id，沿所选报告"
        ),
    }
    return [
        dict(capability=key, description=label, parameters=hints.get(key, "沿用当前范围，可分页"))
        for key, (group, label) in CAPABILITIES.items()
        if domain == "all" or group == domain or domain.startswith(group + "_")
    ]


def describe(engine, domain, *, rows_cache=None):
    if domain == "definitions":
        from .definitions import describe as read_metadata

        return read_metadata(engine)
    if domain in ("products", "promotion_store", "promotion_product"):
        from .board_tools import describe as read_metadata

        return read_metadata(
            engine,
            "products" if domain == "products" else "promotion",
            mode="store" if domain == "promotion_store" else "product",
        )
    if domain == "aftersales":
        from .aftersales_tools import describe as read_metadata

        return read_metadata(engine, rows_cache=rows_cache)
    from .sales_tools import describe as read_metadata

    return read_metadata(engine, domain=domain)


def read(engine, capability, params, *, rows_cache=None):
    if capability == "rule_definitions":
        from .rule_definitions import read as read_rules

        return read_rules(engine, params)
    domain = domain_for(capability, params.get("mode", "product"))
    if domain == "definitions":
        from .definitions import read as read_data
    elif domain in ("products", "promotion_store", "promotion_product"):
        from .board_tools import read as read_data
    elif domain == "aftersales":
        from .aftersales_tools import read as read_data

        return read_data(engine, capability, params, rows_cache=rows_cache)
    else:
        from .sales_tools import read as read_data
    return read_data(engine, capability, params)
