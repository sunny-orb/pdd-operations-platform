"""Linux串行调度外壳；应用既有时槽、项目锁及重试规则负责业务幂等。"""

import json
import resource
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def oom_kills():
    """仅读本容器cgroup计数；宿主测试无该文件时不猜测OOM。"""
    try:
        values = dict(
            line.split() for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines()
        )
        return int(values["oom_kill"])
    except (OSError, KeyError, ValueError):
        return None


def log_event(event, **fields):
    print(json.dumps(dict(event=event, at=datetime.now(UTC).isoformat(), **fields)), flush=True)


def main():
    stop = threading.Event()
    child = None

    def shutdown(*_):
        stop.set()
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not stop.is_set():
        started, before_oom = time.monotonic(), oom_kills()
        child = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/sync-project.py"), "--scheduled"],
            cwd=ROOT,
        )
        log_event("sync_child_started", pid=child.pid)
        code = child.wait()
        after_oom = oom_kills()
        log_event(
            "sync_child_finished",
            pid=child.pid,
            returncode=code,
            signal=signal.Signals(-code).name if code < 0 else None,
            elapsed_seconds=round(time.monotonic() - started, 3),
            # getrusage为此外壳所有已结束子进程的最高值，不标作本轮独占峰值。
            children_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            cgroup_oom_kill_delta=after_oom - before_oom
            if after_oom is not None and before_oom is not None
            else None,
        )
        child = None
        stop.wait(60)


if __name__ == "__main__":
    main()
