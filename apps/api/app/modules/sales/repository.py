"""经营查询只读仓储：同一RR快照固定入口、事实、覆盖及尝试状态。"""

import hashlib
import json
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.promotion import metrics as promotion_metrics

from .contracts import SalesError
from .datasets import READ_SPECS


@contextmanager
def read_snapshot(engine, version=None):
    if engine is None:
        raise SalesError("DATABASE_UNAVAILABLE", 503)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            current = (
                c.execute(
                    sa.text(
                        "SELECT v.* FROM public.current_datasets d JOIN "
                        "public.dataset_versions v ON v.id=d.version_id WHERE "
                        "d.dataset_code='shop_daily' AND v.state='published'"
                    )
                )
                .mappings()
                .first()
            )
            if not current:
                raise SalesError("SALES_NOT_CONNECTED", 503)
            versions = dict(
                c.execute(
                    sa.text(
                        "SELECT d.dataset_code,d.version_id FROM public.current_datasets d JOIN"
                        " public.dataset_versions v ON v.id=d.version_id WHERE "
                        "v.state='published' AND d.dataset_code = ANY(:codes)"
                    ),
                    {"codes": ["shop_daily", *READ_SPECS, "sales_targets"]},
                ).all()
            )
            token = hashlib.sha256(
                json.dumps(
                    {"inputs": versions, "promotion_rule": promotion_metrics.definition_revision()},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if version is not None and token != version:
                raise SalesError("DATA_VERSION_CHANGED", 409)
            facts = (
                c.execute(
                    sa.text(
                        "SELECT * FROM public.shop_daily_facts WHERE dataset_version_id=:v "
                        "ORDER BY business_date,shop_id,source_row_key"
                    ),
                    dict(v=current["id"]),
                )
                .mappings()
                .all()
            )
            window = c.execute(
                sa.text(
                    "SELECT MIN(window_start),MAX(window_end) FROM public.coverage_records "
                    "WHERE dataset_version_id=:v"
                ),
                dict(v=current["id"]),
            ).one()
            attempt = (
                c.execute(
                    sa.text(
                        "SELECT status,queued_at,started_at,finished_at,error_code FROM "
                        "public.task_runs WHERE job_code='sync_shop_daily' ORDER BY queued_at "
                        "DESC,id DESC LIMIT 1"
                    )
                )
                .mappings()
                .first()
            )
            success = (
                c.execute(
                    sa.text(
                        "SELECT finished_at,source_updated_text FROM public.task_runs WHERE "
                        "result_version_id=:v AND status='succeeded' ORDER BY finished_at DESC,"
                        "id DESC LIMIT 1"
                    ),
                    dict(v=current["id"]),
                )
                .mappings()
                .first()
            )
            yield dict(
                connection=c,
                versions=versions,
                token=token,
                version=current,
                rows=facts,
                window=window,
                attempt=attempt,
                success=success,
            )
    except SQLAlchemyError as exc:
        raise SalesError("DATABASE_UNAVAILABLE", 503) from exc
