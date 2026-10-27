"""M3其他已核来源手工接收入口；参数/错误由独立job处理。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.jobs.sync_sales_sources import main

if __name__ == "__main__":
    sys.exit(main())
