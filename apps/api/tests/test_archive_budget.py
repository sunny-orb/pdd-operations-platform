"""维护预算实际限制等待和块读取，离开上下文后正常历史读数不节流。"""

import gzip
import hashlib
import time

import pytest


def test_maintenance_deadline_interrupts_wait_and_restores_normal_execution():
    from app.modules.archive_budget import maintenance_budget

    start = time.monotonic()
    with pytest.raises(ValueError, match="ARCHIVE_MAINTENANCE_BUDGET"):
        with maintenance_budget(0.05):
            time.sleep(0.5)
    assert time.monotonic() - start < 0.3


def test_actual_block_read_is_paced_only_during_maintenance(tmp_path):
    from app.modules.archive_budget import maintenance_budget
    from app.modules.archive_payload import read_block

    root = tmp_path / "ingestion"
    data = b"history\n" * 1024
    digest = hashlib.sha256(data).hexdigest()
    path = root / ".payloads" / digest[:2] / (digest + ".gz")
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(data))
    item = {"sha256": digest, "size": len(data)}
    start = time.monotonic()
    with maintenance_budget(1, bytes_per_second=len(data) * 10):
        assert read_block(root, item) == data
    assert time.monotonic() - start >= 0.09
    # 新的过小预算到读文件前即停止，不影响其后的正常历史读取。
    with pytest.raises(ValueError, match="ARCHIVE_MAINTENANCE_BUDGET"):
        with maintenance_budget(0.01, bytes_per_second=1):
            read_block(root, item)
    assert read_block(root, item) == data
