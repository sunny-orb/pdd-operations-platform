"""同步整链运行接口；触发器注入资源，不拥有本机配置、凭据或连接生命周期。"""

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from app.jobs.project_sync import run_project
from app.modules.data_status.schedule_policy import JST_AFTERSALES_STEPS, RECONCILIATION_TIME


@dataclass(frozen=True)
class ProjectPaths:
    """所有可写运行资料和外部配置均显式定位；不相对进程cwd推断路径。"""

    ingestion_root: Path
    run_archive_root: Path
    refund_targets_config: Path
    feishu_root: Path
    scoring_archive_root: Path

    def __post_init__(self):
        for name in (
            "ingestion_root",
            "run_archive_root",
            "refund_targets_config",
            "feishu_root",
            "scoring_archive_root",
        ):
            value = Path(getattr(self, name))
            if not value.is_absolute():
                raise ValueError("ABSOLUTE_SYNC_PATH_REQUIRED")
            object.__setattr__(self, name, value)


def build_steps(
    engine: Engine,
    paths: ProjectPaths,
    warehouse_password: Callable[[], str],
    *,
    product_sources=None,
    reconcile_product_regions=False,
    reconcile_sources=(),
    repair_dates=None,
    repair_only=(),
):
    """装配既有接收、校验、发布及报告；仓库口令只在对应步骤真正执行时获取。"""
    from app.connectors.product_board import ProductBoardReader
    from app.connectors.sales_feishu import FeishuRefundReader
    from app.connectors.sales_sources import SalesSourceReader
    from app.connectors.shop_daily import ShopDailyReader
    from app.modules.product_board.storage import ProductBoardStore
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.source_policy import approved_window
    from app.modules.sales.storage import ShopDailyStore

    full_sources = set(reconcile_sources)
    if reconcile_product_regions:
        full_sources.add("product_regions")
    from app.modules.data_status.schedule_policy import RECONCILIATION_SOURCES

    if not full_sources <= set(RECONCILIATION_SOURCES):
        raise ValueError("INVALID_RECONCILIATION_SOURCES")

    repair_dates = repair_dates or {}
    repair_only = set(repair_only)
    if repair_dates:
        from app.modules.data_status.schedule_policy import DAILY_GAP_SOURCES

        if not repair_dates.keys() <= DAILY_GAP_SOURCES or full_sources & repair_dates.keys():
            raise ValueError("INVALID_GAP_SOURCE")
    if not repair_only <= repair_dates.keys() or not all(repair_dates.values()):
        raise ValueError("INVALID_GAP_DATES")

    def products(selected=None):
        selection = selected if selected is not None else product_sources
        chosen = (
            set(selection)
            if selection is not None
            else {"product_daily", "product_promotion", "profile_records"}
        )
        days = {code: dates for code, dates in repair_dates.items() if code in chosen}
        reader = None
        try:

            def receive(hb):
                nonlocal reader
                reader = ProductBoardReader(password=warehouse_password(), heartbeat=hb)
                from app.modules.product_board.incremental import receive_product_board

                return receive_product_board(
                    store,
                    reader,
                    selected=selection,
                    **({"repair_days": days, "repair_only": chosen <= repair_only} if days else {}),
                    full_sources=full_sources
                    & {"product_daily", "product_promotion", "profile_records"},
                    heartbeat=hb,
                )

            store = ProductBoardStore(
                engine, paths.ingestion_root / "product_board", reconcile_full_scope=False
            )
            return (
                store.run_selected(selection, receive)
                if selection is not None
                else store.run(receive)
            )
        finally:
            if reader is not None:
                reader.close()

    def sales(code):
        reader = None
        window = approved_window(code)
        gap_options = (
            {"repair_dates": repair_dates[code], "repair_only": code in repair_only}
            if code in repair_dates
            else {}
        )
        try:

            def receive(hb):
                nonlocal reader
                reader = (
                    ShopDailyReader(password=warehouse_password(), heartbeat=hb)
                    if code == "shop_daily"
                    else FeishuRefundReader(root=paths.feishu_root, heartbeat=hb)
                    if code == "shop_refunds"
                    else SalesSourceReader(password=warehouse_password(), heartbeat=hb)
                )
                if code == "product_regions":
                    from app.modules.sales.product_regions_incremental import (
                        receive_product_regions,
                    )

                    return receive_product_regions(
                        store,
                        lambda w: reader.read_stable(code, w, allow_empty=True),
                        heartbeat=hb,
                        full=code in full_sources,
                        **gap_options,
                    )
                from app.modules.sales.window_incremental import (
                    SALES_INCREMENTAL_SOURCES,
                    receive_sales_window,
                )

                if code in SALES_INCREMENTAL_SOURCES:
                    return receive_sales_window(
                        store,
                        (lambda w: reader.read_stable(w, allow_empty=True))
                        if code == "shop_daily"
                        else (lambda w: reader.read_stable(code, w, allow_empty=True)),
                        heartbeat=hb,
                        full=code in full_sources,
                        **gap_options,
                    )
                return (
                    reader.read_stable(window)
                    if code in ("shop_daily", "shop_refunds")
                    else reader.read_stable(code, window)
                )

            store = ShopDailyStore(
                engine,
                archive_root=paths.ingestion_root / code,
                spec=None if code == "shop_daily" else SPECS[code],
                reconcile_full_scope=code == "shop_refunds"
                or (code in full_sources and code != "product_regions"),
                **({"reconciliation": code in full_sources} if code == "product_regions" else {}),
            )
            return asdict(store.run(receive))
        finally:
            if hasattr(reader, "close"):
                reader.close()

    def scores():
        from app.modules.product_scoring.publisher import publish_scores

        result = publish_scores(engine, archive_root=paths.scoring_archive_root)
        return {**result, "version_id": result["versions"]["product_scores"]}

    def aftersales(code):
        from app.connectors.aftersales import AftersalesReader
        from app.modules.aftersales.publisher import publish_source

        reader = None
        try:

            def receive(hb):
                nonlocal reader
                reader = AftersalesReader(password=warehouse_password(), heartbeat=hb)
                end = reader.latest(code)
                raw, rows = reader.read_stable(code, end)
                return end, raw, rows

            return publish_source(engine, code, receive, paths.ingestion_root / "aftersales")
        finally:
            if reader is not None:
                reader.close()

    def reports():
        from app.modules.aftersales.publisher import publish_reports

        return publish_reports(engine, paths.ingestion_root / "aftersales")

    def refund_targets():
        from app.modules.aftersales.targets import run_targets

        return run_targets(
            engine,
            paths.ingestion_root / "refund_targets",
            paths.refund_targets_config,
        )

    def sales_targets():
        from app.connectors.sales_targets import FeishuTargetReader
        from app.modules.sales.targets import SalesTargetStore

        def receive(heartbeat):
            return FeishuTargetReader(root=paths.feishu_root, heartbeat=heartbeat).read_stable(
                basis="original_gmv"
            )

        return asdict(
            SalesTargetStore(
                engine, archive_root=paths.ingestion_root / "sales_targets", basis="original_gmv"
            ).run(receive)
        )

    def weather():
        from app.jobs.sync_weather import run_weather

        return run_weather(engine, paths.ingestion_root / "weather")

    return {
        "products": products,
        **{code: lambda code=code: products((code,)) for code in (product_sources or ())},
        "product_scores": scores,
        **{code: lambda code=code: sales(code) for code in ("shop_daily", *SPECS)},
        "sales_targets": sales_targets,
        **{
            code: lambda code=code: aftersales(code)
            for code in ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales")
        },
        "refund_targets": refund_targets,
        "aftersales_report": reports,
        "weather": weather,
    }


def execute_project(
    engine: Engine,
    slot: str,
    *,
    paths: ProjectPaths,
    warehouse_password: Callable[[], str],
    only: Sequence[str] | None = None,
    max_attempts: int = 2,
    lock_connection=None,
    product_sources=None,
    reconcile_product_regions=False,
    reconcile_sources=(),
    repair_dates=None,
    repair_only=(),
    reconciliation_sources=(),
    reconciliation_time=RECONCILIATION_TIME,
    jst_aftersales_enabled=True,
):
    """同一批次标识重用PG幂等/互斥；串行跑完整业务链，返回脱敏运行报告。

    调用方持有engine生命周期并传入稳定slot；仓库凭据provider延迟至接仓时调用。
    only沿用手工子集语义：仅选中步骤参与依赖校验，不隐式扩大来源范围。
    """
    if not 1 <= max_attempts <= 3:
        raise ValueError("INVALID_SYNC_ATTEMPTS")
    if not jst_aftersales_enabled and only and set(only) & set(JST_AFTERSALES_STEPS):
        raise ValueError("JST_AFTERSALES_DISABLED")
    steps = build_steps(
        engine,
        paths,
        warehouse_password,
        **(
            {"repair_dates": repair_dates, "repair_only": repair_only}
            if repair_dates or repair_only
            else {}
        ),
        **({"reconcile_product_regions": True} if reconcile_product_regions else {}),
        **({"reconcile_sources": tuple(reconcile_sources)} if reconcile_sources else {}),
        **({"product_sources": product_sources} if product_sources is not None else {}),
    )
    if only is not None:
        if not only or not set(only) <= steps.keys():
            raise ValueError("INVALID_SYNC_DATASETS")
        steps = {name: step for name, step in steps.items() if name in only}
    if not jst_aftersales_enabled:
        steps = {name: step for name, step in steps.items() if name not in JST_AFTERSALES_STEPS}
    return run_project(
        engine,
        slot,
        steps,
        paths.run_archive_root,
        max_attempts=max_attempts,
        lock_connection=lock_connection,
        reconciliation_sources=reconciliation_sources,
        reconciliation_time=reconciliation_time,
        depends_on={
            "product_scores": tuple(
                key
                for key in ("products", "product_daily", "product_promotion", "profile_records")
                if key in steps
            ),
            "aftersales_report": tuple(
                key
                for key in (
                    "jst_returns",
                    "jst_shipped_sales",
                    "jst_cancelled_sales",
                    "refund_targets",
                )
                if key in steps
            ),
        },
    )
