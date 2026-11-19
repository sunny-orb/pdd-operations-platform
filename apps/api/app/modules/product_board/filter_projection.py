"""商品与商品推广共用的等级筛选投影；不覆盖资料原值或计算资格。"""

from .profile_rules import matches_keyword as matches_profile_keyword


def matches_keyword(item: dict, keyword: str) -> bool:
    return matches_profile_keyword(item, keyword) or (
        keyword.casefold() in (item.get("platform_title") or "").casefold()
    )


def level_group(info: dict) -> str:
    """输入已解析的链接资料，输出互斥筛选组；不同等级成员已在资料层去重。"""
    members = info.get("candidates", {}).get("level", [])
    return "多等级" if len(members) > 1 else info.get("level") or "未匹配"


def matches_level(info: dict, wanted: str) -> bool:
    """分组按组匹配；旧混合名称仍精确匹配原组合，空条件保留全部链接。"""
    actual = level_group(info) if wanted in ("多等级", "未匹配") else info.get("level")
    return not wanted or actual == wanted
