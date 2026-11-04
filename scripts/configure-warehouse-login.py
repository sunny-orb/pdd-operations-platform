"""配置已有数仓网页登录：密码不回显，8个来源元数据验证通过才保存私密文件。"""

import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.connectors.warehouse_metadata import WarehouseMetadataReader
from app.connectors.warehouse_session import WarehouseAccountSession, load_account_file
from app.modules.data_status.schedule_policy import WAREHOUSE_SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="只验证已保存身份，不修改配置"
    )
    args = parser.parse_args()
    path = ROOT / "var/local-db/warehouse-web-account.json"
    try:
        if args.check:
            credentials = load_account_file(path)
        else:
            if not sys.stdin.isatty():
                raise ValueError("INTERACTIVE_TERMINAL_REQUIRED")
            print(
                "使用现有网页登录账号。验证通过后仅保存到本项目0600私密文件，不启用调度。"
            )
            credentials = {
                "username": input("网页登录账号：").strip(),
                "password": getpass.getpass("网页登录密码（不回显）："),
            }
        reader = WarehouseMetadataReader(
            session=WarehouseAccountSession(lambda: credentials)
        )
        results = [{"source": code, **reader.read(code)} for code in WAREHOUSE_SOURCES]
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".warehouse-account-", dir=path.parent
            )
            try:
                with os.fdopen(descriptor, "w") as output:
                    json.dump(credentials, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        print(
            json.dumps({"status": "verified", "sources": results}, ensure_ascii=False)
        )
        return 0
    except Exception:  # noqa: BLE001 - 交互入口不输出含凭据的异常上下文
        print(
            "验证未通过，未保存新凭据。请确认网页登录账号；验证码、短信或改密须人工完成。"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
