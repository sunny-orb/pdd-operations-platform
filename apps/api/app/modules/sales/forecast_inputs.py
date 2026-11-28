"""月度预测只读同一快照中的 F01 日报与平台目标。"""

from calendar import monthrange
from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .datasets import SPECS
from .f01_gmv import F01_GMV_REVISION
from .targets import MEMBERS, TARGET_REVISION, TARGET_URL


def read_forecast_publications(connection):
    """读取本期 F01 和目标的发布指针及最近接收结果，供计算和快照核对。"""
    return {
        row["dataset_code"]: row
        for row in connection.execute(
            sa.text("""
                SELECT d.dataset_code, v.id, v.source_system, v.source_locator,
                       v.row_schema_revision, last_run.status, last_run.error_code
                FROM public.current_datasets d
                JOIN public.dataset_versions v ON v.id=d.version_id
                    AND v.dataset_code=d.dataset_code AND v.state='published'
                LEFT JOIN LATERAL (
                    SELECT status, error_code FROM public.task_runs
                    WHERE job_code='sync_' || d.dataset_code AND status IN ('failed','succeeded')
                    ORDER BY queued_at DESC, id DESC LIMIT 1
                ) last_run ON true
                WHERE d.dataset_code IN ('shop_refunds','sales_targets')
            """)
        ).mappings()
    }


def _monthly_target(connection, version, month, month_end):
    if not version:
        return None, "source_unavailable"
    if (
        version["row_schema_revision"] != f"{TARGET_REVISION}:original_gmv"
        or version["source_locator"] != TARGET_URL
        or version["source_system"] != "feishu"
    ):
        return None, "definition_unverified"
    if version["error_code"] == "TARGET_MONTH_CONFLICT":
        return None, "source_conflict"
    rows = (
        connection.execute(
            sa.text("""
            SELECT t.target_amount, t.scope_status, t.period_kind, t.period_status, t.period_end,
                   array_agg(m.shop_id ORDER BY m.shop_id) AS members
            FROM public.sales_targets t LEFT JOIN public.sales_target_shops m ON m.target_id=t.id
            WHERE t.dataset_version_id=:version AND t.period_start=:month
            GROUP BY t.id
        """),
            {"version": version["id"], "month": month},
        )
        .mappings()
        .all()
    )
    if not rows:
        return None, "missing"
    if len(rows) != 1:
        return None, "conflict"
    row = rows[0]
    if (
        row["period_kind"] != "month"
        or row["period_end"] != month_end
        or row["period_status"] != "confirmed"
        or row["scope_status"] != "complete"
        or set(row["members"]) != set(MEMBERS)
    ):
        return None, "scope_unverified"
    return row["target_amount"], "failed_retained" if version["status"] == "failed" else "available"


def _sales_days(connection, version, start, end):
    """每月最多返回31行；先按店日核对，再汇总六店，同键重复不能放大金额。"""
    params = {"version": version, "month": start, "end": end, "members": list(MEMBERS)}
    rows = connection.execute(
        sa.text("""
            WITH shop_days AS (
                SELECT business_date,shop_id,count(*) AS records,
                       bool_and(source_platform IS NOT DISTINCT FROM '拼多多'
                           AND source_department IS NOT DISTINCT FROM '市场部'
                           AND source_reported_amount IS NOT NULL AND source_reported_amount >= 0
                           AND source_reported_gmv IS NOT NULL AND source_reported_gmv >= 0
                           AND CASE WHEN source_adjustment_amount IS NULL
                               THEN source_reported_gmv = source_reported_amount
                               ELSE source_reported_gmv =
                                   source_reported_amount - source_adjustment_amount
                           END
                       ) AS valid_amount,
                       sum(source_reported_gmv) AS gmv
                FROM public.shop_refund_records
                WHERE dataset_version_id=:version AND business_date BETWEEN :month AND :end
                    AND shop_id=ANY(:members)
                GROUP BY business_date,shop_id
            )
            SELECT business_date, array_agg(shop_id) AS present,
                   array_agg(shop_id) FILTER (WHERE records>1) AS duplicates,
                   array_agg(shop_id) FILTER (WHERE records=1 AND NOT valid_amount) AS invalid,
                   sum(gmv) FILTER (WHERE records=1 AND valid_amount) AS gmv
            FROM shop_days GROUP BY business_date ORDER BY business_date
        """),
        params,
    ).mappings()
    return {row["business_date"]: row for row in rows}


def _source_complete_through(connection, version, month):
    """单列源已声明的同范围水位。各店任一缺凭据时为空，不以观察记录补水位。"""
    day = connection.scalar(
        sa.text("""
            SELECT min(complete_through) FROM public.coverage_records
            WHERE dataset_version_id=:version AND scope_kind='shop' AND shop_id=ANY(:members)
                AND field_code IS NULL AND window_start<=:month AND window_end>=:month
                AND availability_state='observed' AND complete_through IS NOT NULL
                AND completeness_state IN ('complete','partial')
            HAVING count(DISTINCT shop_id)=:member_count
        """),
        {
            "version": version,
            "month": month,
            "members": list(MEMBERS),
            "member_count": len(MEMBERS),
        },
    )
    return day.isoformat() if day is not None else None


def read_forecast_inputs(connection: Connection, *, month: date, as_of: date) -> dict:
    """在调用者的只读 RR 事务内，按上海核验日（不含当日）取自然月连续六店日报。"""
    month = month.replace(day=1)
    month_end = month.replace(day=monthrange(month.year, month.month)[1])
    end = min(month_end, as_of - timedelta(days=1))
    publications = read_forecast_publications(connection)
    version = publications.get("shop_refunds")
    target, target_state = _monthly_target(
        connection, publications.get("sales_targets"), month, month_end
    )
    coverage = {
        "sales_state": "source_unavailable",
        "sales_source_state": "failed_retained"
        if version and version["status"] == "failed"
        else "published"
        if version
        else "source_unavailable",
        "sales_checked_through": None,
        "sales_observed_through": None,
        "source_complete_through": None,
        "expected_shop_ids": list(MEMBERS),
        "sales_gaps": [],
        "target_state": target_state,
        "promotion_state": "basis_unverified",
        "promotion_note": "当前推广看板的渠道合计与 SOP 算例尚未对齐，暂缺可核验的同口径推广数据。",
    }
    inputs = {
        "cutoff": None,
        "daily": {},
        "target": target,
        "promotion": {},
        "comparable_start": None,
        "versions": {code: row["id"] for code, row in publications.items()},
        "coverage": coverage,
    }
    if not version:
        return inputs
    if (
        version["row_schema_revision"] != F01_GMV_REVISION
        or version["source_system"] != "feishu"
        or version["source_locator"] != SPECS["shop_refunds"].locator
    ):
        coverage["sales_state"] = "projection_unavailable"
        return inputs
    if end < month:
        coverage["sales_state"] = "awaiting_first_complete_day"
        return inputs
    coverage["sales_checked_through"] = end.isoformat()
    coverage["source_complete_through"] = _source_complete_through(connection, version["id"], month)
    observed = _sales_days(connection, version["id"], month, end)
    coverage["sales_observed_through"] = max(observed).isoformat() if observed else None
    gaps = coverage["sales_gaps"]
    for offset in range((end - month).days + 1):
        day = month + timedelta(days=offset)
        row = observed.get(day)
        present = set(row["present"]) if row else set()
        missing = [shop for shop in MEMBERS if shop not in present]
        duplicates = sorted(row["duplicates"] or ()) if row else []
        invalid = sorted(row["invalid"] or ()) if row else []
        if missing or duplicates or invalid:
            gaps.append(
                {
                    "date": day.isoformat(),
                    "missing_shop_ids": missing,
                    "duplicate_shop_ids": duplicates,
                    "invalid_amount_shop_ids": invalid,
                }
            )
        elif not gaps:
            inputs["daily"][day] = row["gmv"]
            inputs["cutoff"] = day
    coverage["sales_state"] = (
        "awaiting_first_complete_day"
        if inputs["cutoff"] is None
        else "partial"
        if gaps
        else "ready"
    )
    return inputs
