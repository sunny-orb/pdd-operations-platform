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

from app.jobs.project_runtime import ProjectPaths, execute_project
from app.jobs.project_sync import Schedule, due_slot, read_schedule
from app.jobs.sync_shop_daily import runtime_engine


def source_password():
    path = ROOT / "var/local-db/sources.env"
    if path.stat().st_mode & 0o077:
        raise ValueError("SOURCE_CREDENTIAL_PERMISSIONS")
    values = dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line
    )
    return values["WAREHOUSE_PASSWORD"]


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
        if args.only and args.scheduled:
            raise ValueError("INVALID_SYNC_DATASETS")
        result = execute_project(
            engine,
            slot,
            paths=ProjectPaths(
                ingestion_root=ROOT / "var/ingestion",
                run_archive_root=ROOT / "var/project-sync",
                refund_targets_config=ROOT / "config/aftersales-targets.toml",
                feishu_root=ROOT,
                scoring_archive_root=ROOT / "var/scoring/product_board",
            ),
            warehouse_password=source_password,
            only=args.only,
            max_attempts=config.max_attempts_per_slot,
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
