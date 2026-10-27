"""只解析本地PG已接收的M1资料；不连接数仓或飞书。"""

import json
import os
import time

from sqlalchemy import create_engine

from app.modules.products.ingestion import IngestionError
from app.modules.products.profiles import ProfileRepository


def main():
    started = time.monotonic()
    engine = None
    try:
        url = os.environ.get("SYNC_DATABASE_URL")
        if not url:
            raise IngestionError("MISSING_CONNECTION_CONFIGURATION")
        engine = create_engine(
            url,
            pool_size=1,
            max_overflow=0,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=60000 -c lock_timeout=5000",
            },
        )
        result = ProfileRepository(engine).run()
        print(
            json.dumps(
                dict(
                    phase="profiles_published",
                    **result,
                    elapsed_seconds=round(time.monotonic() - started, 2),
                )
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                dict(
                    phase="failed",
                    error_code=str(exc) if isinstance(exc, IngestionError) else type(exc).__name__,
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
