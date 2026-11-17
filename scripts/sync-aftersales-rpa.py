"""只读接收公司RPA报表到本地现有事实/版本表；不迁移、不部署、不配置定时。"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import psycopg
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.connectors.aftersales_rpa import connect_readonly, receive_stable
from app.jobs.sync_shop_daily import runtime_engine
from app.modules.aftersales.rpa_publisher import sync_rpa


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials-helper",
        type=Path,
        help="可选的现有私有凭据加载器，须提供credentials()；不复制或输出口令",
    )
    args = parser.parse_args()
    engine = runtime_engine()
    try:
        if (
            engine.url.host not in ("localhost", "127.0.0.1")
            or engine.url.database != "pdd_operations"
        ):
            raise ValueError("RPA_LOCAL_TARGET_REQUIRED")
        if args.credentials_helper:
            helper_path = args.credentials_helper.resolve(strict=True)
            spec = importlib.util.spec_from_file_location(
                "private_rpa_credentials", helper_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            credentials = module.credentials()
            if credentials.get("dbname") != "pdd_dashboard":
                raise ValueError("RPA_SOURCE_DATABASE")

            def connect():
                return connect_readonly(**credentials)
        else:
            source_url = os.environ.get("RPA_DATABASE_URL")
            if not source_url:
                raise ValueError("RPA_CREDENTIALS_REQUIRED")

            def connect():
                return connect_readonly(source_url)

        result = sync_rpa(
            engine,
            lambda: receive_stable(connect),
            ROOT / "var/ingestion/aftersales_rpa",
        )
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, psycopg.Error, SQLAlchemyError) as exc:
        # psycopg连接异常可能含地址/用户，不将完整异常或URL输出到共享日志。
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "code": str(exc)
                    if isinstance(exc, ValueError)
                    else "RPA_SYNC_FAILED",
                }
            )
        )
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
