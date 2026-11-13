"""容器内生产复用策略入口；默认只读，显式启停只修改本项目策略文件。"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

import sqlalchemy as sa
from app.modules.deployment_storage import configure, require_endpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="preflight",
        choices=("preflight", "enable", "disable"),
    )
    args = parser.parse_args()
    engine = None
    try:
        value = os.environ.get("SYNC_DATABASE_URL", "")
        if not value:
            raise ValueError("STORAGE_CONNECTION_REQUIRED")
        # 在连接前拒绝错误目标；不回退到本地或API账号配置。
        engine = sa.create_engine(
            value, hide_parameters=True, connect_args={"connect_timeout": 5}
        )
        require_endpoint(engine)
        print(json.dumps(configure(engine, args.action), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — 不回显DBAPI语句、URL或凭据。
        print(
            "启用检查未完成："
            + (str(exc) if type(exc) is ValueError else type(exc).__name__),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
