"""售后固定输入的有界内存复用；不保存经营文件、不缓存实时同步状态。"""

import json
import zlib
from collections import OrderedDict
from datetime import date
from decimal import Decimal
from threading import Lock

from app.core.overview_cache import DefaultOverviewCache

DECIMAL_FIELDS = {
    "actual_returned_qty",
    "actual_inbound_amount",
    "sales_amount",
    "source_sales_qty",
    "_absolute_actual_returned_qty",
    "_absolute_actual_inbound_amount",
    "_absolute_sales_amount",
    "_absolute_source_sales_qty",
    "_amount_known_subtotal",
}
DATE_FIELDS = {"period_start", "period_end", "first", "last"}


class ReportRowsCache:
    """按已核版本/角色/窗口复用压缩行；每次返回独立对象，LRU总量不超过4MiB。"""

    MAX_RAW_BYTES = 8 * 1024 * 1024

    def __init__(self, max_bytes=4 * 1024 * 1024, max_entries=24):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            data = self._entries.get(key)
            if data is None:
                return None
            self._entries.move_to_end(key)
        columns, values, summary = json.loads(zlib.decompress(data))
        converters = [
            Decimal if c in DECIMAL_FIELDS else date.fromisoformat if c in DATE_FIELDS else None
            for c in columns
        ]
        rows = [
            {
                c: convert(v) if convert and v is not None else v
                for c, convert, v in zip(columns, converters, row, strict=True)
            }
            for row in values
        ]
        if summary is not None:
            summary = {
                k: (
                    Decimal(v)
                    if k in DECIMAL_FIELDS
                    else date.fromisoformat(v)
                    if k in DATE_FIELDS
                    else v
                )
                if v is not None
                else None
                for k, v in summary.items()
            }
        return rows, summary

    def put(self, key, rows, summary=None):
        # 大报告保持原查询路径，避免为缓存再编码巨大对象。
        if len(rows) > 50000:
            return
        columns = list(rows[0]) if rows else []
        raw = json.dumps(
            [columns, [[row.get(c) for c in columns] for row in rows], summary],
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        if len(raw) > self.MAX_RAW_BYTES:
            return
        data = zlib.compress(raw, level=1)
        if len(data) > self.max_bytes:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            self._bytes -= len(old) if old else 0
            self._entries[key] = data
            self._bytes += len(data)
            while self._bytes > self.max_bytes or len(self._entries) > self.max_entries:
                _, removed = self._entries.popitem(last=False)
                self._bytes -= len(removed)


class AftersalesCache(DefaultOverviewCache):
    """沿用原8MiB售后预算：4MiB结果 + 4MiB固定输入；不增加常驻数据预算。"""

    def __init__(self):
        super().__init__(max_entries=32, max_bytes=1024 * 1024)
        self.rows = ReportRowsCache()

    def _put(self, key, data):
        super()._put(key, data)
        while sum(map(len, self._entries.values())) > 4 * 1024 * 1024:
            self._entries.popitem(last=False)
