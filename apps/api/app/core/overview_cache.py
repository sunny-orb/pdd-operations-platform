"""按总览范围分配实例，每个实例只保留一个已发布结果。"""

from collections.abc import Callable
from threading import Lock


class DefaultOverviewCache:
    """按已校验发布版本复用结果。同一版本首次请求只计算一次。"""

    MAX_BYTES = 512 * 1024

    def __init__(self):
        self._lock = Lock()
        self._entry: tuple[str, bytes] | None = None

    def get_or_compute(self, version: str, compute: Callable[[], bytes]) -> bytes:
        """调用方等待时不占用数据库连接。异常或超限结果不会写入缓存。"""
        with self._lock:
            if self._entry is not None and self._entry[0] == version:
                return self._entry[1]
            data = compute()
            self._entry = (version, data) if len(data) <= self.MAX_BYTES else None
            return data
