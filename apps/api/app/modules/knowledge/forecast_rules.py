"""从当前 SOP 的正式条款编译可存档的受控规则。正文只作数据，不作代码。"""

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext

from .forecast_calculation import calculate_forecast

__all__ = ["REPORT_ORDER", "ForecastRuleError", "calculate_forecast", "compile_forecast_rule"]


class ForecastRuleError(ValueError):
    """当前修订无法安全计算。异常文字可记录，给运营看的原文单独存放。"""

    def __init__(self, message: str, *, issues: list[dict] | None = None):
        super().__init__(message)
        self.issues = issues or []


@dataclass(frozen=True)
class _Clause:
    section: str
    quote: str
    text: str
    model_visible: bool = True
    unverified_heading: bool = False

    def citation(self):
        return {"section": self.section, "quote": self.quote}


def _issue(message, *citations):
    return ForecastRuleError(message, issues=[{"message": message, "citations": list(citations)}])


_MARKUP = re.compile(r"[\x60]|</?[a-zA-Z][\w-]*(?:\s[^>]*)?>")
_STRONG = re.compile(r"(?<!\d)(\*\*|__)(?!\s)(.+?)(?<!\s)\1(?!\d)", re.DOTALL)
_LINK = re.compile(r"((?:^|[。；;！？!?\n])\s*)?\[([^\]\n]+)\]\(", re.MULTILINE)
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_EXAMPLE = re.compile(r"^(?:算例|示例|样例|举例|案例|演算示例)")
_INLINE_EXAMPLE = re.compile(r"(?:^|[,:])(?:例如|比如|举例|算例|示例|样例)")
_BARE_URL = re.compile(r"(?:https?|ftp)://[^\s<>\"'，。；！？、）]+", re.I)
_ORDINAL = re.compile(
    r"^(?:第[\d一二三四五六七八九十]+[章节步]|"
    r"[一二三四五六七八九十]+[.、,):]|\d+(?:\.\d+)*[.、,):]?)"
)
# 标题限定整章数值的适用范围；只接受已解释的业务主题及无条件的编辑栏目。
_TOPIC_HEADING = re.compile(
    r"(?:拼多多)?(?:月度|月末|月底)(?:gmv)?(?:滚动)?预测(?:sop|测试|规则|方法|说明)?|"
    r"(?:确定|核对)?月份,店铺(?:和|与|及)数据截止日|范围(?:核对)?|"
    r"(?:取数(?:并|及|与)(?:核对|核验)口径|数据(?:来源|口径|核对))|"
    r"(?:算出|计算|核对)?销售速度(?:和|与|及)(?:实际)?投放效率|销售(?:和|与|及)推广|"
    r"推演剩余销售,?再对照目标|中心估计|加权日均|情景区间|目标与进度|"
    r"用经营证据判断情景(?:能否|是否可以)实现|(?:经营|复核)依据|"
    r"复核,留档(?:和|及|与)通知|(?:汇报|报告)(?:结构|顺序)?|"
    r"(?:修订|修改|维护|阅读|使用)(?:说明|记录)|附录(?:[:·]?(?:修订记录|参考资料))?|"
    r"(?:(?:月末|月底)?(?:gmv|金额|成交额|预测|中心估计|加权日均|权重)(?:的)?)?"
    r"(?:补充|附加)(?:说明|条款|约定|规则)"
)
_SENTENCE_END = re.compile(r"[。；;！？!?]+|\.(?=\s|$)")


def _normalize(value):
    plain = unicodedata.normalize("NFKC", _MARKUP.sub("", _STRONG.sub(r"\2", value))).lower()
    plain = re.sub(r"\s+", "", plain).strip("。；;.")
    return plain.translate(
        str.maketrans(
            {
                "×": "*",
                "÷": "/",
                "−": "-",
                "–": "-",
                "「": "",
                "」": "",
                "“": "",
                "”": "",
                "、": ",",
            }
        )
    )


def _plain_links(markdown):
    """保留 Markdown 链接标签，完整跳过带查询参数、转义和嵌套括号的目的地址。"""
    result, start = [], 0
    while match := _LINK.search(markdown, start):
        result.append(markdown[start : match.start()])
        end, depth = match.end(), 1
        while end < len(markdown) and depth:
            char = markdown[end]
            end += 1
            if char == "\\":
                end = min(len(markdown), end + 1)
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        if depth:
            raise ForecastRuleError("正文包含未闭合的资料链接，无法确定正式条款边界。")
        result.append((match[1] or "") + match[2] + ("。" if match[1] is not None else ""))
        start = end
    result.append(markdown[start:])
    return _BARE_URL.sub("资料链接", "".join(result))


def _clauses(content: str) -> Iterator[_Clause]:
    section, example_depth, lines = "正文", None, []
    # +fetch 将文档名作为独立的首行元信息返回。后文同名标记仍属于正文。
    content = re.sub(r"\A\s*<title>[^\n<]*</title>[ \t]*(?:\r?\n|$)", "", content, count=1)

    def paragraph():
        # 只移除 URL。句末的独立来源链接不能粘到下条规则；带停用或限定含义的标签仍核验。
        body = _plain_links("\n".join(lines)).strip()
        # 飞书会返回「**目标。**随后……」，先还原可见文字，再切句才能保留完整原句。
        body = _STRONG.sub(r"\2", body)
        example_in_paragraph = False
        for part in _SENTENCE_END.split(body):
            quote = re.sub(r"^\s*(?:[-*·>]\s+)?", "", part).strip()
            normalized = _normalize(quote)
            if normalized:
                # 内联算例可能把金额写在随后的句子里。整段自标记处起仅留在本地核验。
                example_in_paragraph = example_in_paragraph or bool(
                    _INLINE_EXAMPLE.search(normalized)
                )
                yield _Clause(section, quote, normalized, not example_in_paragraph)
        lines.clear()

    for line in content.splitlines():
        heading = _HEADING.fullmatch(line)
        if heading:
            yield from paragraph()
            depth = len(heading[1])
            if example_depth is not None and depth > example_depth:
                continue
            section = _plain_links(heading[2].strip())
            heading_text = _ORDINAL.sub("", _normalize(section))
            example_depth = depth if _EXAMPLE.match(heading_text) else None
            if example_depth is None and not _TOPIC_HEADING.fullmatch(heading_text):
                yield _Clause(section, section, heading_text, unverified_heading=True)
        elif example_depth is None:
            if not line.strip() or re.match(r"^\s*[-*]\s+", line):
                yield from paragraph()
            if line.strip():
                lines.append(line)
    yield from paragraph()


_ACTUAL = r"(?:本月|当月|月)?(?:已)?累计(?:的)?(?:gmv|成交额|成交金额|销售额)"
_CENTER = r"(?:月底|月末)(?:gmv)?(?:中心估计|中心预测|预测中心|中心值)(?:值)?"
_REMAINING = r"(?:本月|当月)?(?:剩余|余下)(?:自然)?天数"
_PERIOD = r"(?:最近|近|过去)([1-9]\d?)(?:个)?[日天]"
_AVERAGE = _PERIOD + r"(?:的)?(?:销售日均|gmv日均|日均(?:gmv|销售额|成交额)?)"
_WEIGHT = r"((?:\d{1,3}(?:\.\d{1,8})?%|0\.\d{1,8}|0|1))"
_MULTIPLY = r"(?:\*|乘以)"
_ALLOCATION = r"(?:的)?(?:(?:预测)?(?:权重|比重|占比)(?:应)?(?:是|为|占|取)?|占)"
_TERM_AFTER = re.compile(_AVERAGE + r"(?:" + _MULTIPLY + "|" + _ALLOCATION + ")" + _WEIGHT)
_TERM_BEFORE = re.compile(_WEIGHT + _MULTIPLY + _AVERAGE)
_WINDOW_SHARE = re.compile(_PERIOD + _ALLOCATION + _WEIGHT)
_MEAN = r"加权(?:日均|平均日销售额|日平均销售额)(?:gmv|销售额)?"
_CENTER_FORMULA = re.compile(
    _CENTER
    + r"(?:计算公式|公式)?(?:=|为:?|是:?|:|(?:先)?取)"
    + _ACTUAL
    + r"(?:\+|,?(?:再)?加上)"
    + _REMAINING
    + _MULTIPLY
    + r"(?:\(([^()]+)\)|"
    + _MEAN
    + ")"
)
_NAMED_MEAN = re.compile(_MEAN + r"(?:=|为|是|按|取|采用|:):?(.+)")
_ADDITIONAL = re.compile(
    r"^(?:另外|此外|其中|同时|(?:另)?(?:按(?:照)?|依据)?(?:权重)?补充(?:说明|约定|条款))[:,]?"
)

# 口述的总额关系与每日口径分别核对。每个词项只有一个量纲，整句中没有被解释的
# 条件、金额、运算符或乘数都不允许跨过。加和的两项也可按相反顺序书写。
_AMOUNT = r"(?:gmv|成交金?额|销售额|销售收入)"
_FUTURE = r"(?:本月|当月)?(?:剩余|余下|剩下)"
_END_TOTAL = r"(?:本月|当月)?(?:月底|月末)(?:的)?(?:预计|预测|预估)?"
_DEFINE = r"(?:按(?:照)?|取|用|采用|等于|为|是|=|:)"
_PLUS = r"(?:\+|,?(?:再)?加(?:上)?)"
_PERIOD_AMOUNT = (
    _FUTURE + r"(?:自然)?(?:天数|日子|各天)(?:的)?(?:预计|预估|预测)(?:的)?(?:销售额?|gmv|成交额)"
)
_DAILY_AMOUNT = _FUTURE + r"(?:的)?(?:每日|每天|日均)(?:的)?(?:销售额?|成交额|gmv)"
_MONTH_TOTAL = rf"(?:{_CENTER}|{_END_TOTAL}{_AMOUNT})"
_MONTH_EQUATION = re.compile(
    rf"{_MONTH_TOTAL}{_DEFINE}"
    rf"(?:{_ACTUAL}{_PLUS}{_PERIOD_AMOUNT}|{_PERIOD_AMOUNT}{_PLUS}{_ACTUAL})"
    rf"(?:来算|计算|估算|推算)?"
)
_DAILY_EQUATION = re.compile(rf"{_DAILY_AMOUNT}{_DEFINE}(.+)")
_EQUATION_BOUNDARY = re.compile(rf",(?={_MONTH_TOTAL}|{_DAILY_AMOUNT})")
_ORDERED_WEIGHTS = re.compile(
    _PERIOD
    + r"(?:与|和|及|,)"
    + _AVERAGE
    + r",?(?:权重|比重|占比)分别(?:是|为|占|取)"
    + _WEIGHT
    + r"(?:与|和|及|,)"
    + _WEIGHT
)

# 只在紧邻天数、百分比或成数单位时认中文数字；日期、经营金额和其他词不充当权重。
_CN_DIGITS = {
    "零": "0",
    "〇": "0",
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}
_CN_INTEGER = r"(?:一百|[一二两三四五六七八九]?十[一二三四五六七八九]?|[零〇一二两三四五六七八九])"
_CN_NUMBER = _CN_INTEGER + r"(?:点[零〇一二三四五六七八九]{1,8})?"
_NUMBER = r"(?:\d{1,3}(?:\.\d{1,8})?|" + _CN_NUMBER + ")"
_PERCENT_SPOKEN = re.compile(r"百分之(" + _NUMBER + ")")
_TENTHS = re.compile("(" + _NUMBER + ")成")
_DAY_COUNT = re.compile("(" + _CN_INTEGER + r")(?=个?[日天])")
_WEEK_COUNT = re.compile(r"(最近|近|过去)(" + _CN_INTEGER + r"|\d{1,2})(?:个)?(?:星期|周)")


def _number(token):
    if token[0].isascii():
        return Decimal(token)
    whole, _, fractional = token.partition("点")
    if whole == "一百":
        value = "100"
    elif "十" in whole:
        tens, ones = whole.split("十")
        value = _CN_DIGITS.get(tens, "1") + _CN_DIGITS.get(ones, "0")
    else:
        value = _CN_DIGITS[whole]
    if fractional:
        value += "." + "".join(_CN_DIGITS[c] for c in fractional)
    return Decimal(value)


def _units(text):
    with localcontext(Context(prec=32)):
        text = _PERCENT_SPOKEN.sub(lambda m: f"{_number(m[1]):f}%", text)
        text = _TENTHS.sub(lambda m: f"{_number(m[1]) * 10:f}%", text)
        text = _WEEK_COUNT.sub(lambda m: f"{m[1]}{_number(m[2]) * 7}日", text)
        return _DAY_COUNT.sub(lambda m: str(_number(m[1])), text)


def _terms(expression, *, partial=False):
    with localcontext(Context(prec=32)):
        return _parse_terms(expression, partial)


def _parse_terms(expression, partial):
    bindings = []
    if ordered := _ORDERED_WEIGHTS.fullmatch(expression):
        first, second, first_weight, second_weight = ordered.groups()
        bindings = [(first, first_weight), (second, second_weight)]
    else:
        items = re.split(r"\+|(?:再)?加上|,|与|和|及", expression.removesuffix("之和"))
        for item in items:
            if after := _TERM_AFTER.fullmatch(item) or _WINDOW_SHARE.fullmatch(item):
                bindings.append(after.groups())
            elif before := _TERM_BEFORE.fullmatch(item):
                bindings.append((before[2], before[1]))
            else:
                raise ForecastRuleError("中心估计包含尚未接入的指标、运算或权重。")
    terms = {}
    for days, percent in bindings:
        days = int(days)
        fraction = Decimal(percent[:-1]) / 100 if percent.endswith("%") else Decimal(percent)
        if not 1 <= days <= 31 or not 0 <= fraction <= 1 or days in terms:
            raise ForecastRuleError("中心估计的日窗口或权重超出可执行范围。")
        terms[days] = fraction
    if not (partial and len(terms) == 1) and len(terms) != 2:
        raise ForecastRuleError("中心估计需要两个不同的日窗口。")
    return [{"days": n, "weight": format(terms[n].normalize(), "f")} for n in sorted(terms)]


def _weight_statement(text):
    """整句必须能说明中心运算或窗口的权重。独立的单项声明也要与其他章节对账。"""
    if match := _CENTER_FORMULA.fullmatch(text):
        return True, _terms(match[1]) if match[1] else []
    if match := _NAMED_MEAN.fullmatch(text):
        return False, _terms(match[1])
    statement = _ADDITIONAL.sub("", text)
    if (
        _WINDOW_SHARE.match(statement)
        or _TERM_AFTER.match(statement)
        or _ORDERED_WEIGHTS.fullmatch(statement)
    ):
        return False, _terms(statement, partial=True)
    return None


def spoken_weight_statements(clauses):
    """核对余下期间的预计销售总额与每日预计销售额，两项可换序、分句或分段。"""
    statements = {}
    period_references = []
    defined_daily = False
    for n, clause in enumerate(clauses, 1):
        parts = _EQUATION_BOUNDARY.split(_units(clause.text))
        totals = [part for part in parts if _MONTH_EQUATION.fullmatch(part)]
        daily = [match for part in parts if (match := _DAILY_EQUATION.fullmatch(part))]
        if len(totals) + len(daily) != len(parts) or len(totals) > 1 or len(daily) > 1:
            continue
        if totals and not daily:
            period_references.append(clause.citation())
        try:
            terms = _terms(daily[0][1]) if daily else []
        except ForecastRuleError as exc:
            raise _issue(str(exc), clause.citation()) from None
        defined_daily = defined_daily or bool(daily)
        statements[n] = bool(totals), terms
    if period_references and not defined_daily:
        raise _issue("余下期间的预计销售额缺少对应的每日销售预测口径。", *period_references)
    return statements


_TWO_WINDOWS = _PERIOD + r"(?:与|和|及|,)" + _PERIOD
_DISPLAY = re.compile(
    r"(?:并列)?(?:展示|列出)" + _TWO_WINDOWS + r"(?:与|及|和)月累计的?"
    r"起止日,gmv,日均gmv,花费,推广成交额,roi,推广销售额占比(?:及|和|,)点击成交转化率"
)
_COMPARE = re.compile(
    _TWO_WINDOWS + r"(?:分别)?与(?:之前|此前|前一)(?:的)?等长(?:区间|期间)比较日均变化"
)
_BASELINE = re.compile(r"基准(?:情景)?(?:取|采用)" + _TWO_WINDOWS + r"(?:的)?日均之间")
_ACTIVITY = re.compile(
    r"(\d{1,2})月(?:双十一|双11)(?:活动)?后(?:的做法)?[,]?(?:是)?以活动后最低连续"
    r"([1-9]\d?)[日天]日均(?:作|作为)下限,以(?:最近|近)([1-9]\d?)[天日]最高单日"
    r"(?:作|作为)乐观上限(?:,后者要求余下各天平均达到该水平)?"
)
_REPORT = re.compile(r"(?:汇报|报告)(?:结构|顺序)?按(.+?)(?:排列|汇报|输出)")
_REPORT_ROLES = {
    "截止日和累计实绩": r"(?:数据)?截止日(?:和|及|与)累计实绩",
    "目标与进度": r"(?:月度)?目标(?:与|和|及)进度",
    "月末中心及区间": r"(?:月底|月末)中心(?:及|与|和)区间",
    "较上次变化": r"(?:较|与)上次(?:的)?变化",
    "驱动、风险与待办": r"驱动,风险(?:与|和|及)待办",
}
REPORT_ORDER = tuple(_REPORT_ROLES)

# 每项执行语义都要在正式条款中完整出现；公式只接受有量纲、分子分母及日期约束的表达。
_SEMANTICS = {
    "continuous": (
        r"(?:逐[日天]核对(?:本期)?应覆盖的?店铺,)?(?:只|仅)累计(?:自)?"
        r"月初至(?:最近|最新)连续完整销售日"
    ),
    "enough_sample": (
        r"(?:这是)?(?:当前)?(?:有足够完整样本|完整样本(?:充足|足够))时(?:采用|使用)"
        r"(?:的经营估计)?(?:,权重来自现行预测方法)?"
    ),
    "units": r"(?:使用|以|按)元(?:和|及)未舍入(?:的数)?值(?:来)?计算(?:,最后换算万元)?",
    "sales_input": (
        r"以日期\+店铺(?:检查|核对)日报(?:缺行|重复|基础金额|,|和|及)+,"
        r"逐日汇总销售收入\(gmv\)|按日期和店铺核对gmv"
    ),
    "roi": r"(?:实际)?roi(?:=|为)推广成交额合计/花费合计",
    "sales_share": r"推广销售额占比(?:=|为)推广成交额合计/相同日期,相同店铺的gmv合计",
    "click_conversion": r"点击成交转化率(?:=|为)推广成交笔数合计/点击量合计",
    "zero_denominator": r"分母为(?:零|0)时(?:标为)?无法计算",
    "early_month": r"月初不足([1-9]\d?)[日天]或([1-9]\d?)[日天]时标明实际样本天数",
    "no_days": r"当月(?:尚无|没有)完整销售日时标记待更新",
    "scenario": r"各情景(?:统一)?按"
    + _ACTUAL
    + r"\+"
    + _REMAINING
    + r"\*该情景日均计算上下界(?:,同时列出所需日均)?",
    "scenario_sides": r"保守情景向近期可比低位延伸,乐观情景向近期可持续高位延伸",
    "comparable_phase": r"换月或换活动阶段应重选可比窗口",
    "uncalibrated": r"情景区间(?:表达经营条件,未做|未经)概率校准",
    "achievement": r"目标达成率(?:=|为)" + _ACTUAL + r"/(?:本月|月度)?目标",
    "gap": r"剩余差额(?:=|为)(?:本月)?目标-" + _ACTUAL,
    "needed_daily": r"尚未达标时,差额/" + _REMAINING + r"即达标所需日均",
    "close": r"已经达标就列超额金额,月末剩余天数为(?:零|0)时直接核算实绩",
    "forecast_achievement": r"预测达成率(?:=|为)(?:中心估计|月末中心估计)/(?:本月)?目标",
    "target_missing": (
        r"(?:无记录或目标值冲突就标待确认,销售预测仍可继续|"
        r"目标缺失或冲突时,销售预测可以继续)"
    ),
}
_SEMANTIC_PATTERNS = {role: re.compile(pattern) for role, pattern in _SEMANTICS.items()}
_ADVISORY = {
    "sample_exception": r"样本明显失真或需要换方法时,写清触发原因,旧方法结果和新假设",
    "cross_month": r"若另看跨月走势,单列日期和结果",
    "spend_intent": r"(\d+(?:\.\d+)?)的投产和(\d+(?:\.\d+)?)%的推广销售额占比仅用于对照经营意向",
    "evidence_only": r"(?:只有|仅在)证据能量化(?:新增)?影响时才?调整金额",
    "target_input": (
        r"(?:每次)?打开指定视图,按年份,月份,平台=拼多多(?:精确)?匹配,"
        r"读取数值字段gmv目标,单位元"
    ),
    "promotion_input": r"按日期核对(?:花费|归因成交额|点击|成交笔数|,|及|和)+",
    "promotion_overlap": (r"标出缺失日期,只在销售与推广均有数据的日期计算占比,(?:并)?列出有效天数"),
    "sales_empty": r"基础金额为空(?:而|但)公式显示0时(?:列为|标为)待补",
    "actual_priority": r"目标表gmv达成数据若滞后或为0,记录差异,累计实绩取日报",
    "spend_conditions": (
        r"(?:若|如果)乐观情景依赖加投,匹配单款利润表,核对(?:退款|可承受推广费|供货能力|,|和|及)+"
    ),
    "comparison_basis": (
        r"核对昨日隐含剩余日均与最新完整日实绩的差,再解释中心变动来自"
        r"(?:新增销售|历史补数|方法调整|,|还是|和|及)+"
    ),
    "previous": r"(?:先看|查看)本月上次快照,记下(?:截止日|目标|中心估计|未解决事项|,|和|及)+",
}

# 文案只有在整句用途清楚时才可略过，避免把“核对/记录”后夹带的口径一并放行。
_EDITORIAL = re.compile(
    r"(?:本(?:文档?|页|说明|手册|sop))?(?:供|用于)(?:每周|每月|日常|定期|内部)?"
    r"(?:经营[周月]?会|例会|周会|月会|团队|运营人员|同事)?,?(?:复盘|查阅|讨论|阅读)(?:参考|使用)?|"
    r"(?:修改|修订|维护|阅读|格式)(?:说明|记录)(?:收在|见|参见|收录于|置于|附于)"
    r"(?:文末|本页|附录|目录)|"
    r"(?:本次)?(?:修订|修改|更新|调整|统一)(?:了)?(?:标点|排版|页码|格式|错别字)"
    r"(?:(?:,|和|与|及)(?:标点|排版|页码|格式|错别字))*"
)
_METADATA = re.compile(
    r"(?:版本日期|更新日期)[:为]?(?:\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2})|"
    r"版本号[:为]?v?\d+(?:\.\d+){0,3}|(?:销售分析师|分析师|运营)[a-z\d-]{0,20}使用|"
    r"每天北京时间(?:[01]?\d|2[0-3]):[0-5]\d按当前自然月更新|"
    r"目标|实绩|推广|(?:月度目标|日报销售|推广汇总|经营|库存)(?:资料|看板|台账|表)"
)


# 已支持的经营复核用途同样按整句验证。可换措辞、标点和明细排列，不能借引用、
# 核对或留档改变算法、店铺范围、缺失日处理。正文中的操作不会触发外部动作。
def _items(*names):
    item = "(?:" + "|".join(map(re.escape, names)) + ")"
    return item + "(?:(?:,|和|与|及|或|以及)" + item + ")*"


_CHECKED_OPERATIONS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:先)?回答(?:照|按)(?:最近|近期)的经营情况,(?:月底|月末)能卖多少,"
        r"再对照当月目标,看剩余每天需要卖多少",
        r"按北京时间确定年月(?:及|和)当月天数|按自然月确定剩余天数",
        r"当日尚在销售或有店漏报时单列,剩余天数从已核实的销售截止日往后数到月末",
        r"同一批次已有结果时(?:核对|复核)" + _items("来源版本", "历史修订"),
        r"保留上次快照,在新运行目录记录本次(?:实时)?核验结果",
        r"销售(?:和|与|及)推广分别记录截止日",
        r"目标变更(?:时|要)(?:记录|留存)" + _items("旧值", "读取日期"),
        r"汇总占位或与看板不符时,先查各店明细的" + _items("渠道", "归因口径"),
        r"(?:核对|复核)" + _items("店铺", "活动"),
        r"核对销售变化来自哪些店铺或品类,对照"
        + _items("星期结构", "去年同月可比阶段", "本月确认的活动日期"),
        r"地区看板先查" + _items("版本", "截止日", "缺失店铺") + r",天气用主要成交省份的官方预报",
        r"投放结合实际" + _items("花费", "roi", "转化") + "判断",
        r"利润中已扣费用(?:只|仅)计一次",
        r"库存优先引用库存岗位已核实结果,记录" + _items("款号", "色码", "渠道", "快照日", "到货日"),
        r"分开看" + _items("共享可用", "拼多多分配", "在途"),
        r"确认有货能卖的天数,再核对" + _items("受限sku日销量", "成交单价", "替代购买"),
        r"证据不足时列风险,已体现于近期销售的影响避免重复扣减",
        r"分别记录" + _items("目标修改", "预测修改"),
        r"按预测月份和运行时间保存"
        + _items("原始输入", "筛选范围", "计算结果", "情景假设", "版本对照"),
        _items("预测有实质变化", "风险新增", "待办解除", "月度收官") + r"时通知",
        r"日常小幅波动留档即可",
        r"跨月重新取目标并从新月第一天累计",
        r"上月数据齐全后,对照最后一次有效预测和最终实绩,记录"
        + _items("金额", "百分比偏差", "原因", "收官标记"),
    )
)


def _is_explanatory(clause, strict_sections):
    """整句匹配管理说明或已识别的复核动作；未解释的正文一律暂缓执行。"""
    text = clause.text
    if _EDITORIAL.fullmatch(text):
        return True
    return clause.section not in strict_sections and (
        _METADATA.fullmatch(text) is not None or any(p.fullmatch(text) for p in _CHECKED_OPERATIONS)
    )


def _days(match, clause):
    windows = sorted(map(int, match.groups()))
    if windows[0] == windows[1] or windows[-1] > 31:
        raise _issue("比较或情景窗口超出当前自然月的计算范围。", clause.citation())
    return windows


def _report_order(match, clause):
    fields = re.split(r"\s*(?:→|->|➡)\s*", match[1])
    roles = [
        role
        for field in fields
        for role, pattern in _REPORT_ROLES.items()
        if re.fullmatch(pattern, field)
    ]
    if set(roles) != set(REPORT_ORDER) or len(fields) != len(REPORT_ORDER):
        raise _issue("汇报结构含有未接入的项目或重复栏目。", clause.citation())
    return roles


def formal_clauses(content):
    """选出同一版 Markdown 中的正式条款。模型与本地核验始终使用相同的顺序。"""
    if not isinstance(content, str) or not content.strip() or len(content) > 200_000:
        raise ForecastRuleError("预测 SOP 正文为空或超过单篇读取范围。")
    return list(_clauses(content))


@dataclass
class ForecastReading:
    rule: dict
    centers: list[int]
    weights: list[tuple[int, int, Decimal]]
    roles: dict[int, str]


def read_forecast_clauses(clauses, *, spoken=None) -> ForecastReading:
    """核验正式条款，在计算规则之外保留每次权重声明的本版序号以供逐项对照。"""
    evidence, params = {}, {}
    unclassified, strict_sections = [], set()
    centers, claims = [], []
    weights, sources, roles = {}, {}, {}

    for heading in clauses:
        if heading.unverified_heading:
            raise _issue("正式章节标题包含尚未支持的范围限定或停用条件。", heading.citation())

    def record(role, clause, value=None):
        if role in evidence and params.get(role) != value:
            raise _issue("同一修订包含互不一致的正式条款。", evidence[role], clause.citation())
        evidence.setdefault(role, clause.citation())
        roles[i] = role
        if value is not None:
            params[role] = value

    for i, clause in enumerate(clauses, 1):
        value = _units(clause.text)
        try:
            if spoken and i in spoken:
                declaration = spoken[i]
            else:
                declaration = _weight_statement(value)
        except ForecastRuleError as exc:
            raise _issue(str(exc), clause.citation()) from None
        if declaration:
            center, terms = declaration
            if center:
                record("center", clause)
                centers.append(i)
                strict_sections.add(clause.section)
            elif _NAMED_MEAN.fullmatch(value):
                strict_sections.add(clause.section)
            if terms and not center:
                roles[i] = "weight_definition"
            for term in terms:
                days, weight = term["days"], term["weight"]
                if days in weights and weights[days] != weight:
                    raise _issue(
                        f"正式条款对近 {days} 日销售日均的权重不一致。",
                        sources[days].citation(),
                        clause.citation(),
                    )
                sources.setdefault(days, clause)
                weights[days] = weight
                claims.append((i, days, Decimal(weight)))
        elif match := _DISPLAY.fullmatch(value):
            record("windows", clause, _days(match, clause))
            strict_sections.add(clause.section)
        elif match := _COMPARE.fullmatch(value):
            record("comparisons", clause, _days(match, clause))
        elif match := _BASELINE.fullmatch(value):
            record("baseline", clause, _days(match, clause))
        elif match := _ACTIVITY.fullmatch(value):
            month, low_days, high_days = map(int, match.groups())
            if month != 11 or max(low_days, high_days) > 31:
                raise _issue("尚未接入此活动阶段或可比窗口。", clause.citation())
            record(
                "activity",
                clause,
                {
                    "month": month,
                    "after_day": 11,
                    "low_days": low_days,
                    "high_days": high_days,
                },
            )
        elif match := _REPORT.fullmatch(value):
            record("report", clause, _report_order(match, clause))
        else:
            for role, pattern in _SEMANTIC_PATTERNS.items():
                if match := pattern.fullmatch(value):
                    record(role, clause, _days(match, clause) if role == "early_month" else None)
                    break
            else:
                for role, pattern in _ADVISORY.items():
                    if match := re.fullmatch(pattern, value):
                        record(role, clause, list(match.groups()) or None)
                        break
                else:
                    unclassified.append(clause)

    for clause in unclassified:
        if not _is_explanatory(clause, strict_sections):
            raise _issue(
                "正文含有未能验证的计算、适用条件或例外，当前修订暂停计算。", clause.citation()
            )
    required = {
        *_SEMANTICS,
        "center",
        "windows",
        "comparisons",
        "scenario",
        "baseline",
        "activity",
        "report",
    }
    if required - evidence.keys():
        raise _issue("正式条款缺少可验证的公式、数据前提、情景或汇报结构。")
    for role in ("comparisons", "early_month"):
        if params[role] != params["windows"]:
            raise _issue(
                "速度展示、前期比较和样本说明的窗口不一致。", evidence["windows"], evidence[role]
            )
    weight_sources = list(dict.fromkeys(sources.values()))
    with localcontext(Context(prec=32)):
        if len(weights) != 2 or sum(map(Decimal, weights.values())) != 1:
            raise _issue(
                "中心估计需要明确两个不同日窗口的权重，合计应为 100%。",
                *(cl.citation() for cl in weight_sources or [clauses[centers[0] - 1]]),
            )
    for n, source in enumerate(
        (cl for cl in weight_sources if cl.citation() != evidence["center"]), 1
    ):
        key = "weight_definition" if n == 1 else f"weight_definition_{n}"
        evidence[key] = source.citation()
    rule = {
        "type": "weighted_daily_mean",
        "terms": [{"days": days, "weight": weights[days]} for days in sorted(weights)],
        "windows": params["windows"],
        "baseline_windows": params["baseline"],
        "activity": params["activity"],
        "report_order": params["report"],
        "evidence": evidence,
    }
    return ForecastReading(rule, centers, claims, roles)


def compile_forecast_rule(content: str) -> dict:
    """以完整 Markdown 正文为输入，提取两个加权窗口、前提、场景和汇报结构及各自出处。"""
    return read_forecast_clauses(formal_clauses(content)).rule
