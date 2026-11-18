"""本地项目独立同步入口；定时和手动共用已接入来源，不向上游写入。"""

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.jobs.project_runtime import ProjectPaths, execute_project
from app.jobs.project_sync import due_slot, read_schedule
from app.jobs.sync_shop_daily import runtime_engine
from app.modules.data_status.schedule_policy import JST_AFTERSALES_STEPS


def source_password():
    if os.environ.get("WAREHOUSE_PASSWORD"):
        return os.environ["WAREHOUSE_PASSWORD"]
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
    modes.add_argument(
        "--check-updates", action="store_true", help="检查已接入来源，变化后受控同步"
    )
    modes.add_argument("--set-times", nargs="+")
    parser.add_argument("--only", nargs="+", help="手动仅执行指定已接入来源")
    args = parser.parse_args()
    path = ROOT / "config/sync.toml"
    engine = None
    try:
        config = read_schedule(path)
        if (
            not config.jst_aftersales_enabled
            and args.only
            and set(args.only) & set(JST_AFTERSALES_STEPS)
        ):
            raise ValueError("JST_AFTERSALES_DISABLED")
        if args.set_times:
            config = replace(config, times=tuple(sorted(args.set_times)))
            content = (
                "# 每分钟重新读取；时区为Asia/Shanghai。\n"
                f"enabled = {str(config.enabled).lower()}\n"
                'timezone = "Asia/Shanghai"\n'
                f"times = {json.dumps(list(config.times))}\n"
                f"max_attempts_per_slot = {config.max_attempts_per_slot}\n"
                f"monitor_enabled = {str(config.monitor_enabled).lower()}\n"
                f"monitor_interval_seconds = {config.monitor_interval_seconds}\n"
                f"source_schedule_enabled = {str(config.source_schedule_enabled).lower()}\n"
                f"warehouse_times = {json.dumps(list(config.warehouse_times))}\n"
                f"feishu_times = {json.dumps(list(config.feishu_times))}\n"
                f"reconciliation_time = {json.dumps(config.reconciliation_time)}\n"
                f"reconciliation_retry_seconds = {config.reconciliation_retry_seconds}\n"
                f"jst_aftersales_enabled = {str(config.jst_aftersales_enabled).lower()}\n"
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
        monitor = args.check_updates or (
            args.scheduled and config.enabled and config.monitor_enabled
        )
        new_schedule = config.source_schedule_enabled and config.enabled
        if not slot and not monitor and not (new_schedule and args.scheduled):
            return 0
        engine = runtime_engine()
        if args.only and (args.scheduled or args.check_updates):
            raise ValueError("INVALID_SYNC_DATASETS")
        paths = ProjectPaths(
            ingestion_root=ROOT / "var/ingestion",
            run_archive_root=ROOT / "var/project-sync",
            refund_targets_config=ROOT / "config/aftersales-targets.toml",
            feishu_root=ROOT,
            scoring_archive_root=ROOT / "var/scoring/product_board",
        )
        from app.modules.data_status.schedule_policy import (
            FEISHU_SOURCES,
            LEGACY_SOURCES,
            LEGACY_STEPS,
            MONITOR_ONLY_SOURCES,
            WAREHOUSE_SOURCES,
        )

        manual_sources = ()
        legacy_steps = tuple(
            step
            for step in LEGACY_STEPS
            if config.jst_aftersales_enabled or step not in JST_AFTERSALES_STEPS
        )
        manual_steps = args.only
        manual_processing = False
        if args.run_now and new_schedule:
            migrated = (*WAREHOUSE_SOURCES, *FEISHU_SOURCES)
            allowed = {*migrated, *legacy_steps, "products", "product_scores"}
            if args.only and not set(args.only) <= allowed:
                raise ValueError("INVALID_SYNC_DATASETS")
            selected = set(args.only) if args.only else allowed
            product_ids = {"product_daily", "product_promotion", "profile_records"}
            if "products" in selected:
                selected |= product_ids
            manual_sources = tuple(code for code in migrated if code in selected)
            manual_processing = "product_scores" in selected and bool(
                product_ids & selected
            )
            manual_steps = tuple(
                step
                for step in (*legacy_steps, "product_scores")
                if step in selected
                and not (step == "product_scores" and manual_processing)
            )
        result = {"status": "already_succeeded"}
        if (
            slot
            and not args.check_updates
            and not (args.run_now and new_schedule and not manual_steps)
        ):
            result = execute_project(
                engine,
                slot,
                paths=paths,
                warehouse_password=source_password,
                only=legacy_steps if args.scheduled and new_schedule else manual_steps,
                jst_aftersales_enabled=config.jst_aftersales_enabled,
                max_attempts=config.max_attempts_per_slot,
                **(
                    {
                        "reconciliation_sources": ("product_regions",),
                        "reconciliation_time": config.reconciliation_time,
                    }
                    if args.scheduled and new_schedule
                    else {}
                ),
            )
        if result["status"] not in (
            "already_succeeded",
            "already_running",
            "retry_budget_exhausted",
        ):
            print(json.dumps(result, ensure_ascii=False), flush=True)
        monitor_failed = False
        if monitor:
            from app.connectors.aftersales_rpa_status import production_connection
            from app.jobs.source_monitor_runtime import execute_monitor

            def production_rpa():
                return production_connection(engine)

            checked = execute_monitor(
                engine,
                paths=paths,
                warehouse_password=source_password,
                interval_seconds=config.monitor_interval_seconds,
                source_ids=(*LEGACY_SOURCES, *MONITOR_ONLY_SOURCES)
                if new_schedule
                else None,
                reconciliation_sources=("product_regions",) if new_schedule else (),
                reconciliation_time=config.reconciliation_time,
                jst_aftersales_enabled=config.jst_aftersales_enabled,
                rpa_connect=production_rpa
                if os.environ.get("APP_ENV") == "production"
                else None,
            )
            if checked.get("checked") or checked["status"] != "checked":
                print(json.dumps(checked, ensure_ascii=False), flush=True)
            monitor_failed = checked["status"] in ("failed", "partial")
        if new_schedule and (args.scheduled or args.check_updates or manual_sources):
            from app.jobs.source_monitor_runtime import execute_source_schedule

            checked = execute_source_schedule(
                engine,
                paths=paths,
                warehouse_password=source_password,
                warehouse_times=config.warehouse_times,
                feishu_times=config.feishu_times,
                reconciliation_time=config.reconciliation_time,
                reconciliation_retry_seconds=config.reconciliation_retry_seconds,
                **(
                    {
                        "source_ids": manual_sources,
                        "force": True,
                        "process_products": manual_processing,
                    }
                    if manual_sources
                    else {}
                ),
            )
            if checked.get("checked") or checked["status"] != "checked":
                print(json.dumps(checked, ensure_ascii=False), flush=True)
            monitor_failed = monitor_failed or checked["status"] in (
                "failed",
                "partial",
            )
        if args.scheduled and config.enabled:
            from app.modules.archive_compaction import compact_archives

            try:
                maintenance = compact_archives(engine, ROOT / "var/ingestion", scheduled=True)
                if (maintenance.get("converted") or maintenance.get("collected_blocks")
                    or maintenance.get("removed_temporary_files") or maintenance.get("status") == "budget"):
                    print(
                        json.dumps(dict(stage="archive_compaction", **maintenance)),
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001 - 维护异常不得覆盖已成功完成的取数结果
                # 维护失败保留输入；继续正常检查，由来源本身如实记录容量或接收失败。
                code = (
                    str(exc)
                    if str(exc)
                    in {
                        "ARCHIVE_CAPACITY",
                        "ARCHIVE_CHECKSUM",
                        "ARCHIVE_RESTORE_REQUIRED",
                        "ARCHIVE_PATH",
                        "ARCHIVE_IDENTITY",
                        "ARCHIVE_MANIFEST",
                    }
                    else "ARCHIVE_MAINTENANCE_FAILED"
                )
                print(
                    json.dumps(
                        {
                            "stage": "archive_compaction",
                            "status": "failed",
                            "code": code,
                        }
                    ),
                    flush=True,
                )
        return int(result["status"] == "failed" or monitor_failed)
    except Exception as exc:  # noqa: BLE001 - CLI边界只输出异常类型，避免凭据泄漏
        print(json.dumps({"status": "failed", "error": type(exc).__name__}), flush=True)
        return 1
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    from app.jobs.process_guard import scheduled_entry

    sys.exit(scheduled_entry(main, ROOT))
