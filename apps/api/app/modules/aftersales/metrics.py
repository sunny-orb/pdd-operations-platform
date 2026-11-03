"""售后原值聚合与完整组键；任何缺额不被SUM忽略，未知不补零。"""

from decimal import Decimal, localcontext

from .classification import observations
from .repository import IDENTITY_FIELDS
from .rowset import record_count


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


def summarize(returns, shipped, cancelled, parameters=None):
    result = numeric_summary(returns, shipped, cancelled)
    observed = observations(returns, parameters)
    result.update(size_qty=observed["size_qty"], quality_qty=observed["quality_qty"])
    return result


def numeric_summary(returns, shipped, cancelled):
    """三个独立角色→数量/金额/比率；按需另算词典，分组规模排序不重复分类。"""
    q = total(returns, "actual_returned_qty")
    a = total(returns, "actual_inbound_amount")
    s = total(shipped, "sales_amount")
    p = total(cancelled, "sales_amount")
    return from_values(q, a, s, p, str(record_count(returns)) if returns is not None else None)


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
    return True


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


def groups(roles, level="style", parameters=None):
    """按完整原生组键配对，不以款号降级；保留三角色组并集及缺行。"""
    by_role = []
    for rows in roles:
        found = {}
        for r in rows or []:
            key = category(r) if level == "category" else identity(r)
            found.setdefault(key, []).append(r)
        by_role.append(found)
    keys = set().union(*(set(g) for g in by_role))
    missing_sales_category = (
        {i for i in (1, 2) if any(not r.get("source_product_category") for r in roles[i] or [])}
        if level == "category"
        else set()
    )
    result = []
    for key in sorted(keys, key=str):
        block = [
            g.get(key) if rows is not None else None for g, rows in zip(by_role, roles, strict=True)
        ]
        exemplar = next(r[0] for r in block if r)
        # 分类缺失的销售组不能被静默抛除后宣称某分类分母完整。
        if level == "category":
            for i in missing_sales_category:
                block[i] = None
        elif not complete_key(exemplar):
            block[1] = block[2] = None
        result.append(
            dict(
                key=key,
                example=exemplar,
                roles=block,
                metrics=numeric_summary(*block),
            )
        )
    return result
