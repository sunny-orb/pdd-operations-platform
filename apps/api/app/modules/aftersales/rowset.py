"""请求内等价组的原行计数；普通单行输入默认权重1，不改变业务数量。"""


def record_count(rows):
    """规范行/SQL等价组→原始明细条数；None与空集合均为0，缺源由调用方表达。"""
    return sum(row.get("_record_count", 1) for row in rows or [])


def exact_compaction(rows, fields):
    """用SQL绝对和证明50位内所有原行/子集加总精确；无界NUMERIC不作位数假设。"""
    for field in fields:
        absolute = [
            row["_absolute_" + field] for row in rows if row["_absolute_" + field] is not None
        ]
        if not absolute:
            continue
        # 绝对和保留最大scale；组间再加最多一组数位的进位余量，包含抵消前的幅度。
        integer = max(max(value.adjusted() + 1, 0) for value in absolute)
        fractional = max(max(-value.as_tuple().exponent, 0) for value in absolute)
        if integer + fractional + len(str(len(absolute))) > 50:
            return False
    return True
