"""经营只读仓储；所有筛选、汇总和分页在PG，不读来源或全量前端快照。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa

from app.db import tables as t
from app.modules.products.query import METRIC_VERSION, QueryError

FIELDS = ("product_revenue", "visitors", "views", "favorites", "buyers", "orders", "units")


def days_between(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def year_date(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return None


class ProductRepository:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def snapshot(self, expected_version=None):
        """校验版本及所有业务读取共享一个REPEATABLE READ只读快照。"""
        if self.engine is None:
            raise QueryError(503, "DATABASE_NOT_CONFIGURED", "经营数据库尚未配置")
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
            with c.begin():
                c.execute(sa.text("SET TRANSACTION READ ONLY"))
                view = ProductView(c, expected_version)
                yield view


class ProductView:
    def __init__(self, connection, expected_version):
        self.connection = connection
        self.head = connection.execute(
            sa.select(t.publication_heads.c.publication_id).where(
                t.publication_heads.c.scope == "M1"
            )
        ).scalar_one_or_none()
        if self.head is None:
            raise QueryError(503, "M1_NOT_CONNECTED", "尚无已发布的M1经营数据")
        self.token = f"{self.head}.{METRIC_VERSION}"
        if expected_version and expected_version != self.token:
            raise QueryError(409, "DATA_VERSION_CHANGED", "经营数据版本已变化，请整组重新查询")
        self.versions = {
            r["dataset_id"]: r
            for r in connection.execute(
                sa.select(t.dataset_versions)
                .join(
                    t.publication_datasets,
                    t.publication_datasets.c.version_id == t.dataset_versions.c.version_id,
                )
                .where(t.publication_datasets.c.publication_id == self.head)
            ).mappings()
        }
        resolved = self.versions.get("product_profile")
        inputs = resolved["validation_summary"].get("inputs", {}) if resolved else {}
        if not resolved or any(
            inputs.get(k) != str(self.versions[k]["version_id"])
            for k in ("product_profile_source", "shop_identity")
        ):
            raise QueryError(
                503, "PROFILE_NOT_READY", "本批资料解析未完成，请检查解析任务；未混用旧资料"
            )
        self.latest = connection.execute(sa.select(sa.func.max(t.product_days.c.business_date)))
        self.latest = self.latest.scalar_one()
        if self.latest is None:
            raise QueryError(503, "M1_NO_PRODUCT_DAYS", "尚无商品日数据")
        self.shops = list(
            connection.execute(
                sa.select(t.shops).where(t.shops.c.platform_code == "pdd")
            ).mappings()
        )
        self.imported = [
            tuple(date.fromisoformat(v) for v in w)
            for w in self.versions["product_daily"]["requested_range"].get("windows", [])
        ]
        self._source = t.product_days.outerjoin(
            t.product_profiles,
            sa.and_(
                *(t.product_days.c[k] == t.product_profiles.c[k] for k in t.K),
                t.product_profiles.c.last_changed_version_id == resolved["version_id"],
            ),
        ).join(
            t.shops,
            sa.and_(
                t.shops.c.platform_code == t.product_days.c.platform_code,
                t.shops.c.shop_id == t.product_days.c.shop_id,
            ),
        )
        self._expected_shop_cache = {}
        self._product_catalog_cache = {}

    def normalized(self, query):
        end = query.date_to or query.date_from or self.latest
        start = query.date_from or (end if query.date_to else end - timedelta(days=29))
        return query.model_copy(update={"date_from": start, "date_to": end})

    def where(self, query, *, dates=None):
        p, profile = t.product_days.c, t.product_profiles.c
        result = [p.platform_code == "pdd"]
        if dates is not None:
            result.append(p.business_date.in_(dates))
        if query.shop_id:
            result.append(p.shop_id == query.shop_id)
        if query.product_id:
            result.append(p.product_id == query.product_id)
        if query.product_key:
            result.append(
                sa.tuple_(*(p[k] for k in t.K)).in_(
                    [tuple(key.split(":")) for key in query.product_key]
                )
            )
        for field in ("operator", "category", "level"):
            value = getattr(query, field)
            if value is not None:
                if value == "__unassigned__":
                    result.append(profile[field].is_(None))
                else:
                    result.append(profile[field] == value)
        if query.keyword:
            name = sa.func.coalesce(profile.product_name, p.source_product_name, "")
            result.append(
                sa.or_(
                    name.icontains(query.keyword, autoescape=True),
                    p.product_id.icontains(query.keyword, autoescape=True),
                )
            )
        return result

    def grouped(self, query, dates, *, by=()):
        """只返回所需聚合，带有效值计数，防SQL SUM静默吞掉NULL。"""
        p = t.product_days.c
        groups = [p[k] if k in p else t.product_profiles.c[k] for k in by]
        stmt = sa.select(
            *groups,
            sa.func.count().label("rows"),
            sa.func.count(sa.distinct(p.business_date)).label("observed_days"),
            *[
                expr
                for f in FIELDS
                for expr in (sa.func.sum(p[f]).label(f), sa.func.count(p[f]).label(f + "_count"))
            ],
        )
        stmt = stmt.select_from(self._source).where(*self.where(query, dates=dates))
        if groups:
            stmt = stmt.group_by(*groups)
        return [dict(r) for r in self.connection.execute(stmt).mappings()]

    def coverage(self, query, dates):
        imported = {day for day in dates if any(a <= day <= b for a, b in self.imported)}
        observed = self.grouped(query, dates, by=("shop_id", "business_date"))
        shops = {query.shop_id} if query.shop_id else {s["shop_id"] for s in self.shops}
        if query.product_key:
            shops = {key.split(":")[1] for key in query.product_key}
        elif any(
            getattr(query, k) is not None for k in ("operator", "category", "level", "keyword")
        ):
            # 同一筛选在整个已接收事实范围确定一次店集合，不随每期缺数收缩。
            key = (query.operator, query.category, query.level, query.keyword)
            if key not in self._expected_shop_cache:
                self._expected_shop_cache[key] = set(
                    self.connection.execute(
                        sa.select(t.product_days.c.shop_id)
                        .select_from(self._source)
                        .where(*self.where(query))
                        .distinct()
                    ).scalars()
                )
            shops = self._expected_shop_cache[key]
        pairs = {(r["shop_id"], r["business_date"]) for r in observed}
        missing = [d for d in dates if d in imported and any((s, d) not in pairs for s in shops)]
        missing_products = []
        if query.product_key:
            rows = self.grouped(query, dates, by=(*t.K, "business_date"))
            present = {
                (r["platform_code"], r["shop_id"], r["product_id"], r["business_date"])
                for r in rows
            }
            for key in sorted(set(query.product_key)):
                absent = [d for d in dates if d in imported and (*key.split(":"), d) not in present]
                if absent:
                    missing_products.append(dict(product_key=key, dates=absent))
        return dict(
            not_imported_dates=sorted(set(dates) - imported),
            missing_record_dates=missing,
            observed_days=len({r["business_date"] for r in observed}),
            expected_days=len(dates),
            expected_shops=sorted(shops),
            missing_product_dates=missing_products,
            all_observed=bool(observed)
            and len(imported) == len(dates)
            and not missing
            and not missing_products,
            completeness="unknown",
        )

    def details(self, query):
        p, pr = t.product_days.c, t.product_profiles.c
        fields = [p[k] for k in t.K] + [
            p.business_date,
            p.source_product_name,
            p.visitors,
            p.product_revenue,
            p.buyers,
            p.favorites,
            p.quality_flags,
            sa.func.coalesce(pr.product_name, p.source_product_name).label("product_name"),
            pr.category,
            pr.level,
            pr.operator,
            pr.listed_date,
            pr.field_statuses,
            pr.profile_match_status,
            t.shops.c.shop_name,
        ]
        pred = self.where(query, dates=days_between(query.date_from, query.date_to))
        count = self.connection.execute(
            sa.select(sa.func.count()).select_from(self._source).where(*pred)
        ).scalar_one()
        stmt = (
            sa.select(*fields)
            .select_from(self._source)
            .where(*pred)
            .order_by(p.business_date.desc(), p.shop_id, p.product_id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        return count, [dict(r) for r in self.connection.execute(stmt).mappings()]

    def products(self, query, dates):
        """期间每K一条资料；仅在本请求的一致快照内复用相同查询，返回独立对象。"""
        key = (
            tuple(sorted(set(dates))),
            query.shop_id,
            query.product_id,
            tuple(sorted(set(query.product_key))),
            query.operator,
            query.category,
            query.level,
            query.keyword,
        )
        if key in self._product_catalog_cache:
            return deepcopy(self._product_catalog_cache[key])
        p, pr = t.product_days.c, t.product_profiles.c
        stmt = (
            sa.select(
                *(p[k] for k in t.K),
                sa.func.coalesce(pr.product_name, p.source_product_name).label("product_name"),
                pr.category,
                pr.level,
                pr.operator,
                pr.field_statuses,
                pr.profile_match_status,
                pr.listed_date,
                t.shops.c.shop_name,
            )
            .select_from(self._source)
            .where(*self.where(query, dates=dates))
            .distinct(*(p[k] for k in t.K))
            .order_by(*(p[k] for k in t.K), p.source_product_name.is_(None), p.business_date.desc())
        )
        rows = {tuple(r[k] for k in t.K): dict(r) for r in self.connection.execute(stmt).mappings()}
        self._product_catalog_cache[key] = rows
        return deepcopy(rows)

    def metadata(self, query, coverage):
        """来源最近尝试和成功分别表达；资料任务成功不能掩盖来源失败。"""
        runs = t.sync_runs
        ranked = (
            sa.select(
                runs,
                sa.func.row_number()
                .over(
                    partition_by=runs.c.dataset_id,
                    order_by=(runs.c.started_at.desc(), runs.c.run_id),
                )
                .label("rank"),
            )
            .where(
                runs.c.dataset_id.in_(
                    ["product_daily", "promotion_records", "product_profile_source"]
                ),
                runs.c.source_id.in_([v["source_id"] for v in self.versions.values()]),
            )
            .subquery()
        )
        attempts = [
            dict(r)
            for r in self.connection.execute(sa.select(ranked).where(ranked.c.rank == 1)).mappings()
        ]
        failures = [r for r in attempts if r["status"] == "failed"]
        last = self.connection.execute(
            sa.select(sa.func.max(runs.c.finished_at)).where(
                runs.c.dataset_id.in_(
                    ["product_daily", "promotion_records", "product_profile_source"]
                ),
                runs.c.source_id.in_([v["source_id"] for v in self.versions.values()]),
                runs.c.status == "succeeded",
            )
        ).scalar_one()
        reasons = ["source_completeness_unknown", "current_profile_view"]
        if coverage["not_imported_dates"]:
            reasons.append("outside_imported_range")
        if coverage["missing_record_dates"]:
            reasons.append("no_records_received")
        if coverage["missing_product_dates"]:
            reasons.append("missing_product_dates")
        if coverage["observed_days"] == 0:
            reasons.append("no_records_received")
        return dict(
            data_version=self.token,
            input_batches={k: str(v["version_id"]) for k, v in self.versions.items()},
            metric_definition_version=METRIC_VERSION,
            profile_basis="current",
            data_through=self.latest,
            complete_through=None,
            last_success_at=last,
            sync_status="failed"
            if failures
            else (
                "running"
                if any(r["status"] in ("running", "pending") for r in attempts)
                else "succeeded"
            ),
            last_error={"code": failures[0]["error_code"], "message": failures[0]["error_summary"]}
            if failures
            else None,
            completeness="unknown",
            missing_reasons=sorted(set(reasons)),
            coverage=coverage,
            filters=query.model_dump(),
            queried_at=datetime.now(UTC),
            source_update_basis="warehouse_webpage",
            source_updated_at=None,
            source_update_status="not_recorded_for_current_publication",
            is_preview=False,
        )
