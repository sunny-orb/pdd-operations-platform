"""M2独立RR快照；商品固定相容四头，店铺仅绑定两渠道。"""

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from functools import cached_property
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.product_board.repository import BoardError, source_sync_status
from app.modules.product_board.repository import Repository as ProductRepository
from app.modules.sales.datasets import HISTORY_SPEC
from app.modules.sales.promotion_history import effective_rows, history_rows, supplement_audit

from . import metrics as promotion_metrics

PRODUCT_CODES = ("product_daily", "product_promotion", "profile_records", "profile_links")
STORE_CODES = ("shop_channel_product_promotion", "shop_channel_star_shop")


def definition_revision():
    """绑定本域及实际使用的共享查询规则，避免来源未变时复用旧目录版本。"""
    shared = Path(__file__).parents[1]
    return (
        "m2-v1:"
        + hashlib.sha256(
            promotion_metrics.definition_revision().encode()
            + b"".join(
                shared.joinpath(name).read_bytes()
                for name in (
                    "sales/promotion_history.py",
                    "sales/source_policy.py",
                    "product_board/profile_rules.py",
                    "product_board/filter_projection.py",
                    "product_board/metrics.py",
                    "product_board/repository.py",
                )
            )
            + b"".join(
                Path(__file__).with_name(name).read_bytes()
                for name in ("repository.py", "query.py", "diagnosis.py")
            )
        ).hexdigest()
    )


def query_version(mode, ids):
    """按域、查询口径和实际输入生成页面及问答共用的版本标识。"""
    return hashlib.sha256(
        json.dumps(
            {"mode": mode, "definition": definition_revision(), **ids}, sort_keys=True
        ).encode()
    ).hexdigest()


@contextmanager
def snapshot(engine, mode, token=None):
    """同快照读版本、事实与资料；token过期409，缺失来源503，无写入。"""
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    codes = PRODUCT_CODES if mode == "product" else (*STORE_CODES, HISTORY_SPEC.code)
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
            vector = query_version(mode, {code: row["id"] for code, row in versions.items()})
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
        # 仅属于本次RR只读事务；资料/覆盖上下文不跨请求或发布版本复用。
        self.label_cache = {}
        self.coverage_member_cache = {}

    def rows_for_windows(self, windows, listing_ids=None):
        """读取必要日期窗的原行；商品可限定已按当前窗选定的身份，店铺沿用整行择源。"""
        if self.mode == "store":
            return [
                row
                for row in self.rows
                if windows is None or any(start <= row["date"] <= end for start, end in windows)
            ]
        if windows == [] or listing_ids is not None and not listing_ids:
            return []
        where = ["dataset_version_id=:v", "period_start IS NOT NULL"]
        params = dict(v=self.versions["product_promotion"]["id"])
        if windows is not None:
            clauses = []
            for index, (start, end) in enumerate(windows):
                clauses.append(f"period_start BETWEEN :start{index} AND :end{index}")
                params.update({f"start{index}": start, f"end{index}": end})
            where.append("(" + " OR ".join(clauses) + ")")
        if listing_ids is not None:
            where.append("listing_id=ANY(:listing_ids)")
            params["listing_ids"] = list(listing_ids)
        return [
            dict(row)
            for row in self.c.execute(
                sa.text(
                    "SELECT id,dataset_version_id,source_row_key,period_start,period_start AS date,"
                    "listing_id,source_shop_id,source_shop_name,source_product_id,"
                    "source_product_name,spend,attributed_revenue,net_attributed_revenue,"
                    "attributed_deals,impressions,clicks "
                    "FROM public.read_product_promotion_records "
                    "WHERE " + " AND ".join(where) + " ORDER BY source_row_key,id"
                ),
                params,
            ).mappings()
        ]

    @cached_property
    def rows(self):
        """仅经营查询加载事实；元信息、商品目录不经过整批事实反序列化。"""
        if self.mode == "product":
            return self.rows_for_windows(None)
        _STORE_COLS = (
            "id,dataset_version_id,source_row_key,shop_id,channel_code,"
            "source_shop_id,source_shop_name,source_channel_label,"
            "business_date,spend,attributed_revenue,net_attributed_revenue,"
            "attributed_deals,impressions,clicks"
        )
        ids = [v["id"] for k, v in self.versions.items() if k in STORE_CODES]
        rows = [
            dict(r)
            for r in self.c.execute(
                sa.text(
                    f"SELECT {_STORE_COLS} FROM public.shop_channel_promotion_daily "
                    "WHERE dataset_version_id=ANY(:ids) "
                    "ORDER BY source_row_key,id"
                ),
                dict(ids=ids),
            ).mappings()
        ]
        self.supplements = supplement_audit(rows, self.history)
        rows = effective_rows(rows, self.history)
        for row in rows:
            row["date"] = row["business_date"]
        return [r for r in rows if r["date"] is not None]

    @cached_property
    def history(self):
        return history_rows(self.c, self.versions) if self.mode == "store" else []

    @cached_property
    def date_counts(self):
        """每个实际日期的记录数；不补缺日，不用资料日期推定推广日期。"""
        if self.mode == "product":
            return dict(
                self.c.execute(
                    sa.text(
                        "SELECT period_start,count(*) FROM public.read_product_promotion_records "
                        "WHERE dataset_version_id=:v AND period_start IS NOT NULL "
                        "GROUP BY period_start"
                    ),
                    dict(v=self.versions["product_promotion"]["id"]),
                ).all()
            )
        counts = {}
        for row in self.rows:
            counts[row["date"]] = counts.get(row["date"], 0) + 1
        return counts

    @cached_property
    def start(self):
        primary = self.versions.get("product_promotion") if self.mode == "product" else None
        return primary["period_start"] if primary else min(self.date_counts, default=None)

    @cached_property
    def end(self):
        return max(self.date_counts, default=None)

    @cached_property
    def profiles(self):
        return (
            ProductRepository(self.c, self.versions, self.token).profiles()
            if self.mode == "product"
            else {}
        )

    @cached_property
    def listing_ids(self):
        """已发布推广中实际存在的身份，包含比较期，校验时无需读取经营金额。"""
        return set(
            self.c.execute(
                sa.text(
                    "SELECT DISTINCT listing_id FROM public.read_product_promotion_records "
                    "WHERE dataset_version_id=:v AND period_start IS NOT NULL"
                ),
                dict(v=self.versions["product_promotion"]["id"]),
            ).scalars()
        )

    def identity_rows(self, start=None, end=None, *, ordered=False, listing_ids=None):
        """仅投影身份与来源名称；已选ID可跨已发布日期回显，不改变日期目录范围。"""
        if listing_ids is not None and not listing_ids:
            return []
        distinct = "" if ordered else "DISTINCT "
        order = " ORDER BY source_row_key,id" if ordered else ""
        where = ["dataset_version_id=:v", "period_start IS NOT NULL"]
        params = dict(v=self.versions["product_promotion"]["id"])
        if start is not None and end is not None:
            where.append("period_start BETWEEN :start AND :end")
            params.update(start=start, end=end)
        if listing_ids is not None:
            where.append("listing_id=ANY(:listing_ids)")
            params["listing_ids"] = list(listing_ids)
        return [
            dict(r)
            for r in self.c.execute(
                sa.text(
                    f"SELECT {distinct}period_start AS date,listing_id,source_shop_id,"
                    "source_shop_name,source_product_id,source_product_name "
                    "FROM public.read_product_promotion_records WHERE "
                    + " AND ".join(where)
                    + order
                ),
                params,
            ).mappings()
        ]

    @cached_property
    def recommended_from(self):
        """元信息与默认展示共用推荐起日；商品模式按实际观察日计数。"""
        dates = sorted(day for day in self.date_counts if self.start and day >= self.start)
        return (
            (
                dates[max(0, len(dates) - 30)]
                if self.mode == "product"
                else max(self.start, self.end - timedelta(days=29))
            )
            if dates
            else None
        )

    def metadata(self):
        """实际观察范围与推荐窗口分开；没有上游完成标记时完整日始终未知。"""
        sources = []
        product_sync = source_sync_status(self.c, self.versions) if self.mode == "product" else None
        for code in PRODUCT_CODES if self.mode == "product" else (*STORE_CODES, HISTORY_SPEC.code):
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
            if self.mode == "product":
                first = min(self.date_counts, default=None) if code == "product_promotion" else None
                last = self.end if code == "product_promotion" else None
            else:
                source_rows = [
                    r for r in self.rows if version and r["dataset_version_id"] == version["id"]
                ]
                first = min((r["date"] for r in source_rows), default=None)
                last = max((r["date"] for r in source_rows), default=None)
            if code == "product_daily" and version:
                first, last = self.c.execute(
                    sa.text(
                        "SELECT min(business_date),max(business_date) "
                        "FROM read_product_daily_facts "
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
                    if code in (*STORE_CODES, HISTORY_SPEC.code, "product_promotion")
                    else None,
                    attribution_state="source_reported_payment_date"
                    if code in (*STORE_CODES, HISTORY_SPEC.code, "product_promotion")
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
            historical_supplements=self.supplements if self.mode == "store" else [],
            mode=self.mode,
            business_date_basis="payment_date",
            attribution_state="source_reported_payment_date",
            attribution_window_days=None,
            data_version=self.token,
            definition_revision=definition_revision(),
            date_from=self.start,
            date_to=self.end,
            recommended_from=self.recommended_from,
            recommended_to=self.end,
            observed_rows=str(sum(self.date_counts.values())),
            complete_through=None,
            completeness="unknown",
            sources=sources,
            profile_basis="当前唯一逐字段资料共识" if self.mode == "product" else None,
        )
