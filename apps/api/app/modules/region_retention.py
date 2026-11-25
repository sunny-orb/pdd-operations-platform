"""仅商品地区无人使用版本的有界回收；事务和资源调度由调用方负责。"""

from dataclasses import dataclass
from math import isfinite

import sqlalchemy as sa


@dataclass(frozen=True)
class Policy:
    batch_rows: int = 1000
    statement_timeout_ms: int = 1500
    lock_timeout_ms: int = 100
    max_seconds: float = 10

    def __post_init__(self):
        for value, maximum in (
            (self.batch_rows, 1000),
            (self.statement_timeout_ms, 1500),
            (self.lock_timeout_ms, 100),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("REGION_POLICY")
        if (
            type(self.max_seconds) not in (int, float)
            or not isfinite(self.max_seconds)
            or not 0 < self.max_seconds <= 10
        ):
            raise ValueError("REGION_POLICY")


def inspect(connection):
    """只读查看具备真实退出事件的候选；无共享替代版或未通过保护条件不列入。"""
    candidates = (
        connection.execute(
            sa.text("""
      SELECT v.id FROM public.dataset_versions v
      JOIN public.current_datasets c ON c.dataset_code='product_regions'
      JOIN public.dataset_versions r ON r.id=c.version_id AND r.state='published'
        AND r.row_schema_revision=v.row_schema_revision
      JOIN public.fact_content_manifests m ON m.version_id=r.id
        AND m.table_name='product_region_records'
      WHERE v.dataset_code='product_regions' AND public.pdd_region_eligible(v.id)
      ORDER BY v.id
    """)
        )
        .scalars()
        .all()
    )
    return {"candidates": candidates}


def step(connection, policy=None):
    """完成至多一候选的一小批；争锁/运行任务延期，错误交给外层回滚。"""
    policy = policy or Policy()
    connection.execute(
        sa.text("SELECT set_config('statement_timeout',:v,true)"),
        {"v": str(policy.statement_timeout_ms)},
    )
    connection.execute(
        sa.text("SELECT set_config('lock_timeout',:v,true)"), {"v": str(policy.lock_timeout_ms)}
    )
    result = connection.scalar(
        sa.text("SELECT public.pdd_region_retire_step(:n)"), {"n": policy.batch_rows}
    )
    if result["status"] == "idle":
        result["deleted_rows"] = connection.scalar(
            sa.text("SELECT public.pdd_region_gc_block(:n)"), {"n": policy.batch_rows}
        )
    return {"version_id": None, "cursor": None, **result}


def rebuild_version(
    connection, version_id, rows, *, content_fingerprint, row_schema_revision, heartbeat=None
):
    """完整候选恢复原地区版本；身份、全部内容及行数一致才online，失败须回滚事务。

    已online时不重复写；offline继续沿原归档恢复，其他域不能进入这条路径。
    保留原version_id/归档/coverage，允许无消费者的旧随机事实ID变为共享派生ID。
    """
    from app.modules.database_residency import ensure_online
    from app.modules.fact_content import write_version

    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock_shared(hashtext('pdd-database-maintenance'))")
    )
    connection.execute(
        sa.text("SELECT id FROM public.dataset_versions WHERE id=:v FOR UPDATE"), {"v": version_id}
    ).one()
    # 维护共享锁排斥回收，版本行锁串行化同版本恢复；同步账号只需读取存储状态。
    state = connection.scalar(
        sa.text("SELECT state FROM public.dataset_storage WHERE version_id=:v"),
        {"v": version_id},
    )
    if state == "online":
        ensure_online(connection, [version_id])
        return False
    if state not in ("reclaiming", "reclaimed"):
        raise ValueError("DATASET_RESTORE_REQUIRED")
    connection.execute(
        sa.text("SELECT public.pdd_region_rebuild_begin(:v,:f,:r)"),
        {"v": version_id, "f": content_fingerprint, "r": row_schema_revision},
    )
    write_version(connection, "product_region_records", version_id, rows, heartbeat=heartbeat)
    connection.execute(sa.text("SELECT public.pdd_region_rebuild_finish(:v)"), {"v": version_id})
    ensure_online(connection, [version_id])
    return True
