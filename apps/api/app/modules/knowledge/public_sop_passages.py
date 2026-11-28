"""在已选择的单篇文档中定位可回查的短引文。适用范围和业务动作均来自原文。"""

import re

_HEADING = re.compile(r"(?m)^ {0,3}(#{1,6})[ \t]+([^\n]+)")
_EXAMPLE = re.compile(r"算例|示例|样例|举例|历史案例|\bexamples?\b", re.I)
_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-z][a-z0-9_]+")
# 去掉问句和文档共有的虚词。各业务的动作、名词和文章标题不在这里登记。
_QUESTION_WORDS = re.compile(
    r"应该|需要|请问|告诉我|这篇|这份|文档|文章|原文|里的|怎么做|怎么办|怎么|怎样|如何|"
    r"是什么|有哪些|什么|哪些|是否|能否|具体|一下|介绍|概括|概述|主要|全文|分别|"
    r"操作|步骤|流程|做法|方法|处理|计算|时间|情况|根据|按照|有关|以及|的|呢|吗|\bsop\b"
)
_QUOTE_CHARS = 600
_PASSAGE_LIMIT = 4
_CONTEXT_CHARS = 2400
_CONTEXT_PASSAGES = 12
_SECTION_CHARS = 180


def _terms(text):
    terms = set()
    for word in _WORDS.findall(_QUESTION_WORDS.sub(" ", text.casefold())):
        if word.isascii():
            terms.add(word)
            continue
        for width in (2, 3):
            terms.update(word[i : i + width] for i in range(len(word) - width + 1))
    return terms


def _score(terms, text):
    text = text.casefold()
    ascii_words = set(re.findall(r"[a-z][a-z0-9_]+", text))
    hits = {term for term in terms if (term in ascii_words if term.isascii() else term in text)}
    # 一条长问题中只重合两个汉字，很容易是不同主题里的普通用词。
    if not hits or (len(hits) == 1 and hits != terms and len(next(iter(hits))) <= 2):
        return 0
    return sum(len(term) for term in hits)


def _sections(content):
    parents = []
    start = 0
    for heading in _HEADING.finditer(content):
        yield [label for _, label in parents], content[start : heading.start()]
        depth = len(heading[1])
        while parents and parents[-1][0] >= depth:
            parents.pop()
        parents.append((depth, heading[2].strip()))
        start = heading.end()
    yield [label for _, label in parents], content[start:]


def _quotes(body):
    for paragraph in re.split(r"\n[ \t]*\n", body):
        start = 0
        while start < len(paragraph):
            end = min(start + _QUOTE_CHARS, len(paragraph))
            if end < len(paragraph):
                boundary = max(
                    paragraph.rfind(c, start + _QUOTE_CHARS // 2, end) for c in "。！？\n"
                )
                if boundary >= 0:
                    end = boundary + 1
            quote = paragraph[start:end].strip()
            if quote:
                yield quote
            start = end


def relevant_passages(content: str, query: str, title: str, description: str) -> tuple[str, list]:
    """返回原文定位或有界上下文。仅标题、适用说明的相关性不能证明某一条款。"""
    detail = re.sub(r"《[^》]+》", " ", query.casefold()).replace(title.casefold(), " ")
    terms = _terms(detail)
    examples = bool(_EXAMPLE.search(detail))
    context, ranked = [], []
    context_size = 0
    for headings, section_text in _sections(content):
        path = [label for label in headings if label != title]
        if not examples and any(_EXAMPLE.search(label) for label in path):
            continue
        section = (" / ".join(path) or (headings[-1] if headings else "正文"))[-_SECTION_CHARS:]
        heading_score = _score(terms, " ".join(path))
        for quote in _quotes(section_text):
            passage = {"section": section, "quote": quote}
            remaining = _CONTEXT_CHARS - context_size - len(section)
            if len(context) < _CONTEXT_PASSAGES and remaining > 0:
                preview = quote[:remaining]
                context.append({"section": section, "quote": preview})
                context_size += len(section) + len(preview)
            score = heading_score + 2 * _score(terms, quote)
            if score:
                ranked.append((score, passage))
    if ranked:
        ranked.sort(key=lambda row: -row[0])
        return "matched", [
            passage for score, passage in ranked[:_PASSAGE_LIMIT] if 2 * score >= ranked[0][0]
        ]
    # 泛问可通读开头；短文章的目录说明支持问法时，给模型原文判断实际是否有依据。
    if context and (
        not terms or (len(content) <= _CONTEXT_CHARS and _score(terms, f"{title} {description}"))
    ):
        return "context", context
    return "no_match", []
