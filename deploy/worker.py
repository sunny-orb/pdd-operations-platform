"""Linux串行调度外壳；应用既有时槽、项目锁及重试规则负责业务幂等。"""

import signal
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
        child = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/sync-project.py"), "--scheduled"],
            cwd=ROOT,
        )
        child.wait()
        child = None
        stop.wait(60)


if __name__ == "__main__":
    main()
