"""不同范围的慢聚合不得阻塞热缓存或独立查询。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.core.overview_cache import DefaultOverviewCache


def test_slow_scope_does_not_block_cached_or_other_scopes():
    cache = DefaultOverviewCache(max_entries=4)
    cache.put("warm", b"published")
    started, release = Event(), Event()

    def slow():
        started.set()
        assert release.wait(3)
        return b"slow-result"

    with ThreadPoolExecutor(max_workers=3) as pool:
        slow_result = pool.submit(cache.get_or_compute, "slow", slow)
        try:
            assert started.wait(1)
            hot_result = pool.submit(cache.get, "warm")
            cold_result = pool.submit(cache.get_or_compute, "other", lambda: b"other-result")
            assert hot_result.result(timeout=1) == b"published"
            assert cold_result.result(timeout=1) == b"other-result"
        finally:
            release.set()
        assert slow_result.result(timeout=1) == b"slow-result"


def test_failed_compute_releases_same_scope_waiters():
    cache = DefaultOverviewCache()
    started, release = Event(), Event()

    def broken():
        started.set()
        assert release.wait(3)
        raise ValueError("temporary failure")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_compute, "scope", broken)
        assert started.wait(1)
        second = pool.submit(cache.get_or_compute, "scope", lambda: b"recovered")
        release.set()
        assert isinstance(first.exception(timeout=1), ValueError)
        assert second.result(timeout=1) == b"recovered"
        assert cache.get("scope") == b"recovered"
