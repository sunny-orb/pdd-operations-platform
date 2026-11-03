"""导入已授权完整11条历史推广证据；不访问上游，不修改数仓发布版。"""

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.jobs.project_sync import run_guarded
from app.jobs.sync_shop_daily import runtime_engine
from app.modules.sales.datasets import HISTORY_SPEC
from app.modules.sales.promotion_history import normalize_history
from app.modules.sales.storage import ShopDailyStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    candidate = normalize_history(
        json.loads(args.input.read_text(), parse_float=Decimal)
    )
    if not args.publish:
        print(
            json.dumps(
                {
                    "status": "verified",
                    "rows": len(candidate.rows),
                    "fingerprint": candidate.fingerprint,
                }
            )
        )
        return 0
    engine = runtime_engine()
    try:

        def publish():
            store = ShopDailyStore(
                engine,
                archive_root=ROOT / "var/ingestion" / HISTORY_SPEC.code,
                approved=candidate.window,
                spec=HISTORY_SPEC,
            )
            print(json.dumps(asdict(store.publish_candidate(candidate))))
            return 0

        return run_guarded(engine, publish)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
