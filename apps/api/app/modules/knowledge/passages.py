"""单篇公共 SOP 按经营主题定位章节；返回有出处的原文，不将算例当作经营数据。"""

import re

_TOPICS = (
    (r"权重|公式|怎么算|计算|方法|推演|区间|情景|怎么预测", r"推演|中心估计"),
    (r"截止|完整|缺店|漏报|缺日|范围", r"截止日"),
    (r"来源|从哪|口径|目标表|日报|单位", r"取数|口径"),
    (r"推广|花费|转化|ROI|效率", r"销售速度|投放效率"),
    (r"风险|库存|天气|行动|建议|意向", r"经营证据"),
    (r"留档|通知|快照|报告|顺序", r"复核|留档"),
    (r"算例|示例|例子", r"算例|示例"),
)
_FORECAST_TOPIC = re.compile(r"预测|月末|月底|GMV|本月能卖", re.I)
_BUSINESS_TERMS = re.compile(
    r"投放效率|投放|推广|花费|点击|转化|ROI|库存|缺货|断货|天气|降温|销售速度|活动|意向", re.I
)
_SYNONYMS = {"缺货": "库存", "断货": "库存", "降温": "天气"}
_OTHER_DOCUMENT = re.compile(
    r"(?:售后|退[款货]|商品评分|新品|天气|库存|推广|投放)\s*(?:预测|操作|审批|分析|诊断)?\s*(?:的\s*)?SOP",
    re.I,
)


def outside_source(query: str) -> bool:
    return bool(_OTHER_DOCUMENT.search(query))


def operational_terms(query: str) -> set[str]:
    """抽取当前来源能提供依据的业务术语。仅有泛词时由问答沿用已核实的业务数据。"""
    terms = {_SYNONYMS.get(t, t).lower() for t in _BUSINESS_TERMS.findall(query)}
    if "投放效率" in terms:
        terms.update(("投放", "roi"))
    return terms


def select_passages(content: str, query: str) -> list[tuple[str, str]]:
    """最多两个相关章节。标题或主题不匹配时明确无结果。"""
    if outside_source(query):
        return []
    forecast = _FORECAST_TOPIC.search(query) or re.fullmatch(
        r"(?:那|这个|它的|具体)?(?:权重|公式|依据|怎么算的|口径)(?:是多少|是什么|呢|多少)?[？?。]?",
        query.strip(),
    )
    terms = operational_terms(query)
    if not forecast and not terms:
        return []
    headings = list(re.finditer(r"(?m)^(#{1,6})[ \t]+([^\n]+)\n", content))
    explicit_example = bool(re.search(r"算例|示例|例子", query))
    sections = []
    example_depth = None
    for i, match in enumerate(headings):
        depth, title = len(match[1]), match[2].strip()
        if example_depth is not None and depth <= example_depth:
            example_depth = None
        if re.search(r"算例|示例|样例|举例|案例", title):
            example_depth = depth
        if example_depth is None or explicit_example:
            end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
            body = content[match.end() : end].strip()
            if body:
                clean = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", body)
                clean = re.sub(r"https?://[^\s)>]+", "", clean)
                sections.append((title, clean))
    advice = bool(terms and re.search(r"核查|排查|建议|先看|怎么查|如何判断", query))
    if not forecast or (advice and not explicit_example):
        # 业务建议仅检索匹配到实际术语的正式段落。整篇文章的适用范围由报告一起返回。
        ranked = []
        for title, body in sections:
            for paragraph in re.split(r"\n\s*\n|\n(?= *[-*] )", body):
                title_hits = sum(term in title.lower() for term in terms)
                body_hits = sum(term in paragraph.lower() for term in terms)
                if title_hits or body_hits:
                    ranked.append((2 * body_hits + title_hits, title, paragraph))
        ranked.sort(key=lambda row: -row[0])
        return [(title, paragraph[:900]) for _, title, paragraph in ranked[:2]]
    topics = [heading for pattern, heading in _TOPICS if re.search(pattern, query, re.I)]
    if not topics:
        topics = [r"推演|中心估计"]
    result = []
    for heading in topics:
        for title, body in sections:
            if not re.search(heading, title) or any(title == t for t, _ in result):
                continue
            result.append((title.strip(), body))
            if len(result) == 2:
                return result
    return result
