"""Linux串行调度外壳；应用既有时槽、项目锁及重试规则负责业务幂等。"""

import resource
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.jobs.forecast_worker import start_worker_api
from app.jobs.process_guard import log_event, read_policy, run_child


def oom_kills():
    """仅读本容器cgroup计数；宿主测试无该文件时不猜测OOM。"""
    try:
        values = dict(
            line.split() for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines()
        )
        return int(values["oom_kill"])
    except (OSError, KeyError, ValueError):
        return None


def main():
    stop = threading.Event()
    sop_api = start_worker_api()

    def shutdown(*_):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not stop.is_set():
        policy = read_policy(ROOT)
        before_oom = oom_kills()
        pid, code, timed_out, elapsed = run_child(
            [sys.executable, str(ROOT / "scripts/sync-project.py"), "--scheduled"],
            root=ROOT, policy=policy, stop=stop,
        )
        after_oom = oom_kills()
        log_event(
            "sync_child_finished",
            pid=pid,
            returncode=code,
            timed_out=timed_out,
            signal=signal.Signals(-code).name if code < 0 else None,
            elapsed_seconds=elapsed,
            # getrusage为此外壳所有已结束子进程的最高值，不标作本轮独占峰值。
            children_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            cgroup_oom_kill_delta=after_oom - before_oom
            if after_oom is not None and before_oom is not None
            else None,
        )
        # 下一轮先持锁恢复中断状态，再由来源检查点等待300秒；不叠加第二层5分钟。
        stop.wait(60)
    if sop_api:
        sop_api.should_exit = True


if __name__ == "__main__":
    main()
