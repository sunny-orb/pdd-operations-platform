"""数仓专用浏览器登录/验证；仅读取固定表详情，不启用计划、不接收经营数据。"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.connectors.warehouse_page import WarehousePageReader, page_url, parse_page
from app.modules.data_status.schedule_policy import WAREHOUSE_SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--login", action="store_true", help="打开专用窗口，由用户完成飞书登录"
    )
    mode.add_argument(
        "--check", action="store_true", help="无界面复用登录，逐表读取网页更新时间"
    )
    args = parser.parse_args()
    try:
        with WarehousePageReader(
            ROOT / "var/local-db/warehouse-browser/state.json"
        ) as reader:
            if args.login:
                reader.start(login=True)
                reader.page.goto(page_url("shop_daily"), wait_until="domcontentloaded")
                print(
                    "已打开数仓专用浏览器，请完成飞书登录；成功进入表详情后自动保存并关闭。",
                    flush=True,
                )
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    try:
                        parse_page(
                            "shop_daily",
                            reader.page.url,
                            reader.page.locator("body").inner_text(),
                        )
                    except Exception:  # noqa: BLE001 - 等待人工登录，不输出浏览器错误中的身份信息
                        time.sleep(1)
                        continue
                    reader.save_state()
                    print(
                        json.dumps({"status": "login_saved"}, ensure_ascii=False),
                        flush=True,
                    )
                    return 0
                raise ValueError("LOGIN_TIMEOUT")
            started = time.monotonic()
            rows = [{"source": code, **reader.read(code)} for code in WAREHOUSE_SOURCES]
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "sources": rows,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI只输出固定错误码
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": getattr(exc, "code", "WAREHOUSE_WEB_FAILED"),
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
