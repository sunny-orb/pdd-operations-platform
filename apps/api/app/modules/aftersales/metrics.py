"""售后原值聚合与完整组键；任何缺额不被SUM忽略，未知不补零。"""

from decimal import Decimal, localcontext

from .repository import IDENTITY_FIELDS


def total(rows, field):
    if not rows or any(r.get(field) is None for r in rows):
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return sum((r[field] for r in rows), Decimal(0))


def add(a, b):
    with localcontext() as ctx:
        ctx.prec = 50
        return a + b if a is not None and b is not None else None


def ratio(a, b):
    with localcontext() as ctx:
        ctx.prec = 50
        return a / b if a is not None and b is not None and b > 0 else None


def summarize(returns, shipped, cancelled):
    q = total(returns, "actual_returned_qty")
    a = total(returns, "actual_inbound_amount")
    s = total(shipped, "sales_amount")
    p = total(cancelled, "sales_amount")
    return from_values(q, a, s, p, str(len(returns)) if returns is not None else None)


def from_values(q, a, s, p, records):
    g, t = add(s, p), add(p, a)
    return dict(
        returned_qty=q,
        inbound_amount=a,
        shipped_amount=s,
        presale_amount=p,
        gmv=g,
        total_amount=t,
        presale_rate=ratio(p, g),
        aftersale_rate=ratio(a, s),
        total_rate=ratio(t, g),
        records=records,
        size_qty=None,
        quality_qty=None,
    )


def identity(row):
    return tuple(row.get(k) for k in IDENTITY_FIELDS)


def category(row):
    return row.get("source_product_category") or "待核对品类"


def matches(row, q):
    if q.category and category(row) != q.category:
        return False
    if q.keyword:
        text = " ".join(str(row.get(k) or "") for k in (*IDENTITY_FIELDS, "source_return_reason"))
        if q.keyword.casefold() not in text.casefold():
            return False
    # 未发布分类词典时不能把原原因猜成已证尺码/质量。
    return q.focus == "all"


def complete_key(row):
    return all(
        row.get(k)
        for k in (
            "source_product_category",
            "source_style_code",
            "source_product_code",
            "source_product_name",
        )
    )


def groups(roles, level="style"):
    """按完整原生组键配对，不以款号降级；保留三角色组并集及缺行。"""
    by_role = []
    for rows in roles:
        found = {}
        for r in rows or []:
            key = category(r) if level == "category" else identity(r)
            found.setdefault(key, []).append(r)
        by_role.append(found)
    keys = set().union(*(set(g) for g in by_role))
    result = []
    for key in sorted(keys, key=str):
        block = [
            g.get(key) if rows is not None else None for g, rows in zip(by_role, roles, strict=True)
        ]
        exemplar = next(r[0] for r in block if r)
        # 分类缺失的销售组不能被静默抛除后宣称某分类分母完整。
        if level == "category":
            for i in (1, 2):
                if any(not r.get("source_product_category") for r in roles[i] or []):
                    block[i] = None
        elif not complete_key(exemplar):
            block[1] = block[2] = None
        result.append(dict(key=key, example=exemplar, roles=block, metrics=summarize(*block)))
    return result
