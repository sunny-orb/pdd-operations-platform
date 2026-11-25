"""按需解释领域规则；仅汇集共用定义与已发布报告包，不查询经营事实。"""

import hashlib
import json
from pathlib import Path

from app.modules.aftersales.classification import branch_enabled
from app.modules.aftersales.repository import snapshot
from app.modules.aftersales.targets import METRICS as TARGET_NAMES
from app.modules.aftersales.targets import add_target_ranges
from app.modules.product_board import risk_rules
from app.modules.product_board.metrics import WEIGHTS
from app.modules.product_board.repository import BoardError
from app.modules.product_scoring.rules import parameters as score_parameters
from app.modules.promotion import diagnosis
from app.modules.promotion import metrics as promotion_metrics
from app.modules.sales import detail_queries
from app.modules.sales.query import PERFORMANCE_INPUTS
from app.modules.sales.source_policy import SHOPS

from .business import report, table

VIEWS = {"all", "products", "promotion_store", "promotion_product", "sales", "aftersales"}
COLUMNS = [
    (key, name, "text")
    for key, name in (
        ("id", "规则ID"),
        ("domain", "业务"),
        ("name", "名称"),
        ("condition", "条件或公式"),
        ("sample", "样本"),
        ("window", "窗口"),
        ("exceptions", "例外与边界"),
        ("state", "适用状态"),
        ("rule_version", "规则版本"),
        ("source", "出处"),
    )
]


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _revision(*paths):
    root = Path(__file__).parents[1]
    return _digest({p: root.joinpath(p).read_text() for p in paths})


def _rule(
    id, domain, name, condition, sample, window, exceptions, version, source, state="defined"
):
    return dict(
        id=id,
        domain=domain,
        name=name,
        condition=condition,
        sample=sample,
        window=window,
        exceptions=exceptions,
        state=state,
        rule_version=version,
        source=source,
    )


def _product_risk_rules():
    r = risk_rules
    common = f"完整连续{r.BASELINE_DAYS + 1}个日历日访客及订单非空且非负；不补0"
    window = f"当日T对比T-{r.BASELINE_DAYS}至T-1；转化基准为订单合计÷访客合计"
    exception = "按下列顺序首个命中；不要求连续下滑；没有风险结果不代表缺数商品无风险"
    source = "app/modules/product_board/risk_rules.py；product_board/metrics.py"
    specs = [
        (
            "zero_orders",
            "有流量无成交",
            "当日支付订单数=0；P1，规则分100",
            f"当日访客>{r.TRAFFIC_MIN}",
        ),
        (
            "conversion_severe",
            "严重订单转化下滑",
            f"当日订单转化率≤基准×{r.CONVERSION_SEVERE_FACTOR}；P1，规则分90",
            f"当日访客>{r.TRAFFIC_MIN}；基准转化率>0",
        ),
        (
            "traffic_severe",
            "严重流量下滑",
            f"访客变化≤{r.TRAFFIC_SEVERE_CHANGE * 100:g}%且日均访客减少≥{r.TRAFFIC_SEVERE_LOSS}；"
            f"爆品且前{r.BASELINE_DAYS}日订单≥{r.TOP_PREVIOUS_ORDERS_MIN}为P0/95分，否则P1/85分",
            f"当日访客与基准期日均访客均≥{r.TRAFFIC_MIN}",
        ),
        (
            "conversion_moderate",
            "中度订单转化下滑",
            f"当日订单转化率≤基准×{r.CONVERSION_MODERATE_FACTOR}；P2，规则分70",
            f"当日访客>{r.TRAFFIC_MIN}；基准转化率>0",
        ),
        (
            "traffic_moderate",
            "中度流量下滑",
            f"访客变化≤{r.TRAFFIC_MODERATE_CHANGE * 100:g}%且"
            f"日均访客减少≥{r.TRAFFIC_MODERATE_LOSS}；"
            "P2，规则分65",
            f"当日访客与基准期日均访客均≥{r.TRAFFIC_MIN}",
        ),
    ]
    rows = [
        _rule(
            "products.risk." + code,
            "商品",
            name,
            condition,
            sample + "；" + common,
            window,
            exception,
            r.RULE_VERSION,
            source,
        )
        for code, name, condition, sample in specs
    ]
    rows.append(
        _rule(
            "products.scope",
            "商品",
            "爆品范围、排序与估算影响",
            f"爆品按经营范围所选经营期金额前{r.TOP_SHARE * 100:g}%及临界同额纳入；"
            "风险按P0→P1→P2、规则分排序",
            "爆品资格先按店铺/运营/品类/等级确定，商品选择及关键词只筛展示",
            window,
            f"估算影响按同{r.BASELINE_DAYS}日金额÷订单估计客单价；"
            "金额缺失只影响估算，不阻断风险判断",
            r.RULE_VERSION,
            "app/modules/product_board/query.py；product_board/metrics.py",
        )
    )
    return rows


def _risk_rule_key(label):
    return "products.risk.traffic_severe" if label == risk_rules.TOP_TRAFFIC_ALERT else label


def matched_product_rule(label):
    """把看板关注项关联到本版生效规则。处理状态不参与是否命中的判断。"""
    key = _risk_rule_key(label)
    return next((r for r in _product_risk_rules() if key in (r["name"], r["id"])), None)


def _products():
    version = _revision(
        "product_board/risk_rules.py",
        "product_board/query.py",
        "product_board/metrics.py",
        "product_scoring/rules.py",
        "product_scoring/query.py",
    )
    rows = _product_risk_rules()
    package = score_parameters()
    gates = package["policy"]["gates"]
    rows.append(
        _rule(
            "products.score.policy",
            "商品",
            "新品四档评分与试行范围",
            "各项取达到的最高含等号档位；得分=权重×档位÷4；有效值≤0为0档；缺项不补0",
            "仅纯新品；具体四档线、参照组和样本数随商品已发布标准，须查商品评分详情",
            f"本期{gates['current_window_days']}日、前期{gates['previous_window_days']}日；"
            f"推广曝光门槛{gates['ad_impressions_min']}；增长基数订单门槛{gates['previous_orders_min']}",
            "当前试行标准，不输出完整可信分；完整日/参照效果待验，暂不正式放量；"
            "本行版本是定义指纹，不是商品冻结评分/标准版本，具体相容性须查该商品评分详情",
            version,
            "app/modules/product_scoring/rules.py；product_scoring/query.py",
        )
    )
    for code, label, weight, _ in WEIGHTS:
        branch = next(b for b in package["branches"] if b["code"] == "validate_" + code)
        rows.append(
            _rule(
                "products.score." + code,
                "商品",
                "新品评分：" + label,
                f"权重{weight}；最高四档，单项满分{weight}",
                "取所选商品冻结标准，不存在全商品统一数值线",
                "相容已发布评分窗口",
                (
                    "增长倍数项未验证"
                    if not branch["enabled"]
                    else "试行计分；缺失字段、标准不相容或零分母时该项保留待验证"
                )
                + "；本行定义指纹不是商品冻结评分/标准版本",
                version,
                "app/modules/product_board/metrics.py#WEIGHTS；product_scoring/rules.py",
                "trial" if branch["enabled"] else "not_enabled",
            )
        )
    return rows, version


def _promotion(view):
    labels = {
        field: promotion_metrics.SPECS[key][0] for key, field in promotion_metrics.FIELDS.items()
    }
    version = _revision("promotion/diagnosis.py", "promotion/metrics.py")
    source = "app/modules/promotion/diagnosis.py"
    rows = [
        _rule(
            view + ".formula." + code,
            "推广",
            promotion_metrics.SPECS[code][0],
            f"{labels[numerator]}合计÷{labels[denominator]}合计",
            "店铺按成对完整输入；商品按已知分子分母分别汇总",
            "所选期间，相邻比较窗独立汇总",
            "店铺严格检查已有行缺项；商品部分缺项仅供参考；"
            "非正分母不算；明星店铺无净额不冒充合计净投产；沿来源支付日归因不重算",
            version,
            "app/modules/promotion/metrics.py#RATIO_SPECS",
        )
        for code, numerator, denominator in promotion_metrics.RATIO_SPECS
    ]
    if view == "promotion_store":
        rows.append(
            _rule(
                "promotion_store.diagnosis",
                "店铺推广",
                "店铺模式诊断阈值",
                "未定义商品SOP预警阈值",
                "不适用商品SOP",
                "所选经营期间",
                "店铺模式不使用商品SOP，不从商品预警推定店铺预警",
                version,
                source,
                "not_defined",
            )
        )
        return rows, version
    window = "相邻两个7日历日窗口；截止为所选截止日/来源截至日/昨日三者最早"
    for code, name in (("roi", "投产比"), ("net_roi", "净投产比")):
        rows.append(
            _rule(
                f"promotion_product.{code}_drop",
                "商品推广",
                name + "下降",
                f"相对变化≤{diagnosis.ROI_WARNING_DROP * 100:g}%为P1；"
                f"否则≤{diagnosis.ROI_ATTENTION_DROP * 100:g}%为P2",
                f"本期、前期花费均≥{diagnosis.ROI_MIN_SPEND}；前期比率>0",
                window,
                "各规则独立判断；按精确分子分母比较，不以舍入值判断；缺输入单项不可评",
                diagnosis.RULE_VERSION,
                source,
            )
        )
    rows.extend(
        [
            _rule(
                "promotion_product.spend_growth",
                "商品推广",
                "花费增长与成交承接",
                f"花费增幅≥{diagnosis.SPEND_GROWTH.numerator}/{diagnosis.SPEND_GROWTH.denominator}，"
                f"交易额增幅×{diagnosis.REVENUE_GROWTH_FACTOR}<花费增幅；已有P1则P1，否则P2",
                "前期花费和交易额均>0；不另设花费或笔数下限",
                window,
                "严格小于一半，恰好一半不命中；不能直接推导利润或预算结论",
                diagnosis.RULE_VERSION,
                source,
            ),
            _rule(
                "promotion_product.zero_deals",
                "商品推广",
                "有花费无成交",
                "本期归因成交0笔，P3观察",
                f"本期花费≥{diagnosis.ROI_MIN_SPEND}",
                window,
                "仅观察，归因完整性未确认不升级预警；新品不直接停投",
                diagnosis.RULE_VERSION,
                source,
                "observation_only",
            ),
        ]
    )
    return rows, version


def _sales():
    version = _revision("sales/query.py", "sales/detail_queries.py", "sales/targets.py")
    rows = []
    names = {
        "shop_revenue": "店铺成交金额",
        "shop_visitors": "店铺访客数",
        "buyers": "店铺成交买家数",
    }
    for key, label in (("buyer_conversion", "店铺买家转化率"), ("aov", "店铺客单价")):
        n, d = PERFORMANCE_INPUTS[key]
        rows.append(
            _rule(
                "sales." + key,
                "销售",
                label,
                f"{names[n]}合计÷{names[d]}合计",
                "同店日分子分母同时有效",
                "所选闭区间",
                "缺记录不补0；分母≤0不计算，不平均逐日比率",
                version,
                "app/modules/sales/query.py#PERFORMANCE_INPUTS",
            )
        )
    rows.extend(
        [
            _rule(
                "sales.refund_rate",
                "销售",
                "店铺退款率",
                "同店同日配对的来源退款金额合计÷成交金额合计",
                "两项金额同时有效",
                "所选期间",
                "同日参考，退款与销售不是同一批订单；不再次扣退款",
                version,
                "app/modules/sales/detail_queries.py#refund_totals",
            ),
            _rule(
                "sales.targets",
                "销售",
                "GMV目标达成与缺口",
                "完成额÷相容目标；缺口=max(目标−完成额,0)",
                f"看板只发布 {len(SHOPS)} 家店合计的 GMV 目标。"
                "单店月目标未发布，暂时算不了单店达成率。",
                "自然月/批准财年",
                "仅原版GMV目标生效，不含多多进宝版尚未批准；无目标不算达成率；缺数仅供参考",
                version,
                "app/modules/sales/targets.py#query_targets",
            ),
            _rule(
                "sales.rating",
                "销售",
                "店铺评级分值",
                f"来源原值在{detail_queries.RATING_MIN}..{detail_queries.RATING_MAX}内时"
                f"×{detail_queries.RATING_SCALE}展示",
                "沿来源已有评分，不重算评级",
                "评级卡取自身最新日，趋势取自身所选范围",
                "S/A等等级阈值未定义；负值invalid_score、超范围legacy_basis、NULL与无记录分别表达",
                version,
                "app/modules/sales/detail_queries.py#ratings",
            ),
        ]
    )
    return rows, version


def _aftersales_condition(code, thresholds, templates):
    """展示冻结包原门槛，不填默认值；率差与相对增长区分。"""
    labels = {
        "attention_change_gte": "关注P2的率差下限（乘100为百分点）",
        "warning_change_gte": "预警P1的率差下限（乘100为百分点）",
        "current_sales_denominator_min": "本期销售分母下限（元）",
        "previous_sales_denominator_min": "前期销售分母下限（元）",
        "current_refund_numerator_min": "本期退款分子下限（元）",
        "current_return_qty_min": "本期全部实退数量下限",
        "previous_return_qty_min": "前期全部实退数量下限",
        "current_issue_qty_min": "本期该问题件数下限",
        "top_limit": "实际入库金额核查排名上限",
        "priority": "优先状态",
        "category_min_share": "品类金额份额下限",
        "style_min_share": "单款金额份额下限",
        "sample_cap": "排序样本上限",
        "baseline_floor": "排序基准下限",
        "templates": "报告冻结建议模板",
        "effect_window": "报告冻结观察窗口",
    }
    conditions = [
        f"{labels.get(key, key)}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        for key, value in thresholds.items()
    ]
    if code in {"refund_rate_change", "issue_share_change"}:
        conditions.append("同口径本期率减前期率，达到高级别优先；每项独立判断")
    return "；".join(conditions) or "；".join(templates) or "报告包未提供数值阈值"


def _aftersales(engine, report_id):
    version = _revision(
        "aftersales/metrics.py",
        "aftersales/classification.py",
        "aftersales/decisions.py",
        "aftersales/targets.py",
    )
    rows = [
        _rule(
            "aftersales." + key,
            "售后",
            name,
            formula,
            "报告/品类/款式各自完整原生组；缺额不补0",
            "所选报告固定实际期间",
            "分母≤0不计算；售后确认期间与支付期间不是同一批订单；报告重叠不可相加",
            version,
            "app/modules/aftersales/metrics.py#from_values",
        )
        for key, name, formula in (
            ("presale_rate", "售前退款率", "P÷(S+P)；P=已取消支付销售金额，S=已发货销售金额"),
            ("aftersale_rate", "售后退款率", "A÷S；A=实际入库金额，S=已发货销售金额"),
            ("total_rate", "总退款率", "(P+A)÷(S+P)；A=实际入库金额，P=已取消支付销售金额"),
        )
    ]
    selected = None
    if engine is not None:
        with snapshot(engine, report_id) as repo:
            selected = repo.report
            package = repo.classification_parameters()
            if selected and package:
                rule_version = selected["rule_set_id"]
                for branch in package.get("branches", []):
                    code = branch["code"]
                    threshold = branch.get("thresholds") or {}
                    templates = [
                        s["text"]
                        for s in package.get("suggestions", [])
                        if code in s.get("branch_codes", [])
                    ]
                    rows.append(
                        _rule(
                            "aftersales." + code,
                            "售后",
                            {
                                "refund_rate_change": "三种金额退款率分级",
                                "issue_share_change": "尺码/质量问题占比分级",
                                "separate_risk_ranking": "同层风险排序",
                                "target_application": "整体目标适用范围",
                                "single_style_amount_review": "单款金额核查",
                                "mixed_risk_ranking": "混合层风险排序",
                                "quality_quantity": "质量问题件数去重",
                                "primary_reason": "原退货原因",
                                "size_dictionary": "尺码词典观察",
                                "quality_dictionary": "质量词典观察",
                                "refusal_dictionary": "拒收线索词典",
                            }.get(code, code),
                            _aftersales_condition(code, threshold, templates),
                            "样本门槛按同一冻结包；金额率与件数占比分开判断",
                            "所选报告固定期间及其绑定前期；问题占比需词典相容",
                            "；".join(templates) or "未启用分支不作为有效预警规则",
                            rule_version,
                            f"rule_sets/{rule_version}；{branch.get('evidence_ref') or ''}",
                            "defined" if branch_enabled(package, code) else "not_enabled",
                        )
                    )
                # 仅取报告绑定目标小表；空指标用于复用目标适用范围，绝不查经营行。
                from app.modules.aftersales.query import AfterQuery

                data = add_target_ranges(repo, AfterQuery(), dict(metrics=[], risk_metrics=[]))
                for goal in data["target_ranges"]:
                    rows.append(
                        _rule(
                            "aftersales.target." + goal["metric_code"],
                            "售后",
                            TARGET_NAMES[goal["metric_code"]],
                            f"区间下界={goal['lower_rate']}，上界={goal['upper_rate']}",
                            "仅整体滚动30日或完整自然月报告；不适用筛选后品类/款式",
                            "所选报告固定目标输入",
                            "低于/高于/区间内只描述观察值，不称业务已达标",
                            goal["version_id"],
                            f"refund_targets/{goal['target_id']}",
                        )
                    )
                version = _digest(
                    [version, selected["id"], rule_version, package, data["target_ranges"]]
                )
            else:
                package = None
    else:
        package = None
        if report_id:
            raise BoardError("DATABASE_UNAVAILABLE", 503)
    if not package:
        rows.append(
            _rule(
                "aftersales.rule_package",
                "售后",
                "报告冻结规则包",
                "所选报告无可用固定规则，阈值未定义",
                "无相容规则不评级",
                "固定报告",
                "不使用最新配置补解释历史报告",
                version,
                "app/modules/aftersales/repository.py#classification_parameters",
                "not_defined",
            )
        )
    return rows, version, selected


def read(engine, params):
    """view缺省all；分页最多25项。售后规则始终绑定指定/默认已发布报告。"""
    if not isinstance(params, dict) or set(params) - {
        "view",
        "keyword",
        "report_id",
        "limit",
        "offset",
    }:
        raise BoardError("INVALID_SCOPE")
    view = params.get("view", "all")
    limit, offset = params.get("limit", 10), params.get("offset", 0)
    if (
        not isinstance(view, str)
        or view not in VIEWS
        or type(limit) is not int
        or not 1 <= limit <= 25
        or type(offset) is not int
        or offset < 0
    ):
        raise BoardError("INVALID_SCOPE")
    for key, maximum in (("keyword", 120), ("report_id", 200)):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise BoardError("INVALID_SCOPE")
    report_id = params.get("report_id") or None
    if report_id and view not in {"all", "aftersales"}:
        raise BoardError("INVALID_SCOPE")
    items, versions, selected = [], {}, None
    for domain in ("products", "promotion_store", "promotion_product", "sales", "aftersales"):
        if view not in {"all", domain}:
            continue
        if domain == "products":
            values, revision = _products()
        elif domain.startswith("promotion_"):
            values, revision = _promotion(domain)
        elif domain == "sales":
            values, revision = _sales()
        else:
            values, revision, selected = _aftersales(engine, report_id)
        items.extend(values)
        key = "rules_" + domain
        if domain == "aftersales" and selected:
            key += "_" + selected["id"]
        versions[key] = revision
    keyword = (params.get("keyword") or "").casefold().strip()
    if keyword:
        search_term = _risk_rule_key(keyword)
        items = [r for r in items if search_term in (r["id"] + " " + r["name"]).casefold()]
    return report(
        "definitions",
        "rule_definitions",
        "规则与适用范围",
        "各领域当前共用规则；售后为所选已发布报告冻结规则快照",
        _digest(versions),
        scope=dict(
            view=view,
            keyword=keyword,
            report_id=selected["id"] if selected else report_id,
            limit=limit,
            offset=offset,
        ),
        tables=[
            table(
                "规则定义", COLUMNS, items[offset : offset + limit], total=len(items), offset=offset
            )
        ],
        versions=versions,
        notes=[
            "运营中心复用商品/推广/销售/售后领域结果和规则，不另设一套预警阈值。",
            "当前规则不能自动解释历史对象某次命中；须核对当时规则版本和实际输入。",
            "四档数值线依商品已发布评分标准；本入口不以全局门槛替代商品标准。",
        ],
    )
