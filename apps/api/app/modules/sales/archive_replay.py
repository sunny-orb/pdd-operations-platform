"""只读重放必要原投影，不连接来源或写库，不执行归档中的Python代码。"""

import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import normalization
from .contracts import ReadWindow, SalesError
from .normalization import fingerprint, normalize_shop_daily
from .source_policy import SHOPS


def restore_candidate(folder: Path):
    """校验输入、生产者及解释指纹后重建候选；解释代码不相容时拒绝静默重算。"""
    folder = Path(folder)
    manifest = json.loads((folder / "interpretation.json").read_text())
    data = (folder / "input.ndjson").read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["input_sha256"]:
        raise SalesError("ARCHIVE_CHECKSUM_MISMATCH")
    sources = json.loads((folder / "producer-source.json").read_text())
    if "sha256:" + fingerprint(sources) != manifest["producer_revision"]:
        raise SalesError("ARCHIVE_PRODUCER_MISMATCH")
    if sources["modules/sales/normalization.py"] != Path(normalization.__file__).read_text():
        raise SalesError("ARCHIVE_INTERPRETER_MISMATCH")

    def original_type(value):
        return Decimal(value["decimal"]) if set(value) == {"decimal"} else value

    rows = [json.loads(line, object_hook=original_type) for line in data.splitlines()]
    window = ReadWindow(
        date.fromisoformat(manifest["start"]), date.fromisoformat(manifest["end"]), tuple(SHOPS)
    )
    if manifest.get("dataset"):
        from . import source_normalization
        from .source_normalization import normalize_source

        if (
            sources["modules/sales/source_normalization.py"]
            != Path(source_normalization.__file__).read_text()
        ):
            raise SalesError("ARCHIVE_INTERPRETER_MISMATCH")
        candidate = normalize_source(manifest["dataset"]["code"], rows, window)
        candidate = replace(candidate, source_metadata=manifest.get("source_metadata"))
    else:
        candidate = normalize_shop_daily(rows, window)
    if candidate.fingerprint != manifest["fingerprint"]:
        raise SalesError("ARCHIVE_INTERPRETATION_MISMATCH")
    return candidate
