"""本地项目独立同步入口；定时和手动共用已接入来源，不向上游写入。"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.jobs.project_sync import Schedule, due_slot, read_schedule, run_project
from app.jobs.sync_shop_daily import runtime_engine


def source_password():
    path = ROOT / "var/local-db/sources.env"
    if path.stat().st_mode & 0o077:
        raise ValueError("SOURCE_CREDENTIAL_PERMISSIONS")
    values = dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line
    )
    return values["WAREHOUSE_PASSWORD"]


def build_steps(engine, password):
    from app.connectors.product_board import ProductBoardReader
    from app.connectors.sales_feishu import FeishuRefundReader
    from app.connectors.sales_sources import SalesSourceReader
    from app.connectors.shop_daily import ShopDailyReader
    from app.modules.product_board.storage import ProductBoardStore
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_policy import approved_window
    from app.modules.sales.storage import ShopDailyStore

    def products():
        reader = None
        try:

            def receive(hb):
                nonlocal reader
                reader = ProductBoardReader(password=password, heartbeat=hb)
                return reader.read_stable()

            return ProductBoardStore(
                engine, ROOT / "var/ingestion/product_board", reconcile_full_scope=True
            ).run(receive)
        finally:
            if reader is not None:
                reader.close()

    def sales(code):
        reader = None
        window = approved_window()
        try:

            def receive(hb):
                nonlocal reader
                reader = (
                    ShopDailyReader(password=password, heartbeat=hb)
                    if code == "shop_daily"
                    else FeishuRefundReader(root=ROOT, heartbeat=hb)
                    if code == "shop_refunds"
                    else SalesSourceReader(password=password, heartbeat=hb)
                )
                return (
                    reader.read_stable(window)
                    if code in ("shop_daily", "shop_refunds")
                    else reader.read_stable(code, window)
                )

            store = ShopDailyStore(
                engine,
                archive_root=ROOT / "var/ingestion" / code,
                spec=None if code == "shop_daily" else SPECS[code],
                reconcile_full_scope=True,
            )
            return asdict(store.run(receive))
        finally:
            if hasattr(reader, "close"):
                reader.close()

    def scores():
        from app.modules.product_scoring.publisher import publish_scores

        result = publish_scores(engine)
        return {**result, "version_id": result["versions"]["product_scores"]}

    def aftersales(code):
        from app.connectors.aftersales import AftersalesReader
        from app.modules.aftersales.publisher import publish_source

        reader = None
        try:

            def receive(hb):
                nonlocal reader
                reader = AftersalesReader(password=password, heartbeat=hb)
                end = reader.latest(code)
                raw, rows = reader.read_stable(code, end)
                return end, raw, rows

            return publish_source(
                engine, code, receive, ROOT / "var/ingestion/aftersales"
            )
        finally:
            if reader is not None:
                reader.close()

    def reports():
        from app.modules.aftersales.publisher import publish_reports

        return publish_reports(engine, ROOT / "var/ingestion/aftersales")

    return {
        "products": products,
        "product_scores": scores,
        **{code: lambda code=code: sales(code) for code in ("shop_daily", *SPECS)},
        **{
            code: lambda code=code: aftersales(code)
            for code in ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales")
        },
        "aftersales_report": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--scheduled", action="store_true")
    modes.add_argument("--run-now", action="store_true")
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--set-times", nargs="+")
    parser.add_argument("--only", nargs="+", help="手动仅执行指定已接入来源")
    args = parser.parse_args()
    path = ROOT / "config/sync.toml"
    engine = None
    try:
        config = read_schedule(path)
        if args.set_times:
            config = Schedule(
                times=tuple(sorted(args.set_times)),
                enabled=config.enabled,
                max_attempts_per_slot=config.max_attempts_per_slot,
            )
            content = (
                "# 每分钟重新读取；时区为Asia/Shanghai。\n"
                f"enabled = {str(config.enabled).lower()}\n"
                'timezone = "Asia/Shanghai"\n'
                f"times = {json.dumps(list(config.times))}\n"
                f"max_attempts_per_slot = {config.max_attempts_per_slot}\n"
            )
            tmp = path.with_suffix(".tmp")
            tmp.write_text(content)
            os.replace(tmp, path)
            print(json.dumps(asdict(config), ensure_ascii=False))
            return 0
        if args.status:
            print(json.dumps(asdict(config), ensure_ascii=False))
            return 0
        slot = (
            due_slot(config, datetime.now(ZoneInfo("Asia/Shanghai")))
            if args.scheduled
            else "manual:" + str(uuid4())
        )
        if not slot:
            return 0
        engine = runtime_engine()
        # 凭据仅当前进程；闭包不打印；由用户授权的完整分页双读接收。
        steps = build_steps(engine, source_password())
        if args.only:
            if args.scheduled or not set(args.only) <= steps.keys():
                raise ValueError("INVALID_SYNC_DATASETS")
            steps = {k: v for k, v in steps.items() if k in args.only}
        result = run_project(
            engine,
            slot,
            steps,
            ROOT / "var/project-sync",
            max_attempts=config.max_attempts_per_slot,
            depends_on={
                "product_scores": tuple(k for k in ("products",) if k in steps),
                "aftersales_report": tuple(
                    k
                    for k in ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales")
                    if k in steps
                ),
            },
        )
        if result["status"] not in (
            "already_succeeded",
            "already_running",
            "retry_budget_exhausted",
        ):
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return int(result["status"] == "failed")
    except Exception as exc:  # noqa: BLE001 - CLI边界只输出异常类型，避免凭据泄漏
        print(json.dumps({"status": "failed", "error": type(exc).__name__}), flush=True)
        return 1
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
