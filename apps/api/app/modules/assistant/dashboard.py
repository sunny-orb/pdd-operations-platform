"""业务工具的范围校验与单轮缓存。领域查询和经营公式由各适配模块维护。"""

import json
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.modules.product_board.repository import BoardError

from . import catalog
from .references import refers_to_product

ALIASES = {"product_version": "products", "promotion_version": "promotion_product"}


class DashboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: catalog.Capability
    period: str = Field(default="default", pattern="^(default|selected)$")
    date_from: date | None = None
    date_to: date | None = None
    shop_id: str | None = Field(default=None, max_length=128)
    all_shops: bool = False
    previous_product: bool = False
    listing_ids: list[str] | None = Field(default=None, max_length=20)
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    report_id: str | None = Field(default=None, max_length=128)
    mode: str | None = Field(default=None, pattern="^(product|store)$")
    channel: str = Field(default="", pattern="^(|product_promotion|star_shop)$")
    category: str | None = Field(default=None, max_length=200)
    operator: str | None = Field(default=None, max_length=200)
    level: str | None = Field(default=None, max_length=200)
    keyword: str | None = Field(default=None, max_length=120)
    product_key: str | None = Field(default=None, max_length=200)
    focus: str | None = Field(default=None, pattern="^(all|size|quality)$")
    view: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=10, ge=1, le=25)
    offset: int = Field(default=0, ge=0, le=100000)


def versions(report):
    return {
        ALIASES.get(key, key): value
        for key, value in report.get("versions", {}).items()
        if value is not None
    }


def check_versions(run, report):
    expected = dict(run.scope.versions)
    reference = run.scope.risk_reference
    for key, value in (
        ("products", run.scope.product_version),
        ("promotion_product", run.scope.promotion_version),
    ):
        if value:
            expected[key] = value
    for previous in run.reports:
        expected.update(versions(previous))
    if run.referred_subject and reference:
        if expected.get("products", reference.data_version) != reference.data_version:
            raise BoardError("DATA_VERSION_CHANGED", 409)
        expected["products"] = reference.data_version
    if any(key in expected and expected[key] != value for key, value in versions(report).items()):
        raise BoardError("DATA_VERSION_CHANGED", 409)


async def metadata(run, domain):
    if domain not in run.metadata_cache:
        run.metadata_cache[domain] = await run.query(catalog.describe, domain)
    return run.metadata_cache[domain]


async def discover(run, domain):
    """全域先给短能力清单，选定领域再读取可用日期和有限的选择目录。"""
    data = dict(capabilities=catalog.catalog(domain))
    if domain != "all":
        data["scope"] = await metadata(run, domain)
    data["boundaries"] = ["库存尚无本地查询入口", "售后报告未建立到店铺商品链接的已验证映射"]
    return data


def _shop(value, meta):
    if not value:
        return ""
    for item in meta.get("shops", []):
        ids = [
            item.get(key) for key in ("id", "shop_id", "source_shop_id", "shop_source_id", "value")
        ]
        if value in ids:
            return item.get("id") or item.get("shop_id") or item.get("value")
    # 店铺销售使用内部别名，商品/推广使用来源店铺ID；只在目标目录确有该身份时转译。
    from app.modules.sales.source_policy import SHOPS

    target_ids = {
        str(item.get("id") or item.get("shop_id") or item.get("value"))
        for item in meta.get("shops", [])
    }
    for source_id, (alias, _name, _label) in SHOPS.items():
        if value == alias and source_id in target_ids:
            return source_id
    return value


async def read(run, request):
    from .chat import Clarify, ExplanationContext

    if request.capability == "rule_definitions":
        # 规则依据有独立版本；不能拿指标定义hash或页面经营日期约束纯解释。
        params = request.model_dump(
            include={"view", "keyword", "report_id", "limit", "offset"}, exclude_none=True
        )
        modules = {"all", "products", "promotion_store", "promotion_product", "sales", "aftersales"}
        context = run.scope.explanation
        params.setdefault(
            "view",
            context.view if context else run.scope.domain if run.scope.domain in modules else "all",
        )
        if (
            context
            and context.report_id
            and params["view"] in {"all", "aftersales"}
            and "report_id" not in params
        ):
            params["report_id"] = context.report_id
        if (
            run.scope.report_id
            and params["view"] in {"all", "aftersales"}
            and "report_id" not in params
        ):
            params["report_id"] = run.scope.report_id
        key = ("rules", json.dumps(params, sort_keys=True))
        if key not in run.report_cache:
            result = await run.query(catalog.read, request.capability, params)
            check_versions(run, result)
            # 缺定义仍保留已核验的解释主题，不把它误当作已取得的规则内容。
            run.scope = run.scope.model_copy(
                update={
                    "explanation": ExplanationContext(
                        view=result["scope"]["view"], report_id=result["scope"].get("report_id")
                    )
                }
            )
            rows = [row for part in result["tables"] for row in part["rows"]]
            if rows and all(row.get("state") == "not_defined" for row in rows):
                raise Clarify("；".join(str(row["condition"]) for row in rows[:3]))
            if not rows:
                raise Clarify(
                    "当前没有找到这项适用的生效规则，不能确认阈值。"
                    "请指出提醒名称；售后问题也可以选择一份已发布报告。"
                )
            run.report_cache[key] = result
        return run.report_cache[key]

    mode = (
        request.mode
        or run.scope.mode
        or ("store" if run.scope.domain == "promotion_store" else "product")
    )
    if request.capability.startswith("promotion_"):
        if request.channel or (
            "明星店铺" in run.question
            and not request.listing_ids
            and not request.previous_product
            and not (request.mode == "product" and re.search(r"单款|同款|这款|那款", run.question))
        ):
            mode = "store"
        elif "店铺推广" in run.question and "商品推广" not in run.question:
            mode = "store"
        elif "商品推广" in run.question and "店铺推广" not in run.question:
            mode = "product"
    domain = catalog.domain_for(request.capability, mode)
    meta = await metadata(run, domain)
    params = request.model_dump(exclude_none=True)
    params["data_version"] = meta.get("data_version")
    requested_limit = re.search(r"(?:前|最多|列出?)\s*(\d{1,2})\s*(?:款|家|个|条)", run.question)
    if requested_limit:
        params["limit"] = min(request.limit, max(1, int(requested_limit[1])))
    if domain.startswith("promotion_"):
        params["mode"] = mode
    if request.capability == "data_status" and request.view is None:
        if re.search(r"哪天|哪一天|更新到|截至|截止", run.question):
            params["view"] = "datasets"
            if "limit" not in request.model_fields_set:
                params["limit"] = 25
        elif re.search(r"时效|到数|晚到", run.question):
            params["view"] = "timeliness"
    expected = run.scope.versions.get(domain)
    if expected and expected != params["data_version"]:
        raise BoardError("DATA_VERSION_CHANGED", 409)
    same_domain = run.scope.domain == domain
    if (
        domain == "aftersales"
        and request.report_id is None
        and run.scope.explanation
        and run.scope.explanation.view in {"all", "aftersales"}
        and run.scope.explanation.report_id
    ):
        params["report_id"] = run.scope.explanation.report_id
    explicitly_all = bool(
        re.search(
            r"全部店|所有店|全店铺|全平台|哪个店|哪些店|各店|店铺排名",
            run.question,
        )
    )
    if request.capability == "sales_targets" and re.search(r"六店|渠道", run.question):
        explicitly_all = True
    if request.all_shops and not explicitly_all:
        raise Clarify("请确认要查看全部店铺还是当前店铺。")
    all_shops = explicitly_all or (request.capability == "sales_shops" and not request.shop_id)
    all_products = all_shops or bool(re.search(r"所有商品|全部商品|全店商品", run.question))
    page_shop = run.scope.filter_shop_id or run.scope.shop_id
    if domain not in ("aftersales", "weather", "data_status", "definitions"):
        params["shop_id"] = "" if all_shops else _shop(request.shop_id or page_shop, meta)
        if not request.shop_id and not all_shops and run.scope.shop_ids:
            if len(run.scope.shop_ids) > 1 and domain != "products":
                raise Clarify("当前选了多家店铺，请指定一家店铺，或明确查询全部店铺。")
            shops = [_shop(value, meta) for value in run.scope.shop_ids]
            params["shop_id"] = tuple(shops) if len(shops) > 1 else shops[0]
    else:
        params.pop("shop_id", None)
    if same_domain:
        for key in (
            "category",
            "operator",
            "level",
            "keyword",
            "report_id",
            "product_key",
            "view",
            "focus",
        ):
            if key not in params and getattr(run.scope, key, None):
                params[key] = getattr(run.scope, key)
    for key in ("category", "operator", "level", "keyword"):
        value = getattr(request, key)
        if value and value not in run.question and value != getattr(run.scope, key, None):
            label = {
                "category": "品类",
                "operator": "运营",
                "level": "等级",
                "keyword": "商品关键词",
            }[key]
            raise Clarify(f"请在问题中明确要查询的{label}。")
    if same_domain and domain == "products":
        for plural, single in (
            ("category_values", "category"),
            ("operator_values", "operator"),
            ("level_values", "level"),
        ):
            values = getattr(run.scope, plural)
            if values and getattr(request, single) is None:
                params[single] = tuple(values)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    calendar_month = None
    if re.search(r"本月|这个月|当月", run.question):
        calendar_month = today.strftime("%Y-%m")
    elif re.search(r"上个月|上月", run.question):
        calendar_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    if calendar_month:
        params["month"] = calendar_month
    elif request.month:
        if request.month not in run.question and request.month != run.scope.month:
            raise Clarify("请确认查询月份，可用 YYYY-MM 表示。")
    elif same_domain and run.scope.month:
        params["month"] = run.scope.month

    if domain in ("products", "promotion_product") and all_products:
        params["listing_ids"] = []
    referenced = False
    if domain in ("products", "promotion_product") and not all_products:
        ids = request.listing_ids
        if (
            (request.previous_product or refers_to_product(run.question))
            and not run.subject
            and not run.scope.listing_id
        ):
            product = await run.resolve_reference()
            if ids is not None and ids != [product["listing_id"]]:
                raise Clarify("本轮追问的是上次点名的商品，请按该款查看。")
            ids = [product["listing_id"]]
            referenced = True
        elif ids is None:
            ids = (
                [run.subject["listing_id"]]
                if run.subject
                else run.scope.listing_ids
                or ([run.scope.listing_id] if run.scope.listing_id else [])
            )
        allowed = set(run.scope.listing_ids)
        if run.scope.listing_id:
            allowed.add(run.scope.listing_id)
        if run.subject:
            allowed.add(run.subject["listing_id"])
        if referenced:
            allowed.update(ids)
        for prior in run.reports:
            for table in prior.get("tables", []):
                allowed.update(
                    str(row["listing_id"]) for row in table["rows"] if row.get("listing_id")
                )
        if ids and not set(ids) <= allowed:
            raise Clarify("请先按商品名称或ID定位，或从商品目录中选择商品。")
        params["listing_ids"] = ids or []
    if (
        domain not in ("aftersales", "weather", "data_status", "definitions")
        and request.capability != "sales_targets"
    ):
        first, last, days = run.dates(
            request.period, request.date_from, request.date_to, max_days=366, referenced=referenced
        )
        params.update(date_from=first, date_to=last, recent_days=days)
    if domain == "aftersales":
        if (
            (run.subject or run.scope.listing_id)
            and re.search(r"这款|该商品|这件", run.question)
            and not request.keyword
        ):
            raise Clarify("售后报告暂不能直接对应当前店铺商品链接。请指定报告中的品类或款号再查。")
        params.pop("date_from", None)
        params.pop("date_to", None)
    for unused in ("capability", "period", "all_shops", "previous_product"):
        params.pop(unused, None)
    if domain == "aftersales":
        params = {
            key: value
            for key, value in params.items()
            if key
            in (
                "data_version",
                "report_id",
                "category",
                "keyword",
                "focus",
                "view",
                "limit",
                "offset",
            )
        }
    key = ("dashboard", request.capability, json.dumps(params, sort_keys=True, default=str))
    if key not in run.report_cache:
        cache_options = (
            {"rows_cache": run.aftersales_rows_cache}
            if domain == "aftersales" and run.aftersales_rows_cache is not None
            else {}
        )
        result = await run.query(catalog.read, request.capability, params, **cache_options)
        check_versions(run, result)
        run.report_cache[key] = result
    return run.report_cache[key]


def style_action_question(question: str) -> bool:
    """判断用户是否在要单款优先核查动作，混合问题保留原有工具选择。"""
    return (
        bool(re.search(r"哪一?款|单款|款号|哪个款", question))
        and bool(re.search(r"优先|核查|处理|先查|先做", question))
        and not re.search(
            r"整体|全店|全报告|品类|全局|预警|级别|同比|环比|趋势|推广|销售|库存|周报|月报|"
            r"另外|同时|顺便|分别|退款率|金额|占比|目标|上期|去年|对比|历史",
            question,
        )
    )


def model_view(report, question):
    """向模型只投影本题所需证据；页面仍得到完整、可追溯的报告。"""
    tables = report["tables"]
    facts = report["facts"]
    notes = report["notes"]
    if report["domain"] in {"promotion_store", "promotion_product"} and re.search(
        r"预算|加投|停投|调投|加钱", question
    ):
        notes = [
            *notes,
            (
                "本次数据是历史实际表现。投放试验的预算上限、最低样本、观察期和停投线均未接入。"
                "只能给核查动作和复看指标，具体数值由运营确认，连举例也不要代填。"
                "小额样本限制推断，不能解释为高投产的原因；历史投产不是放量后的边际回报。"
            ),
        ]
    if report["capability"] == "aftersales_overview":
        needed = {"报告来源与覆盖", "优先行动", "规则建议"}
        if re.search(r"品类", question):
            needed.update(("品类风险排序", "品类优先核查建议"))
        else:
            needed.update(("单款风险排序", "单款优先核查建议"))
        for pattern, names in (
            (r"原因|为什么|质量|品质", ("退款原因",)),
            (r"尺码|大小", ("尺码拆分",)),
            (r"阶段|拒收|签收", ("退款阶段",)),
            (r"目标|达标|区间|超标|超出|越界|上限|下限|上界|下界", ("整体目标区间",)),
            (r"历史|趋势|走势|上期|同比|环比|变化|比较|对比", ("历史报告趋势（整体）",)),
        ):
            if re.search(pattern, question):
                needed.update(names)
        if style_action_question(question):
            # 当前报告的品类预警和整体决策不能充当某款的等级或归因。
            needed &= {"单款风险排序", "单款优先核查建议", "尺码拆分", "报告来源与覆盖"}
            facts = []
            notes = [
                "按当前报告筛选范围解释单款。金额占比的分母是该筛选范围的入库金额。"
                "单款风险排序只表示核查顺序；这些行没有单款预警等级。"
                "动作完成时间与效果可评估时间分别按该款的验证指标说明。"
            ]
        tables = [table for table in tables if table["title"] in needed]
        rate_question = question.replace("金额退款率", "退款率")
        three_rates = re.search(r"三率|三[项种个](?:退款)?率", rate_question) or all(
            label in rate_question for label in ("售前", "售后", "总退款率")
        )
        if three_rates and not re.search(
            r"原因|建议|优先|风险|趋势|变化|变动|上期|同比|环比|品类|单款|款号|尺码|分析|分解|金额",
            rate_question,
        ):
            tables = [t for t in tables if t["title"] == "整体目标区间"]
            fields = {"presale_rate", "aftersale_rate", "total_rate"}
            if "整体目标区间" in needed:
                fields.add("risk_target_gap")
            facts = [f for f in facts if f["key"] in fields]
    # 金额保留精确元，常用的万元展示由 Decimal 换算，模型可直接引用。
    display_facts = []
    with localcontext(rounding=ROUND_HALF_UP):
        for entry in facts:
            if entry["unit"] == "currency" and entry["value"] is not None:
                amount = Decimal(str(entry["value"]))
                if abs(amount) >= 10000:
                    entry = {**entry, "display": f"{amount / 10000:,.2f} 万元"}
            if entry["unit"] == "percent" and entry["value"] is not None:
                entry = {**entry, "display": f"{Decimal(str(entry['value'])) * 100:.2f}%"}
            display_facts.append(entry)
    compact = []
    for table in tables:
        columns = [
            column
            for column in table["columns"]
            if column["key"] not in ("rule_set_id", "data_version")
        ]
        if report["capability"] == "products_risks" and not re.search(
            r"成交额|销售额|金额|交易额|卖了多少钱", question
        ):
            columns = [
                c for c in columns if c["key"] not in ("period_revenue", "evaluation_day_revenue")
            ]
        compact.append(
            dict(
                title=table["title"],
                total=table["total"],
                offset=table["offset"],
                columns=[
                    dict(key=column["key"], label=column["label"], unit=column["unit"])
                    for column in columns
                ],
                rows=[
                    [
                        f"{Decimal(str(row[column['key']])) * 100:.2f}%"
                        if column["unit"] == "percent" and row.get(column["key"]) is not None
                        else row.get(column["key"])
                        for column in columns
                    ]
                    for row in table["rows"]
                ],
            )
        )
    scope = {key: value for key, value in report["scope"].items() if key != "rule_set_id"}
    return dict(
        title=report["title"],
        period=report["period"],
        scope=scope,
        facts=display_facts,
        tables=compact,
        notes=notes,
        series=report["series"],
        table_format="rows每行的值与columns顺序一一对应；total是完整数量，offset为已跳过行数",
        rule_reference={
            "source": report["source"],
            "data_version": report["data_version"],
            "rule_version": report["scope"].get("rule_version"),
        },
    )
