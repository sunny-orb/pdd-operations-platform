"""已授权JST加工必要投影；Q有限对账、A未通过门禁，未知不补零。"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.modules.aftersales.bundle_categories import (
    BUNDLE_CATEGORY_ROW_LIMIT,
    bundle_category_consensus,
    bundle_category_evidence,
    parent_key,
)
from app.modules.sales.datasets import REFUND_SHOPS
from app.modules.sales.normalization import fingerprint

TABLES = dict(
    jst_returns="dwd_trd_sys_aftersale_order_detail_f",
    jst_shipped_sales="ud_1_dsyyxskb",
    jst_cancelled_sales="ud_1_dsyyxskb",
)
COMMON = (
    "__id",
    "shop_code",
    "shop_name",
    "product_code",
    "spec_code",
    "product_name",
    "spec_name",
)
MASTER_TABLE = "std_api_jstbz_product_spec_info_f"
MASTER_COLUMNS = (
    "__id",
    "data_source",
    "product_code",
    "spec_code",
    "product_type",
    "suite_code",
    "first_category",
    "second_category",
    "dt",
    "start_date",
    "end_date",
)
STYLE_CATEGORY_COLUMNS = tuple(
    col for col in MASTER_COLUMNS if col not in ("data_source", "start_date", "end_date")
)
STYLE_CATEGORY_ROW_LIMIT = 5000
STYLE_CATEGORY_SCOPE = "all_current_ordinary_skus_for_style"
COLUMNS = {
    "jst_returns": COMMON
    + (
        "confirm_date",
        "aftersale_num",
        "apply_aftersale_num",
        "actual_receive_amount",
        "platform_subsidy_amount",
        "reason_type",
        "aftersale_status",
        "aftersale_type",
        "product_aftersale_type",
    ),
    "jst_shipped_sales": COMMON + ("pay_time", "sfje", "outstock_num"),
    "jst_cancelled_sales": COMMON + ("pay_time", "sale_amount", "sale_num"),
}
NUMBERS = {
    "__id",
    "aftersale_num",
    "apply_aftersale_num",
    "actual_receive_amount",
    "platform_subsidy_amount",
    "sfje",
    "outstock_num",
    "sale_amount",
    "sale_num",
}
NOTE = (
    "实退数量经有限历史样本对账，仅作观察参考；实际入库金额因补贴及倍额反例未通过门禁。"
    "product_code为JST款式编码，spec_code为SKU；按当前JST普通SKU资料核对款/SKU并取得一二分类，"
    "缺失SKU仅按当前同款全部普通SKU逐字段非空唯一共识补类，身份仍未匹配；"
    "原款/SKU精确对应当前组合父键时，仅按完整组合资料原生分类逐字段共识补类，不借普通子件推父类；"
    "非历史分类，不混入旧手工或虚拟分类。无共识保留空分类；重复或跨款SKU资料拒绝。"
    "原售后分类中的拒收仅作来源标记，签收阶段未核；当前明细不是原商品/原因汇总行。"
)
REVISION = "m5-jst-current-bundle-consensus-v4"


def number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (float, bool)):
        raise ValueError("SOURCE_DECIMAL_REQUIRED")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("SOURCE_NUMBER_INVALID")
    return result


def source_code(value):
    """保留来源编码及前导零；缺失不借款号、规格名补SKU。"""
    if value is None or value == "":
        return None
    if isinstance(value, (float, bool)):
        raise ValueError("SOURCE_STRING_ID_REQUIRED")
    return str(value)


def style_consensus_eligible(item):
    """仅新共识路径排除空白款/SKU；不清洗、改写来源编码或改变身份状态。"""
    keys = (source_code(item.get(field)) for field in ("product_code", "spec_code"))
    return item.get("jst_current_mapping_status") == "not_found" and all(
        key is not None and key.strip() for key in keys
    )


def enrich_current_master(raw, master, snapshot, style_candidates=None, bundle_candidates=None):
    """事实与当前普通SKU资料1:1核验；返回含原资料证据的raw，不展开组合或改写度量。"""
    snapshot = str(snapshot)
    requested = {source_code(row.get("spec_code")) for row in raw} - {None}
    by_sku = {}
    for item in master:
        sku = source_code(item.get("spec_code"))
        if (
            sku not in requested
            or item.get("product_type") != "普通商品"
            or source_code(item.get("suite_code")) is not None
        ):
            continue
        if str(item.get("dt")) != snapshot:
            raise ValueError("SOURCE_CURRENT_MASTER_SNAPSHOT_MISMATCH")
        style = source_code(item.get("product_code"))
        if style is None:
            raise ValueError("SOURCE_CURRENT_MASTER_KEY_MISSING")
        if sku in by_sku:
            if source_code(by_sku[sku].get("product_code")) != style:
                raise ValueError("SOURCE_CURRENT_MASTER_SKU_CONFLICT")
            raise ValueError("SOURCE_CURRENT_MASTER_DUPLICATE_KEY")
        by_sku[sku] = {column: item.get(column) for column in MASTER_COLUMNS}
    enriched = []
    for item in raw:
        style, sku = source_code(item.get("product_code")), source_code(item.get("spec_code"))
        record = by_sku.get(sku)
        if style is None or sku is None:
            status = "source_key_missing"
        elif record is None:
            status = "not_found"
        elif source_code(record["product_code"]) != style:
            status = "identity_mismatch"
        else:
            status = "matched"
        enriched.append(
            dict(
                item,
                jst_current_master_source="cubeappdata." + MASTER_TABLE,
                jst_current_snapshot_date=snapshot,
                jst_current_mapping_status=status,
                jst_current_master_record=record,
            )
        )
    eligible = {
        source_code(item.get("product_code")) for item in enriched if style_consensus_eligible(item)
    }
    style_candidates = style_candidates or {}
    if set(style_candidates) - eligible:
        raise ValueError("SOURCE_STYLE_CATEGORY_SCOPE_MISMATCH")
    if sum(len(items) for items in style_candidates.values()) > STYLE_CATEGORY_ROW_LIMIT:
        raise ValueError("SOURCE_STYLE_CATEGORY_ROW_LIMIT")
    evidence, seen = {}, {}
    for style, candidates in style_candidates.items():
        records = [
            {
                col: str(item.get(col)) if col == "dt" else item.get(col)
                for col in STYLE_CATEGORY_COLUMNS
            }
            for item in candidates
        ]
        proof = dict(
            scope=STYLE_CATEGORY_SCOPE,
            source_style_code=style,
            snapshot_date=snapshot,
            candidates=records,
        )
        proof["fingerprint"] = fingerprint(proof)
        style_category_consensus(proof, style, snapshot)
        for item in records:
            sku = source_code(item["spec_code"])
            known = seen.get(sku) or by_sku.get(sku)
            if known and source_code(known["product_code"]) != style:
                raise ValueError("SOURCE_CURRENT_MASTER_SKU_CONFLICT")
            if sku in by_sku and any(
                str(by_sku[sku].get(col)) != str(item.get(col)) for col in STYLE_CATEGORY_COLUMNS
            ):
                raise ValueError("SOURCE_CURRENT_MASTER_INCONSISTENT")
            if sku in requested and sku not in by_sku:
                raise ValueError("SOURCE_CURRENT_MASTER_INCONSISTENT")
            seen[sku] = item
        evidence[style] = proof
    for item in enriched:
        proof = (
            evidence.get(source_code(item.get("product_code")))
            if style_consensus_eligible(item)
            else None
        )
        status = item["jst_current_mapping_status"]
        item["jst_current_style_category_evidence"] = proof
        item["category_match_method"] = "exact_sku" if status == "matched" else "unmatched"
        if (
            status == "not_found"
            and proof
            and any(
                value is not None
                for value in style_category_consensus(proof, item["product_code"], snapshot)
            )
        ):
            item["category_match_method"] = "style_consensus"
    bundle_candidates = bundle_candidates or {}
    eligible_parents = {
        parent_key(item.get("product_code"), item.get("spec_code"))
        for item in enriched
        if style_consensus_eligible(item)
    } - {None}
    if set(bundle_candidates) - eligible_parents:
        raise ValueError("SOURCE_BUNDLE_CATEGORY_SCOPE_MISMATCH")
    if sum(len(items) for items in bundle_candidates.values()) > BUNDLE_CATEGORY_ROW_LIMIT:
        raise ValueError("SOURCE_BUNDLE_CATEGORY_ROW_LIMIT")
    bundle_proofs = {
        key: bundle_category_evidence(key, candidates, snapshot)
        for key, candidates in bundle_candidates.items()
    }
    for item in enriched:
        key = parent_key(item.get("product_code"), item.get("spec_code"))
        proof = bundle_proofs.get(key) if style_consensus_eligible(item) else None
        item["jst_current_bundle_category_evidence"] = proof
        if proof is not None and proof["candidate_count"] > 0:
            item["category_match_method"] = (
                "current_bundle_category_consensus"
                if any(value is not None for value in current_categories(item))
                else "unmatched"
            )
    return enriched


def style_category_consensus(evidence, style, snapshot):
    """完整同款普通SKU证据→独立两级共识；只补当前类别，不确认缺失SKU身份。"""
    if (
        source_code(style) is None
        or not source_code(style).strip()
        or evidence.get("scope") != STYLE_CATEGORY_SCOPE
        or evidence.get("source_style_code") != source_code(style)
        or evidence.get("snapshot_date") != snapshot
    ):
        raise ValueError("SOURCE_STYLE_CATEGORY_SCOPE_MISMATCH")
    if fingerprint(
        {key: value for key, value in evidence.items() if key != "fingerprint"}
    ) != evidence.get("fingerprint"):
        raise ValueError("SOURCE_STYLE_CATEGORY_FINGERPRINT_MISMATCH")
    candidates = evidence.get("candidates") or []
    if len(candidates) > STYLE_CATEGORY_ROW_LIMIT:
        raise ValueError("SOURCE_STYLE_CATEGORY_ROW_LIMIT")
    seen = set()
    for item in candidates:
        if (
            source_code(item.get("product_code")) != source_code(style)
            or str(item.get("dt")) != snapshot
            or item.get("product_type") != "普通商品"
            or source_code(item.get("suite_code")) is not None
        ):
            raise ValueError("SOURCE_STYLE_CATEGORY_IDENTITY_MISMATCH")
        sku = source_code(item.get("spec_code"))
        if sku is None or not sku.strip():
            raise ValueError("SOURCE_STYLE_CATEGORY_KEY_MISSING")
        if sku in seen:
            raise ValueError("SOURCE_CURRENT_MASTER_DUPLICATE_KEY")
        seen.add(sku)
    values = []
    for field in ("first_category", "second_category"):
        entries = [item.get(field) for item in candidates]
        if entries and all(isinstance(value, str) and value.strip() for value in entries):
            unique = set(entries)
            values.append(unique.pop() if len(unique) == 1 else None)
        else:
            values.append(None)
    return tuple(values)


def current_categories(item):
    """精确普通SKU优先；未匹配身份取精确组合原生类，无组合记录才回退普通款共识。"""
    if item.get("jst_current_mapping_status") != "matched":
        proof = item.get("jst_current_style_category_evidence")
        categories = (None, None)
        if proof is not None:
            if not style_consensus_eligible(item):
                raise ValueError("SOURCE_STYLE_CATEGORY_SCOPE_MISMATCH")
            categories = style_category_consensus(
                proof, item["product_code"], item.get("jst_current_snapshot_date")
            )
        bundle = item.get("jst_current_bundle_category_evidence")
        if bundle is not None:
            if not style_consensus_eligible(item):
                raise ValueError("SOURCE_BUNDLE_CATEGORY_SCOPE_MISMATCH")
            bundle_categories = bundle_category_consensus(
                bundle,
                parent_key(item.get("product_code"), item.get("spec_code")),
                item.get("jst_current_snapshot_date"),
            )
            # 父SKU与款级推断粒度不同；存在父记录时NULL也保留，不拼接款级字段。
            if bundle["candidate_count"] > 0:
                return bundle_categories
        return categories
    if item.get("jst_current_bundle_category_evidence") is not None:
        raise ValueError("SOURCE_BUNDLE_CATEGORY_SCOPE_MISMATCH")
    master = item.get("jst_current_master_record") or {}
    if (
        source_code(master.get("product_code")) != source_code(item.get("product_code"))
        or source_code(master.get("spec_code")) != source_code(item.get("spec_code"))
        or master.get("product_type") != "普通商品"
        or source_code(master.get("suite_code")) is not None
        or str(master.get("dt")) != item.get("jst_current_snapshot_date")
    ):
        raise ValueError("SOURCE_CURRENT_MASTER_IDENTITY_MISMATCH")
    return master.get("first_category") or None, master.get("second_category") or None


def normalize(code, raw, end):
    """必要原行→既有事实表字段；原时间取日标签，绝不复制报告期间。"""
    rows, seen = [], set()
    for item in raw:
        key = str(item["__id"])
        if not key or key in seen:
            raise ValueError("SOURCE_DUPLICATE_KEY")
        seen.add(key)
        if item["shop_name"] not in REFUND_SHOPS:
            raise ValueError("SOURCE_SHOP_OUTSIDE_SCOPE")
        day = datetime.fromisoformat(
            str(item["confirm_date" if code == "jst_returns" else "pay_time"])
        ).date()
        if not end - timedelta(days=59) <= day <= end:
            raise ValueError("SOURCE_DATE_OUTSIDE_SCOPE")
        category, subcategory = current_categories(item)
        row = dict(
            source_row_key=key,
            source_record_id=key,
            row_kind="detail",
            source_row_kind=None,
            shop_id=REFUND_SHOPS[item["shop_name"]],
            shop_match_status="matched",
            source_shop_id=str(item["shop_code"]) if item.get("shop_code") is not None else None,
            source_shop_name=item["shop_name"],
            source_style_code=source_code(item.get("product_code")),
            source_product_code=source_code(item.get("spec_code")),
            source_product_name=item.get("product_name") or None,
            source_color_specification=item.get("spec_name") or None,
            source_product_category=category,
            source_subcategory=subcategory,
            source_virtual_category=None,
            source_period_label=None,
            period_start=day,
            period_end=day,
            date_status="parsed_label",
        )
        if code == "jst_returns":
            row.update(
                source_return_reason=item.get("reason_type") or None,
                source_analysis_fields=None,
                source_aftersale_type=item.get("aftersale_type") or None,
                source_aftersale_status=item.get("aftersale_status") or None,
                actual_returned_qty=number(item.get("aftersale_num")),
                actual_inbound_amount=None,
            )
        else:
            row.update(
                sales_amount=number(
                    item.get("sfje" if code == "jst_shipped_sales" else "sale_amount")
                ),
                source_sales_qty=number(
                    item.get("outstock_num" if code == "jst_shipped_sales" else "sale_num")
                ),
            )
        rows.append(row)
    if not rows:
        raise ValueError("SOURCE_EMPTY_REQUIRES_REVIEW")
    return rows
