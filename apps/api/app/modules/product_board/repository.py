"""只读RR快照固定资料及经营版本；完整身份、逐字段共识和过滤集中在SQL。"""

import hashlib
import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

CODES = ("product_daily", "product_promotion", "profile_records", "profile_links")
DEFINITION_REVISION = (
    "m1-board-query-v1:"
    + hashlib.sha256(
        b"".join(
            Path(__file__).with_name(name).read_bytes()
            for name in ("query.py", "metrics.py", "repository.py")
        )
    ).hexdigest()
)


class BoardError(Exception):
    def __init__(self, code, status=422):
        self.code, self.status = code, status


def wire(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    return value


@contextmanager
def snapshot(engine, token=None):
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            versions = {
                r["dataset_code"]: dict(r)
                for r in c.execute(
                    sa.text(
                        "SELECT v.* FROM public.current_datasets d JOIN "
                        "public.dataset_versions v ON d.version_id=v.id "
                        "WHERE v.state='published' AND d.dataset_code=ANY(:codes)"
                    ),
                    dict(codes=[*CODES, "score_standards", "product_scores"]),
                ).mappings()
            }
            if any(code not in versions for code in CODES):
                raise BoardError("PRODUCTS_NOT_CONNECTED", 503)
            ids = {code: r["id"] for code, r in versions.items()}
            vector = hashlib.sha256(
                json.dumps({**ids, "definition": DEFINITION_REVISION}, sort_keys=True).encode()
            ).hexdigest()
            if token is not None and vector != token:
                raise BoardError("DATA_VERSION_CHANGED", 409)
            inputs = dict(
                c.execute(
                    sa.text(
                        "SELECT input_role,input_version_id FROM "
                        "public.version_inputs WHERE output_version_id=:v "
                    ),
                    dict(v=ids["profile_links"]),
                ).all()
            )
            if any(
                inputs.get(role) != ids[code]
                for role, code in (
                    ("profile_records", "profile_records"),
                    ("product_current", "product_daily"),
                    ("ad_current", "product_promotion"),
                )
            ):
                raise BoardError("INCOMPATIBLE_INPUTS", 503)
            yield Repository(c, versions, vector)
    except SQLAlchemyError as exc:
        raise BoardError("DATABASE_UNAVAILABLE", 503) from exc


class Repository:
    def __init__(self, connection, versions, token):
        self.c, self.versions, self.token = connection, versions, token
        self.start = versions["product_daily"]["period_start"]
        self.end = versions["product_daily"]["period_end"]
        self.params = {code: r["id"] for code, r in versions.items()}

    def meta(self):
        attempt = (
            self.c.execute(
                sa.text(
                    "SELECT status,started_at,finished_at,error_code FROM "
                    "public.task_runs WHERE job_code='sync_product_board' ORDER "
                    "BY queued_at DESC,id DESC LIMIT 1 "
                )
            )
            .mappings()
            .first()
        )
        success = self.c.execute(
            sa.text(
                "SELECT finished_at FROM public.task_runs WHERE "
                "job_code='sync_product_board' AND result_version_id=:v AND "
                "status='succeeded' ORDER BY finished_at DESC LIMIT 1 "
            ),
            dict(v=self.params["profile_links"]),
        ).scalar_one_or_none()
        counts = self.c.execute(
            sa.text(
                "SELECT count(*),count(DISTINCT "
                "listing_id),min(business_date),max(business_date) FROM "
                "public.product_daily_facts WHERE "
                "dataset_version_id=:product_daily "
            ),
            self.params,
        ).one()
        coverage = {code: [] for code in CODES}
        for row in self.c.execute(
            sa.text(
                "SELECT dataset_version_id,window_start,window_end,availability_state,"
                "completeness_state FROM public.coverage_records "
                "WHERE dataset_version_id=ANY(:ids)"
            ),
            dict(ids=[self.params[code] for code in CODES]),
        ).mappings():
            code = next(k for k in CODES if self.params[k] == row["dataset_version_id"])
            coverage[code].append(dict(row))

        def source_status(code):
            states = {r["availability_state"] for r in coverage[code]}
            return (
                "not_received"
                if not states
                else "observed"
                if states == {"observed"}
                else "partial"
                if "observed" in states
                else "no_records"
            )

        return dict(
            data_version=self.token,
            metric_definition_revision=DEFINITION_REVISION,
            date_from=self.start,
            date_to=self.end,
            observed_rows=str(counts[0]),
            product_count=str(counts[1]),
            success_at=success.isoformat() if success else None,
            complete_through=None,
            completeness="unknown",
            profile_basis="按当前资料归属，上架日期适用性未知",
            latest_attempt={
                k: v.isoformat() if hasattr(v, "isoformat") else v
                for k, v in dict(attempt or {}).items()
            },
            sources=[
                dict(
                    dataset=code,
                    table=self.versions[code]["source_locator"],
                    version=self.params[code],
                    received_at=self.versions[code]["received_at"].isoformat()
                    if self.versions[code]["received_at"]
                    else None,
                    status=source_status(code),
                    coverage=coverage[code],
                    completeness="unknown",
                )
                for code in CODES
                if code != "profile_links"
            ],
        )

    def profiles(self):
        """关系先收敛为一行/链接，保留共识状态及冲突候选，任何资料重复均不放大事实。"""
        rows = self.c.execute(
            sa.text("""SELECT l.listing_id,
          array_agg(DISTINCT nullif(btrim(p.source_product_name_raw),'')) FILTER (WHERE
          nullif(btrim(p.source_product_name_raw),'') IS NOT NULL) names,
          array_agg(DISTINCT nullif(btrim(p.source_category_raw),'')) FILTER (WHERE
          nullif(btrim(p.source_category_raw),'') IS NOT NULL) categories,
          array_agg(DISTINCT nullif(btrim(p.source_level_raw),'')) FILTER (WHERE
          nullif(btrim(p.source_level_raw),'') IS NOT NULL) levels,
          array_agg(DISTINCT nullif(btrim(p.source_operator_raw),'')) FILTER (WHERE
          nullif(btrim(p.source_operator_raw),'') IS NOT NULL) operators,
          array_agg(DISTINCT p.source_style_codes_raw) styles
          FROM public.profile_listing_links l JOIN public.profile_source_records p ON
          p.id=l.profile_record_id
          WHERE l.dataset_version_id=:profile_links AND p.dataset_version_id=:profile_records
          AND l.relationship_status='matched'
          GROUP BY l.listing_id"""),
            self.params,
        ).mappings()
        result = {}
        for r in rows:
            item = dict(
                candidates={}, field_status={}, listed_date=None, listed_date_status="unknown"
            )
            for plural, name in (
                ("names", "product_name"),
                ("categories", "category"),
                ("levels", "level"),
                ("operators", "operator"),
            ):
                choices = sorted(r[plural] or [])
                item[name] = choices[0] if len(choices) == 1 else None
                item["candidates"][name] = choices
                item["field_status"][name] = (
                    "matched" if len(choices) == 1 else "conflict" if choices else "missing"
                )
            item["styles"] = r["styles"] or []
            result[r["listing_id"]] = item
        return result

    def rows(self, start, end, *, ads=False):
        table = "product_promotion_records" if ads else "product_daily_facts"
        code = "product_promotion" if ads else "product_daily"
        day = "period_start" if ads else "business_date"
        return [
            dict(r)
            for r in self.c.execute(
                sa.text(
                    f"SELECT * FROM public.{table} WHERE dataset_version_id=:v "
                    f"AND {day} BETWEEN :start AND :end "
                    f"ORDER BY {day},source_shop_id,source_product_id,source_row_key"
                ),
                dict(v=self.params[code], start=start, end=end),
            ).mappings()
        ]
