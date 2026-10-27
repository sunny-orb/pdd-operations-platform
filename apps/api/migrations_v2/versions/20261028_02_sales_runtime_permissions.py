"""M3首链运行权限；不增加表，不授予DDL/删除或其他业务域写入。

回退前停止首链接收任务，只撤本revision授予的权限，保留事实与历史版本。
20261028_01时pdd_ingest对v2无权限；若现场已有非基线授权须先独立审查。
"""

from alembic import op

revision = "20261028_02"
down_revision = "20261028_01"
branch_labels = None
depends_on = None

TABLES = (
    "shops",
    "shop_daily_facts",
    "dataset_versions",
    "coverage_records",
    "task_runs",
    "current_datasets",
)


def upgrade():
    for name in TABLES:
        op.execute(f"GRANT SELECT, INSERT ON public.{name} TO pdd_ingest")
    for name, columns in {
        "dataset_versions": (
            "state,content_fingerprint,retained_input_ref,received_at,validation_evidence_ref"
        ),
        "task_runs": (
            "status,started_at,finished_at,worker_ref,lease_expires_at,"
            "result_version_id,source_updated_text,source_update_observed_at,"
            "error_code,error_summary,evidence_ref"
        ),
        "current_datasets": "version_id",
    }.items():
        op.execute(f"GRANT UPDATE ({columns}) ON public.{name} TO pdd_ingest")


def downgrade():
    for name in TABLES:
        op.execute(f"REVOKE SELECT, INSERT ON public.{name} FROM pdd_ingest")
    for name, columns in {
        "dataset_versions": (
            "state,content_fingerprint,retained_input_ref,received_at,validation_evidence_ref"
        ),
        "task_runs": (
            "status,started_at,finished_at,worker_ref,lease_expires_at,"
            "result_version_id,source_updated_text,source_update_observed_at,"
            "error_code,error_summary,evidence_ref"
        ),
        "current_datasets": "version_id",
    }.items():
        op.execute(f"REVOKE UPDATE ({columns}) ON public.{name} FROM pdd_ingest")
