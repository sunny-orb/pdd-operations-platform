"""定时任务的独立进程组监督；只终止本次创建的进程，不操作共享服务。"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class WorkerPolicy:
    run_timeout_seconds: float = 900
    termination_grace_seconds: float = 10
    poll_seconds: float = 0.1

    def __post_init__(self):
        import math

        for value in vars(self).values():
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("INVALID_WORKER_POLICY")
        if self.termination_grace_seconds > 20:
            raise ValueError("INVALID_WORKER_POLICY")


def read_policy(root):
    path = root / "config/worker.toml"
    return WorkerPolicy(**tomllib.loads(path.read_text())) if path.exists() else WorkerPolicy()


def log_event(event, **fields):
    print(json.dumps(dict(event=event, at=datetime.now(UTC).isoformat(), **fields)), flush=True)


def _signal_group(pid, signum):
    try:
        os.killpg(pid, signum)
        return True
    except ProcessLookupError:
        return False


def run_child(command, *, root, policy, stop):
    """截止后TERM/KILL整个本轮进程组；旧组仍存在时一直等待，不并发启动替代者。"""
    started = time.monotonic()
    child = subprocess.Popen(
        command,
        cwd=root,
        start_new_session=True,
        env={**os.environ, "PDD_SYNC_SUPERVISED": "1"},
    )
    log_event("sync_child_started", pid=child.pid)
    terminating = None
    timed_out = False
    killed = False
    while True:
        code = child.poll()
        now = time.monotonic()
        alive = _signal_group(child.pid, 0)
        if code is not None and not alive:
            return child.pid, code, timed_out, round(now - started, 3)
        if terminating is None and (
            stop.is_set() or now - started >= policy.run_timeout_seconds or code is not None
        ):
            timed_out = not stop.is_set() and code is None
            terminating = now
            log_event("sync_child_terminating", pid=child.pid, timed_out=timed_out)
            _signal_group(child.pid, signal.SIGTERM)
        if terminating is not None and now - terminating >= policy.termination_grace_seconds:
            if not killed:
                _signal_group(child.pid, signal.SIGKILL)
                log_event("sync_child_killed", pid=child.pid)
                killed = True
        time.sleep(policy.poll_seconds)


class TaskInterrupted(BaseException):
    """绕过来源级Exception重试，展开finally并退出整轮。"""


def scheduled_entry(main, root):
    """本机scheduled入口与容器共用监督，不重复套进程组；手工命令维持原行为。"""
    if "--scheduled" not in sys.argv:
        return main()
    if os.environ.get("PDD_SYNC_SUPERVISED") == "1":

        def interrupt(*_):
            raise TaskInterrupted()

        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)
        try:
            return main()
        except TaskInterrupted:
            log_event("sync_interrupted")
            return 130
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    pid, code, timed_out, elapsed = run_child(
        [sys.executable, *sys.argv], root=root, policy=read_policy(root), stop=stop
    )
    log_event(
        "sync_child_finished",
        pid=pid,
        returncode=code,
        timed_out=timed_out,
        elapsed_seconds=elapsed,
    )
    return 124 if timed_out else code if code >= 0 else 128 - code
