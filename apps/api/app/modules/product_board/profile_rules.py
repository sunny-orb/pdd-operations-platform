"""当前商品资料解析；保留源格，仅接受有明确边界的ID和名称共识。"""

import re
from collections.abc import Iterable

# 来自当前资料实际备注格式；不是任意中文后缀或任意数字提取器。
ID_NOTES = frozenset(
    (
        "下架",
        "已下架",
        "替换",
        "新链接",
        "旧链接",
        "拼链接",
        "拼老链接",
        "替换链接",
        "成人",
        "儿童",
        "背心链接",
        "复购",
        "限量购",
        "铺货",
    )
)
_NOTES = "|".join(sorted(map(re.escape, ID_NOTES), key=len, reverse=True))
_ID = re.compile(rf"([0-9]+)(?:\s*[-—－]?\s*(?:{_NOTES}))?")
_VARIANT = re.compile(r"\s*(?:【[^【】]*】|\[[^\[\]]*\]|（[^（）]*）|\([^()]*\))$")


def parse_listing_ids(value) -> tuple[str, ...]:
    """逐行解析纯ID/明确状态后缀；未知行不污染有效行，不从URL/小数抽数字。"""
    raw = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    # 来源备注有时单独换行；只连接紧跟在ID后短横线之后的备注。
    raw = re.sub(r"([0-9]+)[-—－]\s*\n+\s*(?=[\u4e00-\u9fff])", r"\1-", raw)
    result = []
    for line in raw.split("\n"):
        if "://" in line:
            continue
        accepted = []
        for token in re.split(r"[/;；]", line):
            token = token.strip()
            if not token or token.lstrip("-—－").strip() in ID_NOTES:
                continue
            match = _ID.fullmatch(token)
            if match is None:
                break  # 同一行有未知结构，不猜分隔语义；其他行仍可解析。
            accepted.append(match[1])
        else:
            result.extend(accepted)
    return tuple(dict.fromkeys(result))


def resolve_product_name(names: Iterable[str | None]) -> str | None:
    """唯一名称优先；多名称仅删除完整末尾款式括号后取共同基础名，冲突返回空。"""
    choices = {name.strip() for name in names if name and name.strip()}
    if len(choices) == 1:
        return next(iter(choices))
    bases = set()
    pairs = {"】": "【", "]": "[", "）": "（", ")": "("}
    for name in choices:
        stack = []
        for char in name:
            if char in pairs.values():
                stack.append(char)
            elif char in pairs and (not stack or stack.pop() != pairs[char]):
                return None
        if stack:
            return None
        base = name
        while match := _VARIANT.search(base):
            base = base[: match.start()].strip()
        bases.add(base)
    if len(bases) == 1:
        base = next(iter(bases))
        return base if len(base) >= 2 else None
    return None
