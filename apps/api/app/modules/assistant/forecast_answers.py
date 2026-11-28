"""月度预测的自然语言入口与范围校验。飞书来源与业务计算由领域模块负责。"""

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.modules.knowledge.passages import operational_terms
from app.modules.product_board.repository import BoardError
from app.modules.sales.contracts import SalesError
from app.modules.sales.source_policy import SHOPS

from . import catalog

_AMOUNT = re.compile(r"gmv|成交金?额|交易金?额|销售[收金额]|能卖|卖多少", re.I)
_ESTIMATE = re.compile(
    r"预测|预估|预计|估一?[下算]|推[演算]|大概能|有望|能卖|月底.{0,6}(?:能到|能达|多少)"
)
_METHOD = re.compile(
    r"公式|权重|怎么算|怎么计算|如何计算|方法|口径|怎么预测|如何预测|"
    r"(?:用|采用|按).{0,6}(?:种|个|什么|啥)模型|SOP.{0,6}(?:写|说|要求|规定|内容)",
    re.I,
)
_MONTH = re.compile(r"(?:(?<!\d)(\d{4})\s*[-年/]\s*)?(?<!\d)(1[0-2]|0?[1-9])\s*月")
_GLOBAL = re.compile(r"六店|全(?:部|平台|渠道|店铺)|整(?:个渠道|个平台|体)|全局")
_SINGLE = re.compile(
    r"这家|该店|单店|每[家店]|分店|各店|这个款|这款|单品|该商品|本店|旗舰店|专卖店"
)
_SHOP_NAMES = set(name for _, full, short in SHOPS.values() for name in (full, short))
_OTHER = re.compile(
    r"顺便|另外|再[看查问]|同时.{0,3}[看查]|(?:并|以及|还有|和).{0,5}(?:退款|退货|商品排行|天气|库存|推广花费)"
)
_AS_OF = re.compile(
    r"(?:截至|截止|算到|算至)\s*(?:(\d{4})[-年/.])?(\d{1,2})[月/.-](\d{1,2})(?:日|号)?"
)
_SINGLE_MESSAGE = "这篇预测 SOP 适用于拼多多六店合计。要看整个平台，可以问「预测本月六店 GMV」。"


_REVISION = r"第几版|哪(?:一|个)?版(?:本)?|版本号|修订号"
_CONFLICT = r"哪两句(?:矛盾|冲突)|哪里(?:矛盾|冲突)|为什么暂停(?:预测)?|冲突在哪"
_HYPOTHETICAL = re.compile(r"如果|假设|假如|试算|改为|改成")
_WEIGHT = re.compile(
    r"(?:近|最近)\s*(\d{1,2})\s*[日天](?:的?销售日均|的?日均)?"
    r"(?:的?权重)?\s*(?:改[为成]|调[为至]|取|为|占|按|给|[:：])?\s*(\d{1,3}(?:\.\d{1,2})?)\s*[%％]"
)
_FOLLOWUP = re.compile(
    r"(?:那|这个|它的|具体|这次)?(?:权重|公式|依据|口径|上次差多少|目标还差多少|还能达标吗|"
    rf"怎么算的|保守情景|乐观情景|基准情景|正式预测|按原来的算|{_CONFLICT}|(?:用的(?:是)?)?(?:{_REVISION}))"
    r"(?:是多少|是什么|呢|多少)?[？?。]?"
)
_OTHER_TOPIC = re.compile(
    r"天气|气温|降雨|售后|退[款货]|投诉|商品风险|新品评分|ROI|投产|转化率|客单价|访客|库存", re.I
)
_COMPOUND = re.compile(r"以及|同时|顺便|另外|分别|还[要想]|和|与|及|再[查问看]|[，,、]")
_CATALOGUE = re.compile(
    r"(?:哪些|哪[几些][篇份本个]?|什么)(?:公共|业务|可用的?)?SOP|SOP(?:清单|目录)|知识库里(?:有|收录)了?哪些",
    re.I,
)
_MONTH_NAMES = "一月 二月 三月 四月 五月 六月 七月 八月 九月 十月 十一月 十二月".split()


def catalogue_question(question):
    return bool(_CATALOGUE.search(re.sub(r"\s+", "", question))) and not re.search(
        r"售后|退款|推广|商品|库存|天气", question
    )


def forecast_followup(question):
    return bool(_FOLLOWUP.fullmatch(re.sub(r"\s+", "", question)))


def revision_question(question):
    return bool(re.search(rf"{_REVISION}|SOP的?版本[？?。]?$", question, re.I))


def asks_channel_promotion_guidance(question, scope):
    """简单投放汇总可一次取齐；有指定对象、细分或其他业务问题时留给 Agent 选择查询。"""
    if not re.search(
        r"(?:结合|对照|参考)(?:看板)?(?:本期|当前|(?:最?近|过去)[一二三四五六七八九十\d]+[天日周])?的?"
        r"(?:实际)?(?:店铺)?(?:推广|投放)(?:汇总|数据|表现)"
        r"(?:[，,。？? ]*(?:(?:告诉我|帮我看看)?先看什么|给个?建议|回答))?[。？?！! ]*$",
        question,
    ):
        return False
    if re.search(
        r"商品|单款|单品|排行|渠道拆分|明细|逐日|趋势|分店|哪家|哪个店|各店|分别|"
        r"预算|库存|天气|售后|退货|退款|归因|目标达成|明星店铺|销售速度|地区|销量排名|客单价|同时|另外|顺便",
        question,
    ):
        return False
    return not (
        _SINGLE.search(question)
        or any(name in question for name in _SHOP_NAMES)
        or scope.listing_id
        or scope.listing_ids
        or scope.product_key
        or scope.risk_reference
        or re.search(r"\d{7,}", question)
    )


async def prepare_guidance(run, *, needs_data=True):
    """取得本题可引用的原文与经营摘要，复用领域的筛选及口径。汇总源暂不可用时返回空提示。"""
    from . import dashboard

    question = run.question
    source = await dashboard.read(
        run, dashboard.DashboardRequest(capability="business_knowledge", document_key="monthly_gmv")
    )
    run.model_reports.add(id(source))
    evidence = [dashboard.model_view(source, question)]
    instruction = "根据原文和问题补查实际经营指标，再给核查动作与复看指标，附原文链接。"
    if not needs_data:
        run.answer_from_reports = True
        instruction = "本题只问 SOP 做法。用相关原文说明步骤及出处；原文未规定的事项如实说明。"
    elif asks_channel_promotion_guidance(question, run.scope):
        try:
            promotion = await dashboard.read(
                run, dashboard.DashboardRequest(capability="promotion_overview", mode="store")
            )
        except (BoardError, SalesError) as exc:
            if exc.status == 503:
                return source, None
            raise
        evidence.append(dashboard.model_view(promotion, question))
        run.model_reports.add(id(promotion))
        run.answer_from_reports = True
        instruction = (
            "原文与推广汇总已取齐。先给该报告实际期间的一项指标，再给核查动作和复看指标，附原文链接。"
            "原文要求但本轮未查的其他窗口只列为下一步。复看可对照有出处的上期值，不能自设达标线。"
        )
    return source, "\n" + instruction + "\n只读资料：" + json.dumps(evidence, ensure_ascii=False)


def forecast_intent(question: str, prior: str = "", *, has_context=False) -> str | None:
    text = re.sub(r"\s+", "", question)
    if (
        re.search(r"(?i:GMV).{0,8}预测|预测.{0,8}(?i:GMV)|月(?:度|末)预测", text)
        and "SOP" in text.upper()
        and operational_terms(text)
        and re.search(r"核查|排查|建议|先看|怎么查|如何判断", text)
        and not re.search(r"预计.{0,6}多少|能卖多少|预测一下", text)
    ):
        real_data = re.search(r"结合|对照看板|当前表现|本期表现|实际.*数据|用.*数据", text)
        return "advice_with_data" if real_data else "guidance"
    if re.search(_CONFLICT, text) and ("预测" in text or (has_context and forecast_followup(text))):
        return "knowledge"
    if (
        _HYPOTHETICAL.search(text)
        and ("权重" in text or _WEIGHT.search(text))
        and (has_context or (_ESTIMATE.search(text) and _AMOUNT.search(text)))
        and not _OTHER_TOPIC.search(text)
    ):
        return "forecast"
    if forecast_followup(text) and (
        has_context or (prior and forecast_intent(prior) in {"knowledge", "forecast", "mixed"})
    ):
        asks_method = (
            _METHOD.search(text)
            or re.search(_CONFLICT, text)
            or "依据" in text
            or revision_question(text)
        )
        return "knowledge" if asks_method else "forecast"
    if catalogue_question(text):
        return "catalogue"
    amount = _AMOUNT.search(text)
    if (_OTHER_TOPIC.search(text) or "推广" in text) and not amount:
        return None
    if not (_ESTIMATE.search(text) and amount) and not (
        "预测" in text and re.search(r"SOP|公式|权重|口径|方法|怎么算|怎么做|依据", text, re.I)
    ):
        return None
    if _OTHER.search(text) or (amount and _OTHER_TOPIC.search(text) and _COMPOUND.search(text)):
        return "mixed"
    if _METHOD.search(text) or revision_question(text) or ("SOP" in text.upper() and not amount):
        return "knowledge"
    return "forecast"


def forecast_period(question, selected_month, today):
    """明确的月份优先；日期指明“截至”时保留当日，转换成领域查询的排他上界。"""
    from .chat import Clarify

    for i in range(11, -1, -1):
        question = question.replace(_MONTH_NAMES[i], f"{i + 1}月")
    question = re.sub(
        r"(?:按|根据|依据|用)(?:照)?\s*((?:\d{4}[-年/.])?\d{1,2}[月/.-]\d{1,2}(?:日|号)?)"
        r"的?(?:完整)?(?:销售日报|经营数据|日报|数据|实绩)",
        r"截至\1",
        question,
    )
    cutoffs = list(_AS_OF.finditer(question))
    if len({cut.groups() for cut in cutoffs}) > 1:
        raise Clarify("请指定本次回算的一个截止日。")
    asof = cutoffs[0] if cutoffs else None
    period_text = _AS_OF.sub("", question)
    if re.search(
        r"今天|今日|昨天|昨日|前天|后天|本日|[0-9]+月[0-9]+[日号]|(?<![近前后])[0-9]+号|"
        r"(?<![0-9])[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}",
        period_text,
    ):
        raise Clarify(
            "这篇 SOP 预测六店全月 GMV。回算历史截止日时可问「截至11月25日，预计月底能卖多少」。"
        )
    if re.search(r"季度|季末|全年|年底|未来\s*\d+\s*[天周月]|下周|下星期|明天|本周", question):
        raise Clarify("这篇 SOP 按自然月预测六店 GMV，请指定一个月份。")
    if re.search(r"截至|截止|算到|算至", question) and not asof:
        raise Clarify("要回看某天的预测，请写明截止日期，例如「截至11月25日」。")
    year = today.year - int("去年" in question) + int("明年" in question)
    months = set()
    for m in _MONTH.finditer(question):
        months.add(date(int(m[1] or year), int(m[2]), 1))
    for year, number in re.findall(r"(?<!\d)(\d{4})-(0[1-9]|1[0-2])(?![-\d])", question):
        months.add(date(int(year), int(number), 1))
    selected = date.fromisoformat(selected_month + "-01") if selected_month else None
    current = today.replace(day=1)
    if re.search(r"(?:本|当|这个|这)月", question):
        months.add(current)
    if re.search(r"(?:上个?月)", question):
        months.add((current - timedelta(days=1)).replace(day=1))
    if re.search(r"(?:下个?月)", question):
        months.add((current + timedelta(days=32)).replace(day=1))
    if asof and (asof[1] or not months):
        try:
            months.add(date(int(asof[1] or year), int(asof[2]), 1))
        except ValueError as exc:
            raise Clarify("截止月份无效，请填写真实日期。") from exc
    if len(months) > 1:
        raise Clarify("请分开指定月份，每次预测使用一个自然月的完整销售日。")
    month = next(iter(months), selected or current)
    try:
        cutoff = date(int(asof[1] or month.year), int(asof[2]), int(asof[3])) if asof else None
    except ValueError as exc:
        raise Clarify("截止日期无效，请用真实的年月日。") from exc
    if cutoff and (cutoff.replace(day=1) != month or cutoff >= today):
        raise Clarify("回看预测的截止日须在指定月份内，并且早于今天。")
    return month, cutoff


async def read_forecast(run, request):
    from .chat import Clarify

    question = run.question
    if not run.public_sops_enabled:
        raise Clarify("当前环境尚未接入飞书公共 SOP。")
    method = request.capability == "business_knowledge"
    selected = run.scope
    hypothesis = _HYPOTHETICAL.search(question)
    asks_weights = bool(hypothesis and ("权重" in question or _WEIGHT.search(question)))
    remembered = selected.forecast if forecast_followup(question) or asks_weights else None
    if method:
        params = {"keyword": request.keyword or question}
        if request.document_key:
            params["document_key"] = request.document_key
    else:
        if re.search(
            r"净(?:gmv|成交|销售)|推广(?:归因|成交|销售|gmv)|分品类|各品类|各商品|各渠道",
            question,
            re.I,
        ):
            raise Clarify("这篇 SOP 预测六店日报 GMV。品类、净成交或推广归因预测暂无已核口径。")
        if hypothesis and re.search(r"加投|断货|提价|降价|预算|涨幅|增长|下跌|促销", question):
            raise Clarify("自设涨幅或加投金额缺少换算依据，可以先看六店的基准预测。")
        weights = None
        if asks_weights:
            matches = _WEIGHT.findall(question)
            parts = sorted((int(days), Decimal(p) / 100) for days, p in matches)
            if (
                len(parts) != 2
                or len({n for n, _ in parts}) != 2
                or sum(w for _, w in parts) != 1
                or any(not 0 < w < 1 for _, w in parts)
                or len(re.findall(r"[%％]", question)) != 2
            ):
                raise Clarify(
                    "请写出两个销售窗口和合计 100% 的权重，例如「近3日占60%、近7日占40%」。"
                )
            weights = tuple((n, format(w.normalize(), "f")) for n, w in parts)
        elif hypothesis and re.search(r"[%％]|万|元", question):
            raise Clarify("该假设缺少可核验的换算依据，可以先看六店的基准预测。")
        if (
            _SINGLE.search(question)
            or any(n in question for n in _SHOP_NAMES)
            or (
                not remembered
                and not _GLOBAL.search(question)
                and any(
                    (
                        request.shop_id,
                        request.listing_ids,
                        request.category,
                        request.operator,
                        request.product_key,
                        selected.shop_id,
                        selected.filter_shop_id,
                        selected.shop_ids,
                        selected.listing_id,
                        selected.listing_ids,
                        selected.category,
                        selected.category_values,
                        selected.operator,
                        selected.operator_values,
                        selected.level,
                        selected.level_values,
                        selected.product_key,
                    )
                )
            )
        ):
            raise Clarify(_SINGLE_MESSAGE)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        month, cutoff = forecast_period(
            question, remembered.month if remembered else selected.month, today
        )
        if (
            remembered
            and remembered.cutoff
            and not cutoff
            and remembered.month == month.strftime("%Y-%m")
        ):
            cutoff = remembered.cutoff
            if cutoff.replace(day=1) != month or cutoff >= today:
                raise Clarify("请确认这次预测需要回看的销售截止日。")
        if request.month and request.month != month.strftime("%Y-%m"):
            raise Clarify("请在问题中明确要预测的月份。")
        if request.date_from or (request.date_to and request.date_to != cutoff):
            raise Clarify("这篇 SOP 从月初累计，回看时请在问题中写明截止日期。")
        params = {"month": month.strftime("%Y-%m"), "cutoff": cutoff}
        if weights:
            params["scenario_weights"] = weights
    key = (request.capability, tuple(sorted(params.items())))
    if key not in run.report_cache:
        options = {"sop_session": run.sop_session} if "document_key" in params else {}
        try:
            data = await run.query(
                catalog.read, request.capability, params, executor=run.sop_executor, **options
            )
        except Clarify as exc:
            run.record_review_usage(getattr(exc, "review_usage", {}))
            raise
        run.record_review_usage(data.pop("review_usage", {}))
        if data.get("knowledge_state") and data.get("forecast_status") == "unavailable":
            return data
        for previous in run.reports:
            for name, right in data["versions"].items():
                left = previous.get("versions", {}).get(name)
                if (
                    (name == "forecast_sop" or name.startswith("sop_"))
                    and left
                    and right
                    and left != right
                ):
                    raise BoardError("DATA_VERSION_CHANGED", 409)
        run.report_cache[key] = data
    return run.report_cache[key]
