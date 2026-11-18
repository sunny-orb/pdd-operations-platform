"""只在维护上下文启用的读写/遍历节流；正常业务读取不改变，状态不落盘。"""

import os
import re
import signal
import stat
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

ACTIVE = ContextVar("archive_maintenance_budget", default=None)
PENDING = re.compile(r"\.pending-[a-z0-9_]{8}")


def repair_pending_link(path, info, progress):
    """仅在归档排他锁内恢复旧link提交的同目录二元链接，不接受外部/未知硬链接。"""
    if info.st_nlink != 2:
        raise ValueError("ARCHIVE_PATH")
    pair = []
    with os.scandir(path.parent) as siblings:
        for entry in siblings:
            consume()
            # 同目录readdir已有inode；只对相同inode执行stat，避免大目录恢复先耗尽I/O额度。
            if entry.inode() != info.st_ino:
                continue
            consume(entries=1)
            candidate = entry.stat(follow_symlinks=False)
            if (candidate.st_dev, candidate.st_ino) == (info.st_dev, info.st_ino):
                pair.append((Path(entry.path), candidate))
    pending = [p for p, _ in pair if PENDING.fullmatch(p.name)]
    if (
        len(pair) != 2
        or len(pending) != 1
        or any(not stat.S_ISREG(i.st_mode) or i.st_nlink != 2 for _, i in pair)
    ):
        raise ValueError("ARCHIVE_PATH")
    for p, i in pair:
        current = p.lstat()
        if (current.st_dev, current.st_ino, current.st_nlink) != (i.st_dev, i.st_ino, 2):
            raise ValueError("ARCHIVE_CHANGED")
    pending[0].unlink()
    if progress is not None:
        progress["removed_temporary_files"] = progress.get("removed_temporary_files", 0) + 1
    return path.lstat() if path != pending[0] else None


class Budget:
    def __init__(self, seconds, bytes_per_second=0, entries_per_second=0):
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.bytes_per_second, self.entries_per_second = bytes_per_second, entries_per_second
        self.bytes = self.entries = 0

    def consume(self, size=0, entries=0):
        now = time.monotonic()
        self.bytes += size
        self.entries += entries
        ready = self.started + max(
            self.bytes / self.bytes_per_second if self.bytes_per_second else 0,
            self.entries / self.entries_per_second if self.entries_per_second else 0,
        )
        if max(now, ready) >= self.deadline:
            raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")
        if ready > now:
            time.sleep(ready - now)


def consume(size=0, entries=0):
    budget = ACTIVE.get()
    if budget is not None:
        budget.consume(size, entries)


@contextmanager
def maintenance_budget(seconds, bytes_per_second=0, entries_per_second=0):
    budget = Budget(seconds, bytes_per_second, entries_per_second)
    token = ACTIVE.set(budget)
    armed = (
        threading.current_thread() is threading.main_thread()
        and not signal.getitimer(signal.ITIMER_REAL)[0]
    )
    previous = None

    def expired(*_):
        raise ValueError("ARCHIVE_MAINTENANCE_BUDGET")

    try:
        budget.consume()
        if armed:
            previous = signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, seconds)
        yield budget
    finally:
        if previous is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        ACTIVE.reset(token)


def inventory(root, *, repair=False, progress=None):
    """逐目录项检查预算和链接，不先rglob整树；只保留调用方必要的小型元数据。"""
    pending = [Path(root)]
    while pending:
        consume()
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                consume(entries=1)
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("ARCHIVE_PATH")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    if not repair:
                        raise ValueError("ARCHIVE_PATH")
                    info = repair_pending_link(Path(entry.path), info, progress)
                    if info is None:
                        continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    yield Path(entry.path), info
                else:
                    raise ValueError("ARCHIVE_PATH")


def resume_inventory(root, state, progress):
    """小型目录栈跨进程续扫；旧目录前缀仅读名称，不重stat/限速；绕回覆盖期间增删。"""
    from app.modules.archive_payload import safe

    stack = state.setdefault("scan_stack", [])
    if not stack:
        stack.append({"path": "", "offset": 0})
    if len(stack) > 32 or any(
        set(frame) != {"path", "offset"}
        or not isinstance(frame["path"], str)
        or len(frame["path"]) > 1024
        or Path(frame["path"]).is_absolute()
        or ".." in Path(frame["path"]).parts
        or type(frame["offset"]) is not int
        or not 0 <= frame["offset"] <= 100_000_000
        for frame in stack
    ):
        raise ValueError("ARCHIVE_MAINTENANCE_STATE")
    while stack:
        consume()
        frame = stack[-1]
        directory = safe(root / frame["path"])
        if not directory.is_dir():
            stack.pop()
            continue
        descended = False
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                consume()
                if index < frame["offset"]:
                    continue
                consume(entries=1)
                frame["offset"] = index + 1
                path = Path(entry.path)
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if len(stack) >= 32:
                        raise ValueError("ARCHIVE_PATH")
                    stack.append({"path": str(path.relative_to(root)), "offset": 0})
                    descended = True
                    break
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("ARCHIVE_PATH")
                if info.st_nlink != 1:
                    info = repair_pending_link(path, info, progress)
                    if info is None:
                        continue
                yield path, info
        if not descended:
            stack.pop()
