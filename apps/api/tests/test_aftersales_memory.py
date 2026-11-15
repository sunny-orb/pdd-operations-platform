"""有界内存回归：输入图可以保留，指纹和双读不再复制整份数据。"""

import gc
import json
import tracemalloc
import weakref
from datetime import date
from hashlib import sha256

from app.connectors.aftersales import AftersalesReader
from app.modules.aftersales.ingestion import enrich_current_master, normalize
from app.modules.aftersales.publisher import fingerprint
from tests.test_aftersales_ingestion import current_master, identity_fact


def synthetic_rows(count):
    raw = enrich_current_master(
        [identity_fact(__id=str(i)) for i in range(count)],
        [current_master()],
        "2026-10-27",
    )
    return raw, normalize("jst_shipped_sales", raw, date(2026, 10, 27))


def test_source_fingerprint_matches_historical_bytes_without_whole_payload_copy():
    raw, rows = synthetic_rows(8000)
    payload = dict(
        revision="test", producer="unchanged", end=date(2026, 10, 27), raw=raw, rows=rows
    )
    historical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    ).encode()
    expected = sha256(historical).hexdigest()
    encoded_size = len(historical)
    del historical
    gc.collect()
    tracemalloc.start()
    try:
        actual = fingerprint(payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected
    assert peak < 2 * 1024**2, f"fingerprint peak={peak}, encoded_size={encoded_size}"


def test_stable_read_releases_first_projection_before_second_source_read():
    class Projection(list):
        pass

    first = None
    retained_at_second_read = []
    reader = AftersalesReader.__new__(AftersalesReader)

    def read(code, end):
        nonlocal first
        if first is not None:
            gc.collect()
            retained_at_second_read.append(first() is not None)
        result = Projection([identity_fact()])
        first = weakref.ref(result)
        return result

    reader.read = read
    reader.read_current_master = lambda raw: ("2026-10-27", [current_master()])
    raw, rows = reader.read_stable("jst_returns", date(2026, 10, 27))
    assert raw[0]["jst_current_master_record"]["first_category"] == "童帽秋冬"
    assert str(rows[0]["actual_returned_qty"]) == "1"
    assert retained_at_second_read == [False]


def test_streamed_arrays_and_ndjson_preserve_legacy_decimal_and_unicode_encoding():
    from datetime import datetime
    from decimal import Decimal

    from app.modules.aftersales.publisher import source_chunks
    from app.modules.sales.normalization import canonical as source_canonical
    from app.modules.sales.normalization import serializable
    from app.modules.streaming_json import fingerprint as streamed_fingerprint
    from app.modules.streaming_json import fingerprint_rows

    rows = [
        {"键": '换行\n引号"🌧', "value": Decimal("1.2300"), "date": date(2026, 11, 15)},
        {"value": None, "list": [True, 0, "é", "e\u0301"], "tuple": ("1", "10", "2")},
    ]
    legacy = json.dumps(
        rows, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    ).encode()
    assert fingerprint_rows(iter(rows)) == sha256(legacy).hexdigest()
    assert fingerprint_rows(iter(())) == sha256(b"[]").hexdigest()
    expected_lines = (
        b"\n".join(
            json.dumps(
                row, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
            ).encode()
            for row in rows
        )
        + b"\n"
    )
    assert b"".join(source_chunks(rows)) == expected_lines
    source = ([dict(amount=Decimal("1.2300"), at=datetime(2026, 11, 15, 9, 1, 2))], "时间")
    assert (
        streamed_fingerprint(source, default=serializable)
        == sha256(source_canonical(source).encode()).hexdigest()
    )
