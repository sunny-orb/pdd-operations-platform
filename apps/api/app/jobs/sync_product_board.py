"""本地M1三表受控接收。凭据仅隐藏输入，运行摘要不含业务原行。"""

import argparse
import getpass
import json
import sys

from app.connectors.product_board import ProductBoardReader
from app.jobs.sync_shop_daily import ROOT, runtime_engine
from app.modules.product_board.storage import ProductBoardStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    reader = engine = None
    try:
        reader = ProductBoardReader(password=getpass.getpass("数仓只读密码（不保存）："))
        if args.verify_only:
            end, rows = reader.read_stable()
            print(
                json.dumps(
                    dict(status="verified", end=str(end), rows={k: len(v) for k, v in rows.items()})
                )
            )
            return 0
        engine = runtime_engine()

        def receive(heartbeat):
            reader.heartbeat = heartbeat
            return reader.read_stable()

        result = ProductBoardStore(engine, ROOT / "var/ingestion/product_board").run(receive)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    status="failed",
                    error=type(exc).__name__,
                    module=type(exc).__module__,
                    source_code=exc.args[0] if exc.args and isinstance(exc.args[0], int) else None,
                    sqlstate=getattr(getattr(exc, "orig", None), "sqlstate", None),
                    diagnostic=str(exc)[:350]
                    if type(exc).__module__.startswith("pymysql")
                    else None,
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


if __name__ == "__main__":
    sys.exit(main())
