"""本项目归档维护：plan只读，export流式压缩，verify全量校验，apply需离线回执。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.jobs.sync_shop_daily import runtime_engine
from app.modules.archive_retention import (
    apply_verified_plan,
    export_bundle,
    file_record,
    plan_retention,
    restore_bundle,
    safe_member,
    verify_bundle,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["plan", "export", "verify", "apply", "restore"]
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    root = ROOT / "var/ingestion"
    if args.action in ("verify", "restore"):
        if args.bundle is None:
            parser.error("需要--bundle")
        with args.bundle.open("rb") as source:
            if args.action == "verify":
                items = verify_bundle(source)
                source.seek(0)
                digest = hashlib.file_digest(source, "sha256").hexdigest()
                result = {
                    "items": items,
                    "verified": True,
                    "backup_sha256": digest,
                    "local_path": str(args.bundle.resolve()),
                }
            else:
                result = restore_bundle(source, root)
        print(json.dumps(result))
        return
    if args.action == "export":
        if args.plan is None:
            parser.error("需要--plan")
        plan = json.loads(args.plan.read_text())
        # 历史包同时保存所属批次的小型解释、生产者和清单，清理时只删payload。
        names = set(plan["candidates"])
        for name in plan["candidates"]:
            batch = safe_member(root, name).parent
            names.update(
                str(p.relative_to(root)) for p in batch.iterdir() if p.is_file()
            )
        items = [file_record(root, safe_member(root, name)) for name in sorted(names)]
        export_bundle(root, items, sys.stdout.buffer)
        return
    engine = runtime_engine()
    try:
        if args.action == "plan":
            result = plan_retention(engine, root)
        else:
            if args.plan is None or args.receipt is None:
                parser.error("需要--plan和--receipt")
            result = apply_verified_plan(
                engine,
                root,
                json.loads(args.plan.read_text()),
                json.loads(args.receipt.read_text()),
            )
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
