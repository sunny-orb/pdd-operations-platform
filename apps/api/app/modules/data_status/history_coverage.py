"""历史记录覆盖读模型：任务计算、状态接口只读；与经营发布及指标计算隔离。"""

import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.modules.record_coverage import missing_shop_days
from app.modules.sales.datasets import HISTORY_SPEC, SPECS
from app.modules.sales.promotion_history import effective_rows, history_rows
from app.modules.sales.source_policy import SHOPS

from .catalog import BY_ID
from .schedule_policy import FEISHU_TIMES, WAREHOUSE_TIMES

RULE = "shop-day-presence-v2-neutral"
WINDOW_DAYS = 30
# 已有页面按店铺日解释缺行的来源；商品明细只核店铺日，不推断单品应交付。
DAILY = {
    "shop_daily",
    "product_daily",
    "shop_ratings",
    "shop_regions",
    "product_regions",
    "shop_refunds",
    "shop_channel_product_promotion",
    "shop_channel_star_shop",
}
SHOP_NAMES = {v[0]: v[2] for v in SHOPS.values()}
logger = logging.getLogger(__name__)


def coverage_window(code, now, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES):
    """完整30日客观记录参考；与更新时效及同步成败独立，不剔除昨天。"""
    end = now.astimezone(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    return end - timedelta(days=WINDOW_DAYS - 1), end, end


def binding(code, heads, now, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES):
    start, end, checked_end = coverage_window(code, now, warehouse_times, feishu_times)
    codes = [code, HISTORY_SPEC.code] if code == "shop_channel_product_promotion" else [code]
    return dict(
        rule=RULE,
        inputs={k: heads[k]["id"] for k in codes if k in heads},
        window_start=str(start),
        window_end=str(end),
        checked_through=str(checked_end),
    )


def history_status(
    code, saved, heads, now, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES
):
    """仅校验摘要绑定；旧缺口仍可见但明确旧结果，不用旧检查证明当前完整。"""
    source = BY_ID[code]
    if source.kind != "daily":
        return dict(state="not_applicable")
    if code not in DAILY:
        return dict(state="unverified")
    expected = binding(code, heads, now, warehouse_times, feishu_times)
    if not saved:
        return dict(**expected, state="pending" if expected["inputs"] else "unavailable")
    if saved.get("state") == "failed":
        return saved
    if any(saved.get(k) != v for k, v in expected.items()):
        return {**saved, "state": "stale"}
    return saved


def collect_coverage(
    c, code, heads, now, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES
):
    """只读当前已发布本地版本的有限日期/身份；历史补齐沿用整行优先规则。"""
    bound = binding(code, heads, now, warehouse_times, feishu_times)
    if not bound["inputs"]:
        return dict(**bound, state="unavailable", checked_at=now.isoformat())
    start, _, end = coverage_window(code, now, warehouse_times, feishu_times)
    spec = SPECS.get(code)
    shops = [SHOPS[s][0] for s in spec.shop_ids] if spec else list(SHOP_NAMES)
    rows = []
    if code in heads:
        table, day, identity = (
            ("shop_daily_facts", "business_date", "shop_id")
            if code == "shop_daily"
            else ("read_product_daily_facts", "business_date", "source_shop_id")
            if code == "product_daily"
            else (spec.fact_table, spec.date_column, "shop_id")
        )
        params = dict(v=heads[code]["id"], lo=start, hi=end)
        if code == "product_regions":
            # 与地区页保持相同排除分类，不把包材等记录当有效覆盖。
            from app.modules.sales.region_queries import PRODUCT_CTE_TEMPLATE

            prefix = PRODUCT_CTE_TEMPLATE.format(columns="shop_id,period_start")
            table = "labeled"
        else:
            prefix, table = "", "public." + table
        rows = [
            dict(r)
            for r in c.execute(
                sa.text(
                    prefix + f"SELECT DISTINCT {identity} AS shop_id,{day} AS business_date "
                    f"FROM {table} WHERE "
                    + ("" if code == "product_regions" else "dataset_version_id=:v AND ")
                    + f"{day} BETWEEN :lo AND :hi"
                ),
                params,
            ).mappings()
        ]
    if code == "product_daily":
        rows = [{**r, "shop_id": SHOPS[r["shop_id"]][0]} for r in rows if r["shop_id"] in SHOPS]
    if code == "shop_channel_product_promotion":
        rows = effective_rows(
            [{**r, "channel_code": "product_promotion"} for r in rows],
            [r for r in history_rows(c, heads) if start <= r["business_date"] <= end],
        )
    observed = {(r["shop_id"], r["business_date"]) for r in rows}
    gaps = missing_shop_days(observed, start, end, shops)
    groups = [
        dict(
            shop_id=shop,
            shop_name=SHOP_NAMES[shop],
            dates=[str(day) for sid, day in gaps if sid == shop],
        )
        for shop in shops
    ]
    return dict(
        **bound,
        state="gaps" if gaps else "observed",
        checked_at=now.isoformat(),
        missing_count=len(gaps),
        missing_date_count=len({d for _, d in gaps}),
        groups=[g for g in groups if g["dates"]],
    )


def _refresh_history_coverage(
    c, *, now=None, warehouse_times=WAREHOUSE_TIMES, feishu_times=FEISHU_TIMES, source_ids=None
):
    """后台独立短事务；单来源失败保旧、不改变上游/同步状态。无上游访问。"""
    from .repository import versions

    now = now or datetime.now(UTC)
    # 仅在独立任务连接调用；已有监测锁串行化写入，不与发布事务合并。
    with c.begin():
        c.execute(sa.text("SET LOCAL statement_timeout = '3s'"))
        heads = versions(c)
        saved = dict(
            c.execute(sa.text("SELECT source_id,platform_snapshot FROM source_checkpoints")).all()
        )
    scope = DAILY if source_ids is None else DAILY & set(source_ids)
    for code in sorted(scope & saved.keys()):
        prior = saved[code].get("history")
        expected = binding(code, heads, now, warehouse_times, feishu_times)
        if (
            prior
            and prior.get("state") != "failed"
            and all(prior.get(k) == v for k, v in expected.items())
        ):
            continue
        try:
            with c.begin():
                c.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
                summary = collect_coverage(c, code, heads, now, warehouse_times, feishu_times)
        except Exception:
            logger.warning("history_coverage_failed source=%s", code)
            summary = {**(prior or expected), "state": "failed", "attempted_at": now.isoformat()}
        try:
            with c.begin():
                c.execute(sa.text("SET LOCAL statement_timeout = '3s'"))
                c.execute(
                    sa.text(
                        "UPDATE source_checkpoints SET platform_snapshot = jsonb_set("
                        "platform_snapshot, '{history}', CAST(:summary AS jsonb)) "
                        "WHERE source_id=:code"
                    ),
                    {"code": code, "summary": json.dumps(summary)},
                )
        except Exception:
            # 写入失败无法安全更新失败标记；GET依据旧版本/窗口绑定显示待重新检查。
            logger.warning("history_coverage_save_failed source=%s", code)


def refresh_history_coverage(c, **kwargs):
    """附属检查整体可降级；元信息读取/保存失败也不能向经营同步传播。"""
    try:
        _refresh_history_coverage(c, **kwargs)
    except Exception:
        logger.warning("history_coverage_unavailable")
