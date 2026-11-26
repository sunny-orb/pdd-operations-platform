"""按总览范围分配实例，限制已发布结果的条数及单条容量。"""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from threading import Condition, Lock


class DefaultOverviewCache:
    """按已校验的版本与查询键复用结果；限制条数、单条及可选总字节预算。"""

    MAX_BYTES = 512 * 1024

    def __init__(
        self, *, max_entries: int = 1, max_bytes: int = MAX_BYTES,
        max_total_bytes: int | None = None,
    ):
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._computing: set[Hashable] = set()
        self._entries: OrderedDict[Hashable, bytes] = OrderedDict()
        self._max_entries = max_entries
        self.max_bytes = max_bytes
        self._max_total_bytes = max_total_bytes

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
        while len(self._entries) > self._max_entries or (
            self._max_total_bytes is not None
            and sum(map(len, self._entries.values())) > self._max_total_bytes
        ):
            self._entries.popitem(last=False)

    def get(self, key: Hashable) -> bytes | None:
        with self._lock:
            return self._get(key)

    def put(self, key: Hashable, data: bytes) -> None:
        with self._lock:
            self._put(key, data)

    def get_or_compute(self, version: Hashable, compute: Callable[[], bytes]) -> bytes:
        """同键合并计算，其他范围和热命中不等待；调用方等待时不占数据库连接。"""
        with self._condition:
            while True:
                cached = self._get(version)
                if cached is not None:
                    return cached
                if version not in self._computing:
                    self._computing.add(version)
                    break
                self._condition.wait()
        try:
            data = compute()
            with self._condition:
                if self._max_entries == 1:
                    self._entries.clear()
                self._put(version, data)
            return data
        finally:
            with self._condition:
                self._computing.discard(version)
                self._condition.notify_all()
