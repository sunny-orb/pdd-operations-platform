"""M3其余来源受控串行接收，单来源失败保旧版并继续独立来源。"""

import argparse
import getpass
import json
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.connectors.sales_feishu import FeishuRefundReader
from app.connectors.sales_sources import SalesSourceReader
from app.jobs.sync_shop_daily import runtime_engine
from app.modules.sales.contracts import ReadWindow, SalesError
from app.modules.sales.datasets import SPECS
from app.modules.sales.source_policy import SHOPS, approved_window, validate_read_window
from app.modules.sales.storage import ShopDailyStore

ROOT = Path(__file__).resolve().parents[4]


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["all", *SPECS], default="all")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify-only", action="store_true")
    mode.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    reader = engine = None
    errors = 0
    try:
        window = ReadWindow(args.start, args.end, tuple(SHOPS))
        validate_read_window(window)
        if args.publish and window != approved_window():
            raise SalesError("PUBLICATION_SCOPE_MISMATCH")
        selected = list(SPECS) if args.dataset == "all" else [args.dataset]
        if any(SPECS[code].source_system == "warehouse" for code in selected):
            password = getpass.getpass("数仓只读密码（不保存）：")
            reader = SalesSourceReader(password=password)
            del password
        if args.publish:
            engine = runtime_engine()
        for code in selected:
            started = time.monotonic()
            try:
                source = FeishuRefundReader(root=ROOT) if code == "shop_refunds" else reader

                def receive(heartbeat, source=source, code=code):
                    source.heartbeat = heartbeat
                    return (
                        source.read_stable(window)
                        if code == "shop_refunds"
                        else source.read_stable(code, window)
                    )

                if args.publish:
                    store = ShopDailyStore(
                        engine, archive_root=ROOT / "var/ingestion" / code, spec=SPECS[code]
                    )
                    result = asdict(store.run(receive))
                else:
                    candidate = receive(lambda: None)
                    result = dict(
                        status="verified",
                        rows=len(candidate.rows),
                        fingerprint=candidate.fingerprint,
                    )
                print(
                    json.dumps(
                        dict(
                            dataset=code,
                            **result,
                            elapsed_seconds=round(time.monotonic() - started, 3),
                        ),
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as error:
                errors += 1
                print(
                    json.dumps(
                        dict(
                            dataset=code,
                            status="failed",
                            error=error.code
                            if isinstance(error, SalesError)
                            else type(error).__name__,
                        ),
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    except Exception as error:
        errors += 1
        print(
            json.dumps(
                dict(
                    status="failed",
                    error=error.code if isinstance(error, SalesError) else type(error).__name__,
                )
            )
        )
    finally:
        if reader:
            reader.close()
        if engine:
            engine.dispose()
    return int(errors > 0)


def main():
    from app.jobs.project_sync import run_guarded

    engine = runtime_engine()
    try:
        return run_guarded(engine, _main)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
