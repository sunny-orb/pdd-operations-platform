"""2026-11-16改名的封闭归档兼容；只接受已审查的旧→新解释器，不执行归档代码。"""

import hashlib
from dataclasses import asdict, replace
from pathlib import Path

from .contracts import SalesError
from .datasets import EXTENDED_REVISION, READ_SPECS, REFUND_SHOPS
from .normalization import canonical, fingerprint
from .normalization_compat import normalizer_revision
from .source_policy import METRICS, POLICY_REVISION, SHOPS

# 同时锁定旧、新实现，未来公式变化必须重新审查，不能借本次改名绕过解释校验。
INTERPRETERS = {
    "normalization.py": (
        "6c5e2529492ad2e23eb24d7fa5a07396b631d9d0748b87480a342c1a0b0f7fd0",
        "9ee1f39d70b324fd4040d6a63a706ba977c155094464e8359baf850fcf82f08b",
    ),
    "source_normalization.py": (
        "5e4a2e2f8b0ea7b05afb4981840ff6b76c3d2670723a5b3a99f5c9ce485bcf7e",
        "e483497657e70c7003145539b8f6d7e3cc50bac04a93cdbc53784be6eb94c36e",
    ),
    "source_policy.py": (
        "0af72f340ec4764c70ece3c1235e070122a36283dfb0825120fc22714677280f",
        "6e346c32e3a9c4abf4fe3b051cd71b1f19480196d06b3eba0e1f752960dc02f6",
    ),
    "datasets.py": (
        "77d9d5c674908cee9748b1a96348bd2a573c3a755aabc06263c91dbd994e159c",
        "db2260febebffa85a2567b5aa2e6c451bab41490d9dbeee6e17f52317167ab37",
    ),
    "promotion_history.py": (
        "c00c904a6d2c28b2e47d28c2de63b0e353a535403128ca4fa3411704f57051f6",
        "83311da7f68f0a56c488dfd1b9477dde4ed85af690e0a9d9509e112d70f6a7ab",
    ),
}
# 10/30固定历史补齐的原配置；差异仅动态窗口/地区预算，不影响固定11条解释。
# 必须成对命中且仅供history，不能逐文件混搭或用于普通来源。
ORIGINAL_HISTORY_CONFIG = {
    "source_policy.py": "8626ede1c2bc3f2fa4a2eb7455c78d97579f62ed8f31f899a7ddc2ff4d237b36",
    "datasets.py": "298910b16a3538105c01917c5b3588437382b9e754dbafc46e0bc67a78bf3af6",
}
LEGACY_SHOPS = {**SHOPS, "271470254": ("spitztx", "kocotree童装童鞋旗舰店", "童装童鞋店")}
LEGACY_REFUND_SHOPS = {
    name: shop
    for name, shop in REFUND_SHOPS.items()
    if name != "kk树-kocotree青少年服饰旗舰店-配饰"
}


def validate_legacy_interpretation(manifest, sources):
    """旧生产者与当前代码均须命中精确摘要，政策/表/店铺范围必须等于已核版本。"""
    code = (manifest.get("dataset") or {}).get("code")
    original_history = code == "shop_promotion_history" and all(
        hashlib.sha256(sources.get("modules/sales/" + name, "").encode()).hexdigest() == digest
        for name, digest in ORIGINAL_HISTORY_CONFIG.items()
    )
    for name, (old, current) in INTERPRETERS.items():
        if name == "promotion_history.py" and code != "shop_promotion_history":
            continue
        archived = sources.get("modules/sales/" + name, "").encode()
        if original_history:
            old = ORIGINAL_HISTORY_CONFIG.get(name, old)
        current_path = Path(__file__).with_name(name)
        actual_current = (
            normalizer_revision(current_path)
            if name == "normalization.py"
            else hashlib.sha256(current_path.read_bytes()).hexdigest()
        )
        if hashlib.sha256(archived).hexdigest() != old or actual_current != current:
            raise SalesError("ARCHIVE_INTERPRETER_MISMATCH")
    expected = asdict(READ_SPECS[code]) if code in READ_SPECS else None
    if (
        canonical(manifest.get("dataset")) != canonical(expected)
        or (code is not None and code not in READ_SPECS)
        or manifest.get("policy") != (EXTENDED_REVISION if code else POLICY_REVISION)
        or canonical(manifest.get("shops")) != canonical(LEGACY_SHOPS)
    ):
        raise SalesError("ARCHIVE_INTERPRETATION_MISMATCH")


def restore_legacy_fingerprint(candidate, code):
    """当前规则已验证金额/键；另验旧别名范围，重建旧指纹并保留旧事实字段。"""
    if code == "shop_promotion_history":
        rows = tuple(
            {**row, "source_shop_name": LEGACY_SHOPS[row["source_shop_id"]][1]}
            for row in candidate.rows
        )
        return replace(candidate, rows=rows)
    for row in candidate.raw_rows:
        if code == "shop_refunds":
            valid = row.get("店铺") in LEGACY_REFUND_SHOPS
        else:
            valid = row.get("shop_name") == LEGACY_SHOPS[str(row["shop_id"])][1]
        if not valid:
            raise SalesError("ARCHIVE_INTERPRETATION_MISMATCH")
    content = dict(
        policy=EXTENDED_REVISION if code else POLICY_REVISION,
        mapping=LEGACY_SHOPS,
        normalizer_revision=INTERPRETERS["source_normalization.py" if code else "normalization.py"][
            0
        ],
        start=str(candidate.window.start),
        end=str(candidate.window.end),
        rows=candidate.raw_rows,
    )
    if code:
        content.update(source=asdict(READ_SPECS[code]), refund_shops=LEGACY_REFUND_SHOPS)
    else:
        content["metrics"] = METRICS
    return replace(candidate, fingerprint=fingerprint(content))
