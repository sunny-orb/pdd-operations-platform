"""M2独立RR快照；商品固定相容四头，店铺仅绑定两渠道。"""

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.product_board.repository import BoardError, source_sync_status
from app.modules.product_board.repository import Repository as ProductRepository

PRODUCT_CODES = ("product_daily", "product_promotion", "profile_records", "profile_links")
STORE_CODES = ("shop_channel_product_promotion", "shop_channel_star_shop")


def definition_revision():
    return (
        "m2-v1:"
        + hashlib.sha256(
            b"".join(
                Path(__file__).with_name(name).read_bytes()
                for name in ("repository.py", "query.py", "diagnosis.py")
            )
        ).hexdigest()
    )


@contextmanager
def snapshot(engine, mode, token=None):
    """同快照读版本、事实与资料；token过期409，缺失来源503，无写入。"""
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    codes = PRODUCT_CODES if mode == "product" else STORE_CODES
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            versions = {
                r["dataset_code"]: dict(r)
                for r in c.execute(
                    sa.text(
                        "SELECT v.* FROM public.current_datasets d JOIN public.dataset_versions v "
                        "ON v.id=d.version_id WHERE v.state='published' "
                        "AND d.dataset_code=ANY(:codes)"
                    ),
                    dict(codes=list(codes)),
                ).mappings()
            }
            if not versions or (mode == "product" and any(x not in versions for x in codes)):
                raise BoardError("PROMOTION_NOT_CONNECTED", 503)
            vector = hashlib.sha256(
                json.dumps(
                    {
                        "mode": mode,
                        "definition": definition_revision(),
                        **{k: v["id"] for k, v in versions.items()},
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if token is not None and token != vector:
                raise BoardError("DATA_VERSION_CHANGED", 409)
            if mode == "product":
                inputs = dict(
                    c.execute(
                        sa.text(
                            "SELECT input_role,input_version_id FROM public.version_inputs "
                            "WHERE output_version_id=:v"
                        ),
                        dict(v=versions["profile_links"]["id"]),
                    ).all()
                )
                if any(
                    inputs.get(role) != versions[code]["id"]
                    for role, code in (
                        ("profile_records", "profile_records"),
                        ("product_current", "product_daily"),
                        ("ad_current", "product_promotion"),
                    )
                ):
                    raise BoardError("INCOMPATIBLE_INPUTS", 503)
            repo = Repository(c, mode, versions, vector)
            yield repo
    except SQLAlchemyError as exc:
        raise BoardError("DATABASE_UNAVAILABLE", 503) from exc


class Repository:
    def __init__(self, connection, mode, versions, token):
        self.c, self.mode, self.versions, self.token = connection, mode, versions, token
        table = "product_promotion_records" if mode == "product" else "shop_channel_promotion_daily"
        ids = [
            v["id"]
            for k, v in versions.items()
            if k in (("product_promotion",) if mode == "product" else STORE_CODES)
        ]
        self.rows = [
            dict(r)
            for r in connection.execute(
                sa.text(
                    f"SELECT * FROM public.{table} WHERE dataset_version_id=ANY(:ids) "
                    "ORDER BY source_row_key,id"
                ),
                dict(ids=ids),
            ).mappings()
        ]
        for row in self.rows:
            row["date"] = row["period_start"] if mode == "product" else row["business_date"]
        self.rows = [r for r in self.rows if r["date"] is not None]
        observed = [r["date"] for r in self.rows]
        self.end = max(observed) if observed else None
        primary = versions.get("product_promotion") if mode == "product" else None
        self.start = primary["period_start"] if primary else min(observed, default=None)
        self.profiles = (
            ProductRepository(connection, versions, token).profiles() if mode == "product" else {}
        )

    def metadata(self):
        """实际观察范围与推荐窗口分开；没有上游完成标记时完整日始终未知。"""
        dates = sorted({r["date"] for r in self.rows if self.start and r["date"] >= self.start})
        default_start = (
            (
                dates[max(0, len(dates) - 30)]
                if self.mode == "product"
                else max(self.start, self.end - timedelta(days=29))
            )
            if dates
            else None
        )
        sources = []
        product_sync = source_sync_status(self.c, self.versions) if self.mode == "product" else None
        for code in PRODUCT_CODES if self.mode == "product" else STORE_CODES:
            version = self.versions.get(code)
            attempt = (
                product_sync[0]
                if product_sync
                else (
                    self.c.execute(
                        sa.text(
                            "SELECT status,started_at,finished_at,error_code FROM public.task_runs "
                            "WHERE job_code=:job ORDER BY queued_at DESC,id DESC LIMIT 1"
                        ),
                        dict(
                            job="sync_product_board" if self.mode == "product" else "sync_" + code
                        ),
                    )
                    .mappings()
                    .first()
                )
            )
            success = (
                product_sync[1]
                if product_sync
                else self.c.execute(
                    sa.text(
                        "SELECT finished_at FROM public.task_runs WHERE status='succeeded' AND "
                        "result_version_id=:v ORDER BY finished_at DESC LIMIT 1"
                    ),
                    dict(
                        v=(
                            self.versions["profile_links"]["id"]
                            if self.mode == "product"
                            else version["id"]
                        )
                        if version
                        else None
                    ),
                ).scalar_one_or_none()
            )
            source_rows = [
                r for r in self.rows if version and r["dataset_version_id"] == version["id"]
            ]
            first = min((r["date"] for r in source_rows), default=None)
            last = max((r["date"] for r in source_rows), default=None)
            if code == "product_daily" and version:
                first, last = self.c.execute(
                    sa.text(
                        "SELECT min(business_date),max(business_date) FROM product_daily_facts "
                        "WHERE dataset_version_id=:v"
                    ),
                    dict(v=version["id"]),
                ).one()
            sources.append(
                dict(
                    dataset=code,
                    version=version["id"] if version else None,
                    table=version["source_locator"] if version else None,
                    observed_from=first,
                    observed_through=last,
                    date_status="not_applicable"
                    if code in ("profile_records", "profile_links")
                    else "business_date",
                    business_date_basis="payment_date"
                    if code in (*STORE_CODES, "product_promotion")
                    else None,
                    attribution_state="source_reported_payment_date"
                    if code in (*STORE_CODES, "product_promotion")
                    else None,
                    last_success_at=success.isoformat() if success else None,
                    latest_attempt={
                        k: v.isoformat() if hasattr(v, "isoformat") else v
                        for k, v in dict(attempt or {}).items()
                    },
                    status="observed" if version else "not_connected",
                    complete_through=None,
                )
            )
        return dict(
            mode=self.mode,
            business_date_basis="payment_date",
            attribution_state="source_reported_payment_date",
            attribution_window_days=None,
            data_version=self.token,
            definition_revision=definition_revision(),
            date_from=self.start,
            date_to=self.end,
            recommended_from=default_start,
            recommended_to=self.end,
            observed_rows=str(len(self.rows)),
            complete_through=None,
            completeness="unknown",
            sources=sources,
            profile_basis="当前唯一逐字段资料共识" if self.mode == "product" else None,
        )
