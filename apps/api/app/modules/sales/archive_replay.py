"""只读重放必要原投影，不连接来源或写库，不执行归档中的Python代码。"""

import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.modules.archive_payload import open_payload

from . import normalization
from .contracts import PromotionReadWindow, ReadWindow, SalesError
from .normalization import fingerprint, normalize_shop_daily
from .normalization_compat import compatible_normalizer
from .source_policy import PROMOTION_CODES, SHOPS


def restore_candidate(folder: Path, *, heartbeat=lambda: None, expected_producer=None):
    """校验输入、生产者及解释指纹后重建候选；解释代码不相容时拒绝静默重算。"""
    folder = Path(folder)
    manifest = json.loads((folder / "interpretation.json").read_text())
    sources = json.loads((folder / "producer-source.json").read_text())
    if "sha256:" + fingerprint(sources) != manifest["producer_revision"]:
        raise SalesError("ARCHIVE_PRODUCER_MISMATCH")
    if expected_producer is not None and manifest["producer_revision"] != expected_producer:
        raise SalesError("ARCHIVE_PRODUCER_MISMATCH")
    archived_normalizer = sources["modules/sales/normalization.py"]
    current_normalizer = Path(normalization.__file__).read_bytes()
    legacy = archived_normalizer.encode() != current_normalizer and not compatible_normalizer(
        archived_normalizer, current_normalizer
    )
    if legacy:
        from .rename_archive import validate_legacy_interpretation

        validate_legacy_interpretation(manifest, sources)

    def original_type(value):
        return Decimal(value["decimal"]) if set(value) == {"decimal"} else value

    # 先流式校验，再边读边解释，不同时保留原始大列表和规范化副本。
    digest = hashlib.sha256()
    with open_payload(folder / "input.ndjson") as stream:
        while chunk := stream.read(1024 * 1024):
            heartbeat()
            digest.update(chunk)
    if digest.hexdigest() != manifest["input_sha256"]:
        raise SalesError("ARCHIVE_CHECKSUM_MISMATCH")

    def original_rows():
        verified, count = hashlib.sha256(), 0
        with open_payload(folder / "input.ndjson") as stream:
            for index, line in enumerate(stream):
                if index % 1000 == 0:
                    heartbeat()
                verified.update(line)
                count += 1
                yield json.loads(line, object_hook=original_type)
        if verified.hexdigest() != manifest["input_sha256"]:
            raise SalesError("ARCHIVE_CHECKSUM_MISMATCH")
        if not count:
            raise SalesError("SOURCE_NO_RECORDS")

    rows = original_rows()
    code = (manifest.get("dataset") or {}).get("code")
    window_type = PromotionReadWindow if code in PROMOTION_CODES else ReadWindow
    window = window_type(
        date.fromisoformat(manifest["start"]), date.fromisoformat(manifest["end"]), tuple(SHOPS)
    )
    if manifest.get("dataset"):
        from . import source_normalization
        from .source_normalization import normalize_source

        if not legacy and (
            sources["modules/sales/source_normalization.py"]
            != Path(source_normalization.__file__).read_text()
        ):
            raise SalesError("ARCHIVE_INTERPRETER_MISMATCH")
        if manifest["dataset"]["code"] == "shop_promotion_history":
            from . import promotion_history
            from .promotion_history import normalize_history

            if not legacy and (
                sources.get("modules/sales/promotion_history.py")
                != Path(promotion_history.__file__).read_text()
            ):
                raise SalesError("ARCHIVE_INTERPRETER_MISMATCH")
            candidate = normalize_history(list(rows))
        elif code == "shop_refunds" and "gmv_projection" in (manifest.get("source_metadata") or {}):
            from . import f01_gmv

            if (
                manifest["source_metadata"]["gmv_projection"] != f01_gmv.F01_GMV_REVISION
                or sources.get("modules/sales/f01_gmv.py") != Path(f01_gmv.__file__).read_text()
            ):
                raise SalesError("ARCHIVE_INTERPRETER_MISMATCH")
            candidate = f01_gmv.normalize_f01_gmv(rows, window, heartbeat)
        else:
            candidate = normalize_source(manifest["dataset"]["code"], rows, window, heartbeat)
    else:
        candidate = normalize_shop_daily(rows, window)
    candidate = replace(candidate, source_metadata=manifest.get("source_metadata"))
    if legacy:
        from .rename_archive import restore_legacy_fingerprint

        candidate = restore_legacy_fingerprint(candidate, code)
    if candidate.fingerprint != manifest["fingerprint"]:
        raise SalesError("ARCHIVE_INTERPRETATION_MISMATCH")
    return candidate
