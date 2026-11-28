"""将公共 SOP 的短目录和有出处的原句交给助手。普通业务查询不会接触飞书。"""

import json
import re

from .business import report

_DOCUMENT = re.compile(r"SOP|知识库|手册|文档|原文章节|规程|业务规范", re.I)
_GUIDANCE = re.compile(
    r"操作流程|原文|条款|指引|"
    r"核查|排查|建议|怎么办|怎么[做查处理改善提升]|如何|先[做查看]|审批|补救|优先|追目标",
    re.I,
)
_FACT = re.compile(
    r"多少|几[单笔件人]|[金销]额|销量|GMV|环比|同比|排行|趋势|达成率|完成率|转化率|投产比",
    re.I,
)
_ANALYTICAL = re.compile(
    r"为什么|为啥|原因|判断|解释|预测|预计|预估|推算|估算|月底能|能卖|权重|公式|阈值|按什么|能否|该不该"
)
_GENERAL_METHOD = re.compile(
    r"只[讲说谈聊给](?:通用|一般|排查)?(?:思路|方法|原理)|(?:不用|不必|无需)查.{0,7}数据"
)


def wants_guidance(question):
    return requests_document(question) or bool(_GUIDANCE.search(question))


def requests_document(question):
    return bool(_DOCUMENT.search(question))


def asks_general_method(question):
    return bool(_GENERAL_METHOD.search(question)) and not requests_document(question)


def only_observed_facts(question):
    return bool(
        _FACT.search(question) and not (wants_guidance(question) or _ANALYTICAL.search(question))
    )


def entries():
    from app.modules.knowledge import public_sops

    return public_sops.catalog_entries()


def catalog_text():
    documents = entries()
    if not documents:
        return "目前还没有接入公共 SOP。"
    return "已接入的公共 SOP\n\n" + "\n".join(
        f"- **{doc['title']}**　{doc['description']}" for doc in documents
    )


def directory_prompt(question):
    if asks_general_method(question):
        return "\n方法部分给通用思路。实绩查经营报告，涉及专门口径才另查该口径。"
    if not wants_guidance(question):
        return ""
    return "\n可选的公共 SOP（目录只用于选择文档）：" + json.dumps(
        entries(), ensure_ascii=False, separators=(",", ":")
    )


def requested_links(question, reports, answer):
    if not re.search(r"原文|出处|来源|链接", question):
        return ""
    links = {}
    for item in reports:
        if item.get("capability") == "business_knowledge":
            url = item["scope"].get("document_url")
            if url and url not in answer:
                links[url] = item["source"]
    if not links:
        return ""
    return "\n\n查阅原文　" + " · ".join(f"[{title}]({url})" for url, title in links.items())


def read_document(params, *, session=None):
    """目录 key 由服务端解析，模型只得到本次读取的摘句、章节和来源。"""
    from app.modules.knowledge.public_sops import read_public_sop

    from .forecast_answers import revision_question

    doc = read_public_sop(params["document_key"], params["keyword"], session=session)
    state = doc["state"]
    revision = str(doc["revision"]) if doc.get("revision") is not None else None
    key = doc["key"]
    passages = doc.get("passages") or []
    cited = state in {"matched", "context"} and bool(passages)
    if (
        revision_question(params["keyword"])
        and revision
        and state in {"matched", "context", "no_match"}
    ):
        state, cited = "revision", True
        answer = f"《{doc['title']}》当前的飞书修订号是 {revision}。[查看原文]({doc['source_url']})"
        passages = []
    elif cited:
        answer = "\n\n".join(
            [
                *(f"「{p['section']}」{p['quote']}" for p in passages),
                f"[查看 SOP 原文]({doc['source_url']})",
            ]
        )
    else:
        answer = {
            "not_registered": "公共目录里尚未接入这篇 SOP。",
            "no_match": "这篇 SOP 中没有找到与本题对应的原文条款。",
        }.get(state, "本次未能核对飞书原文，暂时无法引用这篇 SOP。")
    version_key = "forecast_sop" if key == "monthly_gmv" else f"sop_{key}"
    data = report(
        "definitions",
        "business_knowledge",
        doc.get("title") or "公共 SOP",
        doc.get("title") or "公共 SOP",
        revision,
        versions={version_key: revision},
        scope={"document_key": key, "document_url": doc.get("source_url")},
        notes=["原文章节 · " + "、".join(dict.fromkeys(p["section"] for p in passages))]
        if passages
        else [],
    )
    return dict(
        data,
        answer=answer,
        knowledge_state=state,
        forecast_status="method" if cited else "unavailable",
    )
