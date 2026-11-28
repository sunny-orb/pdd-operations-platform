"""扩展同一 F01 日报的原 GMV 投影；旧七字段归档继续使用原解释器。"""

import hashlib
from dataclasses import replace
from pathlib import Path

from .normalization import fingerprint
from .source_normalization import normalize_source, numeric

GMV_FIELD = "fldax7fYxe"
ADJUSTMENT_FIELD = "fldQmlTprE"
GMV_LABEL = "销售收入（GMV）"
ADJUSTMENT_LABEL = "做单金额"
F01_GMV_REVISION = "f01-reported-sales-gmv-v1"


def normalize_f01_gmv(rows, window, heartbeat=lambda: None):
    """沿用已核日期、店铺、原行键；新增数值保持来源精度和空值，不参与退款口径。"""
    original = list(rows)
    base = normalize_source("shop_refunds", original, window, heartbeat)
    by_record = {row["record_id"]: row for row in original}
    for fact, retained in zip(base.rows, base.raw_rows, strict=True):
        heartbeat()
        row = by_record[fact["source_record_id"]]
        for label in (GMV_LABEL, ADJUSTMENT_LABEL):
            retained[label] = row.get(label)
        fact["source_reported_gmv"] = numeric(retained[GMV_LABEL], negative=True, formula=True)
        fact["source_adjustment_amount"] = numeric(retained[ADJUSTMENT_LABEL], negative=True)
    digest = fingerprint(
        {
            "base": base.fingerprint,
            "revision": F01_GMV_REVISION,
            "interpreter": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "raw": base.raw_rows,
        }
    )
    return replace(base, fingerprint=digest)
