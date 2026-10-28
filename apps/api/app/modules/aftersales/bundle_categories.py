"""当前组合父键原生分类证据；只归并原字段，不推断子件、身份或历史归属。"""

from app.modules.sales.normalization import fingerprint

BUNDLE_CATEGORY_COLUMNS = (
    "__id",
    "data_source",
    "product_code",
    "spec_code",
    "suite_code",
    "suite_spec_code",
    "product_type",
    "first_category",
    "second_category",
    "dt",
)
BUNDLE_CATEGORY_ROW_LIMIT = 5000
BUNDLE_CATEGORY_SCOPE = "all_current_bundle_records_for_exact_parent"


def parent_key(style, sku):
    """仅接受非空文本原父键；保留前导零和非空编码，不清洗或借用名称。"""
    if all(isinstance(value, str) and value.strip() for value in (style, sku)):
        return style, sku
    return None


def bundle_category_consensus(evidence, key, snapshot):
    """完整同dt父组合原生字段→独立两级共识；不读取普通组件分类。"""
    if (
        not isinstance(evidence, dict)
        or not isinstance(key, tuple)
        or len(key) != 2
        or parent_key(*key) is None
        or evidence.get("scope") != BUNDLE_CATEGORY_SCOPE
        or evidence.get("source_style_code") != key[0]
        or evidence.get("source_product_code") != key[1]
        or evidence.get("snapshot_date") != snapshot
        or evidence.get("complete") is not True
    ):
        raise ValueError("SOURCE_BUNDLE_CATEGORY_SCOPE_MISMATCH")
    if fingerprint(
        {name: value for name, value in evidence.items() if name != "fingerprint"}
    ) != evidence.get("fingerprint"):
        raise ValueError("SOURCE_BUNDLE_CATEGORY_FINGERPRINT_MISMATCH")
    candidates = evidence.get("candidates")
    if (
        not isinstance(candidates, list)
        or type(evidence.get("candidate_count")) is not int
        or evidence["candidate_count"] != len(candidates)
    ):
        raise ValueError("SOURCE_BUNDLE_CATEGORY_COUNT_MISMATCH")
    if len(candidates) > BUNDLE_CATEGORY_ROW_LIMIT:
        raise ValueError("SOURCE_BUNDLE_CATEGORY_ROW_LIMIT")
    seen_rows, seen_skus = set(), set()
    for item in candidates:
        if (
            parent_key(item.get("suite_code"), item.get("suite_spec_code")) != key
            or str(item.get("dt")) != snapshot
            or item.get("product_type") != "组合商品"
        ):
            raise ValueError("SOURCE_BUNDLE_CATEGORY_IDENTITY_MISMATCH")
        component = parent_key(item.get("product_code"), item.get("spec_code"))
        row_id = item.get("__id")
        if component is None or not isinstance(row_id, str) or not row_id.strip():
            raise ValueError("SOURCE_BUNDLE_CATEGORY_KEY_MISSING")
        if row_id in seen_rows or component[1] in seen_skus:
            raise ValueError("SOURCE_BUNDLE_CATEGORY_DUPLICATE_COMPONENT")
        seen_rows.add(row_id)
        seen_skus.add(component[1])
    result = []
    for field in ("first_category", "second_category"):
        values = [item.get(field) for item in candidates]
        unique = (
            set(values)
            if all(isinstance(value, str) and value.strip() for value in values)
            else set()
        )
        result.append(unique.pop() if len(unique) == 1 else None)
    return tuple(result)


def bundle_category_evidence(key, candidates, snapshot):
    """保留完整父范围候选和数量声明；构建时立即复验，可供离线归档重演。"""
    proof = dict(
        scope=BUNDLE_CATEGORY_SCOPE,
        source_style_code=key[0],
        source_product_code=key[1],
        snapshot_date=snapshot,
        complete=True,
        candidate_count=len(candidates),
        candidates=[
            {
                col: str(item.get(col)) if col == "dt" else item.get(col)
                for col in BUNDLE_CATEGORY_COLUMNS
            }
            for item in candidates
        ],
    )
    proof["fingerprint"] = fingerprint(proof)
    bundle_category_consensus(proof, key, snapshot)
    return proof
