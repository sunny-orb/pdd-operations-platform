"""当前生效指标的简短口径。只解释既有公式，经营事实仍经领域查询取得。"""

import hashlib
import json

from app.modules.product_board.metrics import RATIOS

from .business import report, table

_LABELS = {
    "product_revenue": "商品交易金额",
    "buyers": "支付买家数",
    "orders": "支付订单数",
    "visitors": "商品访客数",
    "attributed_revenue": "推广归因交易额",
    "net_attributed_revenue": "推广净归因交易额",
    "spend": "推广花费",
    "clicks": "点击量",
    "impressions": "曝光量",
    "attributed_deals": "推广归因成交笔数",
}
_NAMES = {
    "aov": "商品客单价",
    "conversion": "商品经营支付转化率",
    "score_conversion": "新品评分支付转化率",
    "uv_value": "商品UV价值",
}
_PROMOTION = (
    ("roi", "推广投产比（倍）", "attributed_revenue", "spend"),
    ("net_roi", "推广净投产比（倍）", "net_attributed_revenue", "spend"),
    ("ctr", "推广点击率", "clicks", "impressions"),
    ("cvr", "推广点击转化率", "attributed_deals", "clicks"),
    ("cpc", "单次点击成本", "spend", "clicks"),
)


def rows():
    values = [
        dict(
            domain="商品",
            metric=_NAMES[key],
            numerator=_LABELS[numerator],
            denominator=_LABELS[denominator],
            note="新品评分与经营转化率的分子不同。"
            if key in ("conversion", "score_conversion")
            else "以相容记录的分子、分母汇总计算，缺失和分母为零有独立状态。",
            source="app/modules/product_board/metrics.py",
        )
        for key, numerator, denominator in RATIOS
    ]
    values.extend(
        dict(
            domain="推广",
            metric=label,
            numerator=_LABELS[numerator],
            denominator=_LABELS[denominator],
            note="归因成交口径；店铺与商品模式分别沿既有领域。部分缺项可算值仅供参考。",
            source="app/modules/promotion/metrics.py",
        )
        for _key, label, numerator, denominator in _PROMOTION
    )
    values.extend(
        [
            dict(
                domain="销售",
                metric="店铺买家转化率",
                numerator="店铺成交买家数",
                denominator="配对记录中的店铺访客数",
                note="先按店铺日配对，缺项不补零。",
                source="app/modules/sales/query.py",
            ),
            dict(
                domain="销售",
                metric="店铺客单价",
                numerator="配对记录中的店铺成交金额",
                denominator="店铺成交买家数",
                note="先按店铺日配对，沿既有销售领域。",
                source="app/modules/sales/query.py",
            ),
            dict(
                domain="销售",
                metric="GMV月目标达成",
                numerator="当月累计GMV",
                denominator="相容的当月原版GMV目标",
                note="目标固定六店，不随经营单店筛选改分母。",
                source="app/modules/sales/detail_queries.py",
            ),
            dict(
                domain="售后",
                metric="固定报告三率",
                numerator="查看所选报告的金额/率依据",
                denominator="查看所选报告的核验口径",
                note="报告、品类、款式独立核验；缺额、真实零与零分母有区别。",
                source="app/modules/aftersales/metrics.py",
            ),
            dict(
                domain="通用",
                metric="经营与推广成交金额",
                numerator="各域原指标",
                denominator="不适用",
                note="经营成交与推广归因成交不能当作同一指标相加，也不重复扣退款。",
                source="docs/data-contract.md#project-ai-contract",
            ),
        ]
    )
    return values


def describe(engine=None):
    data = rows()
    version = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return dict(domain="definitions", data_version=version, shops=[], date_from=None, date_to=None)


def read(engine, capability, params):
    items = rows()
    keyword = params.get("keyword") or ""
    if keyword:
        items = [item for item in items if keyword in " ".join(item.values())]
    offset, limit = params.get("offset", 0), params.get("limit", 10)
    return report(
        "definitions",
        capability,
        "指标口径",
        "现有指标实现与数据契约",
        describe()["data_version"],
        scope=dict(keyword=keyword, limit=limit, offset=offset),
        tables=[
            table(
                "适用口径",
                [
                    (key, label, "text")
                    for key, label in (
                        ("domain", "业务"),
                        ("metric", "指标"),
                        ("numerator", "分子"),
                        ("denominator", "分母"),
                        ("note", "说明"),
                        ("source", "实现出处"),
                    )
                ],
                items[offset : offset + limit],
                total=len(items),
                offset=offset,
            )
        ],
        notes=["本工具说明公式与边界；实际经营数字、售后报告和SOP命中仍从对应查询取得。"],
    )
