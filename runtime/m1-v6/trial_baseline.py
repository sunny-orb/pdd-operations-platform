"""离线入口复用正式领域纯函数；算法唯一维护在product_scoring.model。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api"))

from app.modules.product_scoring.model import build_calibration, score_observation  # noqa: F401
