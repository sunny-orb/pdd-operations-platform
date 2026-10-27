"""手工导入已核四线或重算已入库M1新品；不连数仓、不自动调度。"""

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine

from app.modules.products.ingestion import IngestionError
from app.modules.products.query import QueryError
from app.modules.products.score_repository import ScoreRepository
from app.modules.products.score_standards import display_parameters, import_model


def main():
    parser = argparse.ArgumentParser(description="本地PG新品评分；可先导入经核对的固定显示四线")
    parser.add_argument("--standards-file", type=Path)
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args()
    started = time.monotonic()
    engine = None
    try:
        url = os.environ.get("SYNC_DATABASE_URL")
        if not url:
            raise IngestionError("MISSING_CONNECTION_CONFIGURATION")
        engine = create_engine(
            url,
            pool_size=2,
            max_overflow=0,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=60000 -c lock_timeout=5000",
            },
        )
        if args.standards_file:
            # 参数文件不能携带旧评分/原始经营快照；仅接受严格限定的标准结构。
            document = json.loads(args.standards_file.read_text(encoding="utf-8"))
            if set(document) != {"families", "products", "provenance"}:
                raise IngestionError("INVALID_STANDARD_DOCUMENT")
            result = import_model(engine, display_parameters(document), document["provenance"])
            print(json.dumps(dict(phase="model_imported", **result)))
        if args.import_only and not args.standards_file:
            raise IngestionError("STANDARDS_FILE_REQUIRED")
        if not args.import_only:
            result = ScoreRepository(engine).run(args.as_of)
            print(
                json.dumps(
                    dict(
                        phase="scores_finished",
                        **result,
                        elapsed_seconds=round(time.monotonic() - started, 2),
                    )
                )
            )
            if result["status"] == "stale":
                raise SystemExit(2)
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    phase="failed",
                    error_code=str(exc)
                    if isinstance(exc, IngestionError)
                    else (exc.code if isinstance(exc, QueryError) else type(exc).__name__),
                    credentials_logged=False,
                )
            )
        )
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
