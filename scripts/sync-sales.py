"""M3独立入口；从项目根目录执行，隐藏输入数仓密码。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.jobs.sync_shop_daily import main

if __name__ == "__main__":
    sys.exit(main())
