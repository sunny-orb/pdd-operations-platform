"""在开发库固定已发布来源重建商品资料关系与相容试行评分；不访问上游。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", required=True)
    parser.parse_args()
    sys.path.insert(0, str(ROOT / "apps/api"))
    from app.jobs.project_sync import run_guarded
    from app.jobs.sync_shop_daily import runtime_engine
    from app.modules.product_board.storage import ProductBoardStore
    from app.modules.product_scoring.publisher import publish_scores

    engine = runtime_engine()

    def execute():
        profiles = ProductBoardStore(
            engine, ROOT / "var/ingestion/product_board"
        ).rebuild_profiles()
        scores = publish_scores(engine, archive_root=ROOT / "var/scoring/product_board")
        print(json.dumps(dict(profiles=profiles, scores=scores), ensure_ascii=False))
        return 0

    try:
        return run_guarded(engine, execute)
    except Exception as exc:
        print(json.dumps(dict(status="failed", error=type(exc).__name__)))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
