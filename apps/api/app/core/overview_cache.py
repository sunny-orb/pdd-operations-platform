"""按总览范围分配实例，限制已发布结果的条数及单条容量。"""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from threading import Lock


class DefaultOverviewCache:
    """按已校验发布版本复用结果。同一版本首次请求只计算一次。"""

    MAX_BYTES = 512 * 1024

    def __init__(self, *, max_entries: int = 1, max_bytes: int = MAX_BYTES):
        self._lock = Lock()
        self._entries: OrderedDict[Hashable, bytes] = OrderedDict()
        self._max_entries = max_entries
        self.max_bytes = max_bytes

    def _get(self, key):
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key]
        return None

    def _put(self, key, data):
        if len(data) > self.max_bytes:
            return
        self._entries[key] = data
        self._entries.move_to_end(key)
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def get(self, key: Hashable) -> bytes | None:
        with self._lock:
            return self._get(key)

    def put(self, key: Hashable, data: bytes) -> None:
        with self._lock:
            self._put(key, data)

    def get_or_compute(self, version: str, compute: Callable[[], bytes]) -> bytes:
        """调用方等待时不占用数据库连接。异常或超限结果不会写入缓存。"""
        with self._lock:
            cached = self._get(version)
            if cached is not None:
                return cached
            data = compute()
            if self._max_entries == 1:
                self._entries.clear()
            self._put(version, data)
            return data
