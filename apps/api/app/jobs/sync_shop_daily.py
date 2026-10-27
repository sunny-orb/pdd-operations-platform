"""受控本地M3首链接收；凭据仅在进程内，不启动定时任务。"""

import argparse
import getpass
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

import sqlalchemy as sa

from app.connectors.shop_daily import ShopDailyReader
from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.source_policy import SHOPS, approved_window, validate_read_window
from app.modules.sales.storage import ShopDailyStore

ROOT = Path(__file__).resolve().parents[4]


def sync_engine(url):
    """本地最多三连接：项目锁、评分发布锁、评分RR快照；上游仍串行。"""
    return sa.create_engine(
        url,
        hide_parameters=True,
        pool_size=3,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"},
    )


def runtime_engine():
    """沿既有本机写入身份，限定连接目标；不初始化或变更任何密码。"""
    from migrations_v2.local import local_url

    password = (ROOT / "var/local-db/sync.env").read_text().strip().split("=", 1)[1]
    url = local_url("pdd_operations").set(username="pdd_ingest", password=password)
    return sync_engine(url)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify-only", action="store_true")
    mode.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    reader = engine = None
    started = time.monotonic()
    try:
        window = ReadWindow(args.start, args.end, tuple(SHOPS))
        validate_read_window(window)
        if args.publish and window != approved_window():
            raise SalesError("PUBLICATION_SCOPE_MISMATCH")
        password = os.environ.get("WAREHOUSE_PASSWORD") or getpass.getpass(
            "数仓只读密码（不保存）："
        )
        reader = ShopDailyReader(password=password)
        del password
        if args.verify_only:
            candidate = reader.read_stable(window)
            output = dict(
                status="verified", rows=len(candidate.rows), fingerprint=candidate.fingerprint
            )
        else:
            engine = runtime_engine()
            store = ShopDailyStore(engine, archive_root=ROOT / "var/ingestion/shop_daily")

            def receive(heartbeat):
                reader.heartbeat = heartbeat
                return reader.read_stable(window)

            output = asdict(store.run(receive))
        print(
            json.dumps(
                {**output, "elapsed_seconds": round(time.monotonic() - started, 3)},
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="failed",
                    error=exc.code if isinstance(exc, SalesError) else type(exc).__name__,
                ),
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        if reader:
            reader.close()
        if engine:
            engine.dispose()
    return 0


def main():
    from app.jobs.project_sync import run_guarded

    engine = runtime_engine()
    try:
        return run_guarded(engine, _main)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
