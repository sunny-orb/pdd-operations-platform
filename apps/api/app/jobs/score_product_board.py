"""在既有M1输入成功发布后生成并发布试行评分，不连接上游或启动调度。"""

import json

from app.jobs.sync_shop_daily import runtime_engine
from app.modules.product_scoring.publisher import publish_scores


def main():
    engine = runtime_engine()
    try:
        print(json.dumps(publish_scores(engine), ensure_ascii=False))
        return 0
    except Exception as exc:
        # 数据库约束错误可能附带业务原行，任务日志仅回报稳定阶段及错误类型。
        print(
            json.dumps(dict(status="failed", stage="score_product_board", error=type(exc).__name__))
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
