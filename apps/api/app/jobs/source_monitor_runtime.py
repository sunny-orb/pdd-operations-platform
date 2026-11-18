"""监测与既有项目执行装配；只重跑变化来源所属一致性组及其派生步骤。"""

from uuid import uuid4

import sqlalchemy as sa

from app.connectors.aftersales_rpa import receive_stable
from app.connectors.aftersales_rpa_status import configured_connection
from app.connectors.source_probes import SourceProbes
from app.jobs.project_runtime import execute_project
from app.jobs.source_monitor import run_monitor
from app.modules.aftersales.rpa_publisher import sync_rpa
from app.modules.data_status.failure_codes import safe_failure_code
from app.modules.data_status.schedule_policy import (
    JST_AFTERSALES_SOURCES,
    JST_AFTERSALES_STEPS,
    RECONCILIATION_RETRY_SECONDS,
    RECONCILIATION_TIME,
)

GROUP_STEPS = {
    "products": ("products", "product_scores"),
    "aftersales": (
        "jst_returns",
        "jst_shipped_sales",
        "jst_cancelled_sales",
        "refund_targets",
        "aftersales_report",
    ),
}


def selected_steps(groups):
    return tuple(
        dict.fromkeys(step for group in groups for step in GROUP_STEPS.get(group, (group,)))
    )


def pending_processing(c):
    """已有相容输入但结果缺失或落后时重算；没有输入时不制造失败任务。"""
    heads = dict(c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all())
    pending = []
    rpa_active = c.scalar(
        sa.text("""SELECT EXISTS (
        SELECT 1 FROM current_datasets d JOIN version_inputs i ON i.output_version_id=d.version_id
        JOIN dataset_versions v ON v.id=i.input_version_id
        WHERE d.dataset_code='aftersales_report' AND v.dataset_code='rpa_returns')""")
    )
    for output, required, step in (
        (
            "product_scores",
            ("product_daily", "product_promotion", "profile_links"),
            "product_scores",
        ),
        (
            "aftersales_report",
            ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales"),
            "aftersales_report",
        ),
    ):
        # RPA本地独立入口原子发布三角色；JST监测不能拿自己的日明细重算RPA期间报表。
        if output == "aftersales_report" and rpa_active:
            continue
        if not all(code in heads for code in required):
            continue
        inputs = dict(
            c.execute(
                sa.text("""
            SELECT v.dataset_code,i.input_version_id FROM version_inputs i
            JOIN dataset_versions v ON v.id=i.input_version_id WHERE i.output_version_id=:id
        """),
                {"id": heads.get(output)},
            ).all()
        )
        relevant = (
            (*required, "refund_targets")
            if output == "aftersales_report" and ("refund_targets" in heads)
            else required
        )
        if any(inputs.get(code) != heads[code] for code in relevant) or any(
            heads.get(code) != vid for code, vid in inputs.items()
        ):
            pending.append(step)
    return pending


def execute_monitor(
    engine,
    *,
    paths,
    warehouse_password,
    interval_seconds=900,
    source_ids=None,
    reconciliation_sources=(),
    reconciliation_time=RECONCILIATION_TIME,
    jst_aftersales_enabled=True,
    rpa_connect=None,
):
    receive_rpa = not jst_aftersales_enabled and (
        source_ids is None or "aftersales_rpa" in source_ids
    )
    if rpa_connect is None:

        def rpa_connect():
            return configured_connection(paths.feishu_root)

    probes = SourceProbes(warehouse_password, paths.feishu_root, rpa_connect=rpa_connect)

    def synchronize(groups, connection):
        rpa_state = {}
        if receive_rpa and "aftersales_rpa" in groups:
            groups = tuple(group for group in groups if group != "aftersales_rpa")
            try:
                sync_rpa(
                    engine,
                    lambda: receive_stable(rpa_connect),
                    paths.ingestion_root / "aftersales_rpa",
                )
                rpa_state["aftersales_rpa"] = True
            except Exception as exc:
                rpa_state = {
                    "aftersales_rpa": False,
                    "_errors": {"aftersales_rpa": safe_failure_code(str(exc))},
                }
        with connection.begin():
            pending = pending_processing(connection)
        if source_ids is not None:
            pending = [step for step in pending if step == "aftersales_report"]
        if not jst_aftersales_enabled:
            pending = [step for step in pending if step not in JST_AFTERSALES_STEPS]
        steps = tuple(dict.fromkeys((*selected_steps(groups), *pending)))
        if not steps:
            return rpa_state
        result = execute_project(
            engine,
            "monitor:" + str(uuid4()),
            paths=paths,
            warehouse_password=warehouse_password,
            only=steps,
            lock_connection=connection,
            jst_aftersales_enabled=jst_aftersales_enabled,
        )
        outputs = result.get("datasets", {})
        states = {
            group: all(
                outputs.get(step, {}).get("status") == "succeeded"
                for step in GROUP_STEPS.get(group, (group,))
                if step not in ("product_scores", "aftersales_report", "refund_targets")
            )
            for group in groups
        }
        states["_errors"] = {
            group: next(
                (
                    safe_failure_code(outputs.get(step, {}).get("error_code"))
                    for step in GROUP_STEPS.get(group, (group,))
                    if step not in ("product_scores", "aftersales_report", "refund_targets")
                    and outputs.get(step, {}).get("status") != "succeeded"
                ),
                "SYNC_FAILED",
            )
            for group in groups
            if not states[group]
        }
        if rpa_state:
            states["aftersales_rpa"] = rpa_state["aftersales_rpa"]
            states["_errors"].update(rpa_state.get("_errors", {}))
        states["_deferred"] = {
            group: next(
                outputs.get(step, {}).get("error_code")
                for step in GROUP_STEPS.get(group, (group,))
                if outputs.get(step, {}).get("error_code")
                in ("DATASET_RESTORE_REQUIRED", "MAINTENANCE_DEFERRED")
            )
            for group in groups
            if any(
                outputs.get(step, {}).get("error_code")
                in ("DATASET_RESTORE_REQUIRED", "MAINTENANCE_DEFERRED")
                for step in GROUP_STEPS.get(group, (group,))
            )
        }
        states["_processing_failed"] = any(
            outputs.get(step, {}).get("status") != "succeeded"
            for step in steps
            if step in ("product_scores", "aftersales_report", "refund_targets")
        )
        return states

    try:
        return run_monitor(
            engine,
            {
                k: v
                for k, v in probes.functions().items()
                if (source_ids is None or k in source_ids)
                and (jst_aftersales_enabled or k not in JST_AFTERSALES_SOURCES)
            },
            synchronize,
            interval_seconds=interval_seconds,
            reconciliation_sources=reconciliation_sources,
            reconciliation_time=reconciliation_time,
            receiving_sources=("aftersales_rpa",) if receive_rpa else (),
        )
    finally:
        probes.close()


def execute_source_schedule(
    engine,
    *,
    paths,
    warehouse_password,
    warehouse_headers=None,
    warehouse_session=None,
    warehouse_reader=None,
    now=None,
    warehouse_times=None,
    feishu_times=None,
    reconciliation_time=RECONCILIATION_TIME,
    reconciliation_retry_seconds=RECONCILIATION_RETRY_SECONDS,
    source_ids=None,
    force=False,
    process_products=True,
):
    """外部调度复用入口；所有路径/认证/时刻显式注入，不在API进程创建定时器。"""
    from app.connectors.warehouse_metadata import (
        WarehouseMetadataReader,
        configured_warehouse_reader,
    )
    from app.jobs.source_schedule import run_scheduled_sources
    from app.modules.data_status.schedule_policy import (
        FEISHU_SOURCES,
        FEISHU_TIMES,
        WAREHOUSE_SOURCES,
        WAREHOUSE_TIMES,
    )
    from app.modules.product_board.source import TABLES

    metadata = warehouse_reader or (
        WarehouseMetadataReader(warehouse_headers, session=warehouse_session)
        if warehouse_headers is not None or warehouse_session is not None
        else configured_warehouse_reader(paths.feishu_root)
    )
    feishu = SourceProbes(warehouse_password, paths.feishu_root)
    probes = {code: lambda code=code: metadata.read(code) for code in WAREHOUSE_SOURCES}
    probes["product_regions"] = lambda: feishu.read("product_regions")
    probes.update({code: lambda code=code: feishu.read(code) for code in FEISHU_SOURCES})

    if source_ids is not None:
        if not set(source_ids) <= probes.keys():
            raise ValueError("INVALID_SOURCE_SELECTION")
        probes = {code: probe for code, probe in probes.items() if code in source_ids}

    def synchronize(sources, connection):
        products = tuple(code for code in sources if code in TABLES)
        with connection.begin():
            pending = (
                [step for step in pending_processing(connection) if step == "product_scores"]
                if not force
                else []
            )
            heads = set(
                connection.execute(sa.text("SELECT dataset_code FROM current_datasets")).scalars()
            )
            # 调度已在同一项目锁内登记本轮周任务；普通补跑不消费周对账状态。
            running_reconciliations = (
                set(
                    connection.execute(
                        sa.text(
                            "SELECT source_id FROM source_reconciliations WHERE status='running'"
                        )
                    ).scalars()
                )
                if not force
                else set()
            )
            reconcile_sources = tuple(code for code in sources if code in running_reconciliations)
        bootstrap = bool(products) and not set(TABLES) <= heads
        product_steps = ("products",) if bootstrap else products
        steps = tuple(
            dict.fromkeys(
                (
                    *product_steps,
                    *(("product_scores",) if products and process_products else ()),
                    *(code for code in sources if code not in TABLES),
                    *pending,
                )
            )
        )
        if not steps:
            return {}
        result = execute_project(
            engine,
            "source-slot:" + str(uuid4()),
            paths=paths,
            warehouse_password=warehouse_password,
            only=steps,
            lock_connection=connection,
            product_sources=products or None,
            **({"reconcile_sources": reconcile_sources} if reconcile_sources else {}),
        )
        outputs = result.get("datasets", {})
        states = {
            code: outputs.get("products" if bootstrap and code in TABLES else code, {}).get(
                "status"
            )
            == "succeeded"
            for code in sources
        }
        states["_errors"] = {
            code: safe_failure_code(
                outputs.get("products" if bootstrap and code in TABLES else code, {}).get(
                    "error_code"
                )
            )
            for code in sources
            if not states[code]
        }
        states["_deferred"] = {
            code: outputs.get("products" if bootstrap and code in TABLES else code, {}).get(
                "error_code"
            )
            for code in sources
            if outputs.get("products" if bootstrap and code in TABLES else code, {}).get(
                "error_code"
            )
            in ("DATASET_RESTORE_REQUIRED", "MAINTENANCE_DEFERRED")
        }
        states["_processing_failed"] = any(
            outputs.get(step, {}).get("status") != "succeeded"
            for step in steps
            if step in ("product_scores", "aftersales_report")
        )
        return states

    try:
        return run_scheduled_sources(
            engine,
            probes,
            synchronize,
            now=now,
            force=force,
            warehouse_times=warehouse_times or WAREHOUSE_TIMES,
            feishu_times=feishu_times or FEISHU_TIMES,
            reconciliation_time=reconciliation_time,
            reconciliation_retry_seconds=reconciliation_retry_seconds,
        )
    finally:
        feishu.close()
