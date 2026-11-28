"""区分问答任务和取数范围。页面筛选只服务于查数，不决定能否交谈。"""

import json
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import UsageLimits
from pydantic_ai.usage import RunUsage

from . import catalog, knowledge_answers
from .answer_text import clean_answer_text, stream_options
from .forecast_answers import forecast_intent

ReplyMode = Literal["identity", "open", "evidence", "auto"]


class GeneralAnswer(BaseModel):
    """模型可用这一种结束方式交付无需看板事实的回答。"""

    answer: str = Field(min_length=1, max_length=6000)


class AssistantHelp(BaseModel):
    """模型选择用户想了解的能力，由服务按实际配置给出答复，无需读经营数据。"""

    topic: (
        Literal[
            "overview", "usage", "writing", "model", "scope", "actions", "inventory", "unavailable"
        ]
        | catalog.Capability
    )

    def answer(self, *, model_id: str | None, public_sops_enabled: bool) -> str:
        if self.topic == "model":
            name = model_id.split(":")[-1] if model_id else None
            return (
                f"我是这个看板的 AI 助手，用的是 DeepSeek，当前型号是 `{name}`。"
                if name
                else "我是这个看板的 AI 助手，当前没有可显示的模型名称。"
            )
        if self.topic == "scope":
            return (
                "五个页面都可以问同样的问题。打开「跟随页面」会沿用当前筛选。"
                "关闭后，直接在问题里说要看哪家店、哪款商品或哪段时间。"
            )
        if self.topic == "writing":
            return (
                "可以。把原话或商品卖点发来，我可以帮你写主图文案、整理晨会要点，或改成更好懂的话。"
            )
        if self.topic == "usage":
            return (
                "可以让我比店铺销售、找退货多的款，或整理晨会要点。提问时带上对象和时间，"
                "例如「近七天哪些款退货多，我先查哪款？」接着可以问「为什么先看它？」"
            )
        if self.topic == "actions":
            return (
                "我可以核对销售和推广数据，说明规则，给你调整建议。"
                "改价、调整预算和处理订单需要你在业务后台操作。"
            )
        if self.topic == "inventory":
            return catalog.DATA_BOUNDARIES["inventory"]
        if self.topic == "unavailable":
            return "这项能力目前还没接入看板。你可以贴出手头的资料，我可以据此帮你分析。"
        if self.topic == "overview":
            # 例子取自启用的查询能力，普通对话与写作无需经营权限。
            examples = {
                "sales_shops": "比各店销售",
                "products_risks": "找优先关注的商品",
                "promotion_diagnosis": "排查推广异常",
                "aftersales_analysis": "看哪些款退货多",
            }
            cases = "、".join(
                label
                for key, label in examples.items()
                if key in catalog.CAPABILITIES and catalog.enabled(key, public_sops_enabled)
            )
            return f"你可以让我{cases}。我也能解释指标和规则，帮你写文案、整理晨会要点。"
        if not catalog.enabled(self.topic, public_sops_enabled):
            return "当前环境尚未接入飞书公共 SOP。"
        description = catalog.CAPABILITIES[self.topic][1].split("；")[0].replace("/", "、")
        text = f"可以。看板提供{description}。"
        if self.topic in {"aftersales_overview", "aftersales_analysis"}:
            text += catalog.DATA_BOUNDARIES["listing_aftersales"]
        return text


@dataclass(frozen=True)
class TurnPlan:
    mode: ReplyMode
    forecast: str | None = None

    @property
    def needs_agent(self):
        return self.mode != "identity" and self.forecast not in {
            "forecast",
            "knowledge",
            "catalogue",
        }


_IDENTITY = re.compile(
    r"(?:(?:我(?:是)?问(?:的)?|请问|我想知道)[，,]?)?"
    r"(?:你(?:们)?|这个助手|当前助手|后台)(?:(?:现在|当前|底层|背后|到底|接入|调用|使用|运行|用|的|是|基于|跑|了))*"
    r"(?:(?:哪[个家款种]|什么|啥)(?:版本的)?(?:AI|语言|大语言|大|基础)?模型|"
    r"(?:模型(?:名(?:称|字)?|型号)?)(?:是)?(?:什么|啥|哪[个款])|谁|"
    r"(?:DeepSeek|千问|豆包|ChatGPT|Claude)(?:模型)?)"
    r"(?:在回答|在答|吗|呀|啊|呢|来着)*[？?！!。,.，]*",
    re.I,
)
_DRAFT = re.compile(
    r"文案|话术|拟稿|拟[一两几份个句]|写(?:[一两三几\d]+[篇份段句封首个条]|个)|"
    r"润色|改写|翻译|起(?:个|名)|取(?:个|名)|头脑风暴|创意|模板|"
    r"(?:想|写|拟|起|取|改|来|给).{0,16}(?:标题|标语|广告语)"
)
_TEACH = re.compile(
    r"通用(?:做法|思路|方法|建议)|一般(?:来说|怎么|如何)|(?:举|用).{0,8}例子?|打个比方|"
    r"讲个故事|打[个声]招呼|假设有|用.{0,5}比喻|全局问答|跟随页面|页面跟随"
)
_REWRITE = re.compile(
    r"改写|润色|翻译|缩[短成]|压缩|(?:概括|总结|浓缩|整理|换).{0,6}"
    r"(?:一句|表格|说法|要点|口吻)|说人话|说短点"
)
_REFERENCE = re.compile(r"上[文面]|刚才|刚刚|这段|上一[轮条问]|前面|上个回答")
_AD_COPY = re.compile(r"文案|标题|标语|广告语|主图|宣传语|卖点|商品详情页")
_CLAIM = re.compile(
    r"\d+(?:\.\d+)?(?:%|小时|分钟|个月|[万亿]元?|元|毫升|ml|公斤|克|年质保|天包退|h\b)|"
    r"\d{3}(?:不锈钢|内胆|材质)|(?:食品级|医用级)(?:不锈钢|内胆|材质|硅胶)?|(?:永不|绝不|保证|百分百)\w{0,4}|"
    r"防漏|不漏(?:水)?|防烫|不烫手|防摔|防晒|防紫外线|UPF\d+\+?|终身保修|无条件退款",
    re.I,
)
_ACTUAL = re.compile(
    r"实际|真实|本期|本月|上月|上周|本周|当月|昨天|昨日|今日|这[家款个]店|这款|本店|我[们家]*店|"
    r"当前筛选|所选|选中|同店|各店|全店|六店|旗舰店|(?:最近|过去|近)[一二三四五六七八九十\d]+[天日周月]|"
    r"\d{1,2}月\d{1,2}[日号]|(?:\d{4}-)?\d{1,2}-\d{1,2}"
)
_METRIC = re.compile(
    r"GMV|成交|销售|销量|退[货款]|库存|缺货|推广|投产|转化|流量|访客|花费|达成|目标|"
    r"跌|涨|下滑|异常|归因|表现|订单|风险|阈值|排名|经营|卖",
    re.I,
)
_LOOKUP = re.compile(
    r"查[一下看出证]|核实|核对.{0,4}(?:数字|金额|数量)|排名|排第|哪[家款].{0,10}最|"
    r"是否命中|阈值(?:是|为)?多少"
)
_MORE_DATA = re.compile(
    r"顺便|另外|再[查问看核]|还要|补[上充]|附上|核实|核对|更新|最新|同时|以及|结合.{0,5}看板"
)
_FOLLOWUP = re.compile(
    r"(?:那|所以|具体|到底|我问你|那它|那这款|它的|这款)?"
    r"(?:是多少|多少|为什么|为啥|呢|怎么样|如何)?[？?。！!]*"
)
_HYPOTHETICAL = re.compile(r"举例|例如|比如|假设|假如|如果|示例")
# 来自用户的样本量或金额，不将年份、日期、商品 ID 当成计算输入。
_INPUT_QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?=位|个|人|名|笔|件|单|次|元|万|%|％)")


def asks_channel_revenue(question: str) -> bool:
    """识别明确的全店销售合计，页面名称只用于说明提问位置。"""
    text = re.sub(r"(?:从|在)(?:推广(?:分析)?(?:这边|页面|页|模块))", "", question)
    return bool(
        re.search(r"六(?:家|个)?店|(?<!\d)6(?:家|个)?店|全部店铺|所有店铺|全平台", text)
        and re.search(r"合计|累计|一共|总计|总额|汇总", text)
        and re.search(
            r"GMV|实际销售额|销售金额|销售额|成交[金总]?额|卖[了出].{0,5}多少", text, re.I
        )
        and not re.search(
            r"推广|归因|投放|流量|转化|退款|售后|库存|访客|订单|天气|"
            r"目标|预测|预估|趋势|同比|环比|对比|比较|异常|排名|SOP|规则",
            text,
            re.I,
        )
    )


def _reply_mode(question: str, previous: str = "") -> ReplyMode:
    """明确取实绩或公司规则时留在查询流程；开放请求直接写，其他问题由同一 Agent 选择。"""
    text = re.sub(r"\s", "", question)
    if _IDENTITY.fullmatch(text):
        return "identity"
    if _FOLLOWUP.fullmatch(text) and previous:
        return "evidence" if _reply_mode(previous) == "evidence" else "auto"
    draft = bool(_DRAFT.search(text))
    rewrite = bool(_REWRITE.search(text))
    if (
        _HYPOTHETICAL.search(text)
        and len(_INPUT_QUANTITY.findall(text)) >= 2
        and not (
            _LOOKUP.search(text)
            or _MORE_DATA.search(text)
            or knowledge_answers.requests_document(text)
        )
    ):
        return "auto"
    if knowledge_answers.asks_general_method(text) and not (
        _LOOKUP.search(text)
        or _MORE_DATA.search(text)
        or re.search(r"多少|实际(?:是|为)|真实(?:金额|销量|成交)", text)
    ):
        return "open"
    # 已有答复的纯改写沿用材料；混合请求仍走取数流程。
    if (
        previous
        and rewrite
        and _REFERENCE.search(text)
        and not (_MORE_DATA.search(text) or re.search(r"实际|真实|多少", text))
    ):
        return "open"
    if (
        _LOOKUP.search(text)
        or (_ACTUAL.search(text) and _METRIC.search(text))
        or (
            _METRIC.search(text)
            and re.search(r"多少|最高|最低|哪.{0,5}最好", text)
            and not re.search(r"假设|假如|举例", text)
        )
        or re.search(
            r"(?:这|本|当|所选)期(?:如何|怎么样)|多少[元单件]|导致.{0,8}(?:销量|退货|下滑)", text
        )
        or re.search(r"(?:结合|根据|按|用|看)看板.{0,9}(?:数据|实际|指标)", text)
    ):
        return "evidence"
    if knowledge_answers.requests_document(text):
        # 让助手起草一份新模板和查阅公司已有制度是两种请求。
        new_document = draft and re.search(r"起草|草拟|帮我写|写[一份个]|拟一份", text)
        if not new_document or re.search(r"现有|现行|规定|按.{0,3}公司", text):
            return "evidence"
    if draft or _TEACH.search(text) or (rewrite and (previous or _REFERENCE.search(text))):
        return "open"
    return "auto"


_POLICY_ASSERTION = re.compile(
    r"(?:按|根据|按照).{0,7}(?:公司|内部|看板).{0,8}(?:规定|制度|SOP)|(?:我们公司|公司|内部|SOP|看板规则).{0,8}(?:要求|规定|明确|必须|统一)",
    re.I,
)
_ASSERTED_WRITE = re.compile(
    r"(?:我(?:还)?(?:已(?:经)?|可以|能|会)|我(?:帮|替)你|已(?:经)?(?:帮|替)你).{0,10}(?:加|提高|调高|调整|修改|更改|改|取消|发起|提交|发送|审批|上架|下架).{0,8}(?:预算|出价|价格|订单|退款|消息|商品)"
)


def reply_without_report(output, run, *, document_requested: bool) -> str | None:
    """无业务报告时统一处理文本和结构化输出。返回 None 表示仍须取得依据。"""
    if run.reports or run.tool_messages:
        return None
    if isinstance(output, AssistantHelp):
        return output.answer(model_id=run.model_id, public_sops_enabled=run.public_sops_enabled)
    if run.reply_mode != "auto" or document_requested:
        return None
    body = clean_answer_text(output.answer if isinstance(output, GeneralAnswer) else output).strip()
    if not body:
        return None
    supplied = _claims_in(run.question)
    for sentence in re.split(r"[。！？；\n]", body):
        if _ASSERTED_WRITE.search(sentence):
            return AssistantHelp(topic="actions").answer(
                model_id=run.model_id, public_sops_enabled=run.public_sops_enabled
            )
        if _POLICY_ASSERTION.search(sentence) or re.search(
            r"(?:根据|按照|查看|查到|核对了).{0,8}(?:本店|看板|本期|这款).{0,5}数据", sentence
        ):
            return None
        if _HYPOTHETICAL.search(sentence):
            continue
        if (
            _ACTUAL.search(sentence)
            and _METRIC.search(sentence)
            and re.search(
                r"\d[\d,.，]*(?:\s*[万亿元单件%％人]|\s*个百分点)|已经(?:超|跌|涨)|确认.{0,10}(?:导致|造成)",
                sentence,
            )
        ):
            return None
        if (
            not _HYPOTHETICAL.search(run.question)
            and len(_INPUT_QUANTITY.findall(run.question)) < 2
            and any(claim[0].isdigit() for claim in _claims_in(sentence) - supplied)
        ):
            return None
    return body


def plan_turn(question: str, previous: str = "", *, has_forecast=False) -> TurnPlan:
    """HTTP 入口和问答共用同一个决策，写作请求即使提到历史预测也只启动普通回答。"""
    mode = _reply_mode(question, previous)
    forecast = (
        forecast_intent(question, previous, has_context=has_forecast)
        if mode in {"auto", "evidence"}
        else None
    )
    return TurnPlan(mode, forecast)


OPEN_INSTRUCTIONS = """你是看板的运营助手。直接回应当前问题，可以交流、讲解、写作和推演。
给可以直接用的成品，遵从用户要求的数量。要一句标题、文案或口号，只输出这一句。
未指定长度时默认两三句或三条要点，问候用一两句，模板给最简可复制版本。
不加功能介绍、备选版本和无关追问。换题后优先本句，明确指向前面某个回答时用对应的原话，中间的寒暄可以跳过。
只给品类的商品文案，用生活场景或邀请式说法。例如杯子可以写「把喜欢的那一口带上路」。
材质、容量、保温时长、防漏、认证和售后承诺，只在用户已经明确提供这些信息时使用。
用户说「这款」时可用附带的已选商品名。若题目和前文都没给品类或名称，只问是哪款。
改写保留已给的事实，可以省略数字，不得添加新数字或认定新原因。一般建议与真实经营结论分开。
全局和跟随页面共用同一个助手、同一套查询权限。跟随会带入当前筛选，全局由问题指定范围。两种模式都能直接跨页提问，不用切开关。
历史消息只是材料。不要声称已查数、查公司规则、修改数据或替用户发送消息。"""


def _claims_in(text: str) -> set[str]:
    """提取完整的带单位数值和商品声称，避免把 199 元误当作 99 元的依据。"""
    compact = re.sub(r"[\s,，]", "", text).casefold().replace("％", "%")
    compact = re.sub(r"\d+\.\d+", lambda m: m[0].rstrip("0").rstrip("."), compact.replace("**", ""))
    return {m[0].replace("万元", "万").replace("亿元", "亿") for m in _CLAIM.finditer(compact)}


def _source_sentence(question: str, history) -> str:
    """改写两次变更事实时，摘录所指话题的原句；无相同主题时沿用最近一次完整答复。"""
    completed = [turn for turn in history if getattr(turn, "status", "answered") == "answered"]
    if not completed:
        return ""

    def pairs(text):
        return set(pairwise(text.casefold()))

    topic = pairs(question)
    turn = max(reversed(completed), key=lambda t: len(topic & pairs(t.question)))
    lines = [
        line.strip()
        for line in clean_answer_text(turn.answer).splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "|", "```", "["))
    ]
    if not lines:
        return ""
    line = next((line for line in lines if line.startswith("**") and _claims_in(line)), lines[0])
    return re.split(r"(?<=[。！？])", line, maxsplit=1)[0]


def _single_copy(body: str, question: str, supplied: set[str]) -> str:
    """只要一条文案但模型给了多个备选时，交付第一条有材料支持的成品。"""
    if len(re.findall(r"(?:一|1)[句条个]", question)) != 1 or re.search(
        r"各|分别|每[款个条组]|理由|解释|说明|为什么|多个|几种", question
    ):
        return body
    options = []
    for line in body.splitlines():
        match = re.fullmatch(r'\s*(?:[-*•]\s+|\d+[.、)）]\s*)(.+)|\s*[「“"](.+)[」”"]\s*', line)
        if match:
            option = (match[1] or match[2]).strip()
            options.append(option)
    if len(options) < 2:
        return body
    return next((option for option in options if _claims_in(option) <= supplied), options[0])


async def answer_open(agent, deps, messages, history):
    """共用原 Agent 和实际模型。文案的可核验声称与原材料对照后再交付。"""
    question = deps.question
    ad = bool(
        _AD_COPY.search(question)
        or (
            history
            and _AD_COPY.search(history[-1].question)
            and re.search(r"再|改|换|上一|刚才|短点|简短", question)
        )
    )
    rewrite = bool(history and _REWRITE.search(question))
    supplied = _claims_in(question)
    product = None
    if ad and re.search(r"这款|那款|这件|所选|选中|当前商品|该商品", question):
        if deps.scope.listing_id:
            await deps.resolve_pinned()
            product = deps.subject
        elif deps.scope.listing_ids or deps.scope.risk_reference:
            product = await deps.resolve_reference()
    if history and (rewrite or _REFERENCE.search(question) or re.match(r"再|换|改", question)):
        sources = history if _REFERENCE.search(question) else history[-1:]
        for turn in sources:
            supplied.update(_claims_in(turn.question))
            if rewrite and getattr(turn, "status", "answered") == "answered" and not ad:
                supplied.update(_claims_in(turn.answer))
    briefing = [f"服务模型为 {deps.model_id}。"] if deps.model_id else []
    if product and product.get("name"):
        supplied.update(_claims_in(product["name"]))
        briefing.append("看板已定位的商品名：" + json.dumps(product["name"], ensure_ascii=False))
    prompt = "\n".join([*briefing, f"当前问题：{question}"])
    usage = RunUsage()
    status = "answered"
    for attempt in range(2):
        result = await agent.run(
            prompt,
            deps=deps,
            message_history=messages,
            usage=usage,
            usage_limits=UsageLimits(request_limit=2, tool_calls_limit=0),
            **stream_options(deps, lambda: True, buffered=ad or rewrite),
        )
        body = clean_answer_text(result.output).strip()
        if ad and not rewrite:
            body = _single_copy(body, question, supplied)
        unsupported = sorted(_claims_in(body) - supplied) if ad or rewrite else []
        if not unsupported:
            break
        if attempt:
            if rewrite and not ad and (source := _source_sentence(question, history)):
                body = "可以直接用原回答这句：" + source
            else:
                body = (
                    "请给我一项可以确认的商品卖点，我可以据此写文案。"
                    if ad
                    else "请贴出要保留的原句或数字，我再帮你精简。"
                )
                status = "no_evidence"
            break
        prompt += (
            "\n上一稿中的以下说法与原文不符。删去后重写。保留的数字直接从原文照抄，不能确定的可以省略："
            + "、".join(unsupported)
        )
    deps.usage = {
        key: getattr(usage, key)
        for key in ("requests", "tool_calls", "input_tokens", "output_tokens")
    }
    return deps.response(body, status)
