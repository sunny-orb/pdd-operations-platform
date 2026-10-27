"""手工执行一次M1受限接入；无定时调度，密码只在运行内存中。"""

import getpass
import json
import os
import time

from sqlalchemy import create_engine

from app.connectors.warehouse import WarehouseReader
from app.modules.products.ingestion import IngestionError, initial_windows
from app.modules.products.ingestion_repository import IngestionRepository
from app.modules.products.profiles import ProfileRepository


def main():
    """验证三来源后运行一次事务任务；仅输出范围/计数/安全错误代码。"""
    reader = None
    engine = None
    started = time.monotonic()
    try:
        target = os.environ.get("SYNC_DATABASE_URL")
        host = os.environ.get("WAREHOUSE_HOST")
        if not target or not host:
            raise IngestionError("MISSING_CONNECTION_CONFIGURATION")
        password = os.environ.get("WAREHOUSE_PASSWORD") or getpass.getpass(
            "数仓只读密码（不保存）："
        )
        reader = WarehouseReader(
            host=host,
            port=int(os.environ.get("WAREHOUSE_PORT", "9030")),
            user=os.environ.get("WAREHOUSE_USER", "ro"),
            password=password,
        )
        del password
        schema = reader.validate_schema()
        windows = initial_windows(reader.latest_observed_day())
        print(
            json.dumps(
                {
                    "phase": "validated_connection",
                    "windows": [[str(a), str(b)] for a, b in windows],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        engine = create_engine(
            target,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=60000 -c lock_timeout=5000",
            },
        )
        result = IngestionRepository(engine).run(
            lambda: reader.read_stable(windows), windows, source_schema=schema
        )
        result["raw_publication_id"] = result["publication_id"]
        result["profile_resolution"] = ProfileRepository(engine).run()
        result["publication_id"] = result["profile_resolution"]["publication_id"]
        result.update(
            phase="finished",
            source_queries=reader.query_count,
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as error:
        code = str(error) if isinstance(error, IngestionError) else type(error).__name__
        print(
            json.dumps(
                {"phase": "failed", "error_code": code, "credentials_logged": False},
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise SystemExit(1) from None
    finally:
        if reader is not None:
            reader.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
