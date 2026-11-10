"""版本物理可用性；封版身份不等于事实仍在线。"""

import sqlalchemy as sa


def ensure_online(connection, version_ids):
    """连同递归输入检查；缺登记/离线拒绝复用，不查询上游或移动当前版本。"""
    ids = sorted(set(version_ids))
    if not ids:
        return
    missing = (
        connection.execute(
            sa.text("""
      WITH RECURSIVE deps(id) AS (
        SELECT unnest(CAST(:ids AS text[])) UNION
        SELECT i.input_version_id FROM public.version_inputs i
        JOIN deps d ON d.id=i.output_version_id)
      SELECT d.id FROM deps d LEFT JOIN public.dataset_storage s ON s.version_id=d.id
      WHERE s.state IS DISTINCT FROM 'online' ORDER BY d.id
    """),
            {"ids": ids},
        )
        .scalars()
        .all()
    )
    if missing:
        raise ValueError("DATASET_RESTORE_REQUIRED")


def deferred_reason(exc):
    """只分类已知维护/恢复结果，不把上游异常笼统改成待恢复。"""
    code = getattr(exc, "code", None) or (exc.args[0] if exc.args else None)
    if code == "DATASET_RESTORE_REQUIRED":
        return code
    if code in ("SYNC_ALREADY_RUNNING", "M1_SYNC_OR_SCORING_RUNNING", "RETENTION_BUSY"):
        return "MAINTENANCE_DEFERRED"
    return None
