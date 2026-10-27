"""M1批次暂存、原值UPSERT和原子发布；不直连来源，不实现页面指标。"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.db import tables as t
from app.modules.products.ingestion import (
    COLUMNS,
    DATASETS,
    SHOPS,
    SOURCE_IDS,
    IngestionError,
    digest,
    prepare,
    serializable,
)

TARGETS = {
    "product_daily": t.product_days,
    "promotion_records": t.promotion_records,
    "product_profile_source": t.product_profile_records,
}
STAGES = {
    "product_daily": t.metadata.tables["stg_product_records"],
    "promotion_records": t.metadata.tables["stg_promotion_records"],
    "product_profile_source": t.metadata.tables["stg_profile_records"],
}


def upsert(connection, table, rows, *, revisioned=False):
    """按真实主键分块写入；同内容只更新最近观察任务，不改真正变更版本。"""
    if not rows:
        return
    keys = [col.name for col in table.primary_key]
    for offset in range(0, len(rows), 500):
        batch = rows[offset : offset + 500]
        statement = insert(table).values(batch)
        changes = {name: statement.excluded[name] for name in batch[0] if name not in keys}
        if revisioned:
            changes["last_changed_version_id"] = sa.case(
                (
                    table.c.row_hash.is_distinct_from(statement.excluded.row_hash),
                    statement.excluded.last_changed_version_id,
                ),
                else_=table.c.last_changed_version_id,
            )
        statement = statement.on_conflict_do_update(index_elements=keys, set_=changes)
        connection.execute(statement)


class IngestionRepository:
    """每次手工任务独占会话锁；失败记录独立落库，旧发布保持有效。"""

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _head(connection, *, locked=False):
        query = sa.select(t.publication_heads.c.publication_id).where(
            t.publication_heads.c.scope == "M1"
        )
        if locked:
            query = query.with_for_update()
        return connection.execute(query).scalar_one_or_none()

    def run(self, read_source, windows, *, source_schema=None):
        """接收已批准范围的读取回调，返回安全批次摘要；不将源时间当修改水位。"""
        runs = {}
        with self.engine.connect() as connection:
            acquired = connection.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext('pdd:m1:ingestion'))")
            ).scalar_one()
            connection.commit()
            if not acquired:
                raise IngestionError("INGESTION_ALREADY_RUNNING")
            try:
                with connection.begin():
                    # 已持有本任务独占会话锁，遗留运行记录必然已失去执行者。
                    stale = (
                        connection.execute(
                            sa.select(t.sync_runs.c.run_id).where(
                                t.sync_runs.c.dataset_id.in_(DATASETS),
                                t.sync_runs.c.source_id.in_(SOURCE_IDS.values()),
                                t.sync_runs.c.status.in_(("pending", "running")),
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if stale:
                        connection.execute(
                            t.sync_runs.update()
                            .where(t.sync_runs.c.run_id.in_(stale))
                            .values(
                                status="failed",
                                phase="interrupted",
                                finished_at=datetime.now(UTC),
                                error_code="INTERRUPTED_PREVIOUS_ATTEMPT",
                                error_summary="前次执行已中断，本轮持锁后终结遗留状态",
                            )
                        )
                        for table in STAGES.values():
                            connection.execute(
                                table.update()
                                .where(table.c.sync_run_id.in_(stale))
                                .values(
                                    validation_status="invalid",
                                    validation_errors=[{"code": "interrupted_previous_attempt"}],
                                )
                            )
                    base = self._head(connection)
                    for dataset in DATASETS:
                        runs[dataset] = uuid4()
                        connection.execute(
                            t.sync_runs.insert().values(
                                run_id=runs[dataset],
                                dataset_id=dataset,
                                source_id=SOURCE_IDS[dataset],
                                status="running",
                                phase="extract",
                                requested_range={
                                    "windows": serializable(windows),
                                    "scope": "current_profiles"
                                    if dataset in ("product_profile_source", "shop_identity")
                                    else "six_shop_day",
                                },
                            )
                        )
                raw = read_source()
                self._stage(connection, raw, runs)
                batch = prepare(raw, windows)
                with connection.begin():
                    if self._head(connection, locked=True) != base:
                        raise IngestionError("PUBLICATION_CHANGED_DURING_READ")
                    self._guard(connection, batch)
                    result = self._publish(connection, batch, runs, base, source_schema or {})
                return result
            except BaseException as error:
                if connection.in_transaction():
                    connection.rollback()
                code = str(error) if isinstance(error, IngestionError) else type(error).__name__
                with connection.begin():
                    for dataset, run in runs.items():
                        connection.execute(
                            t.sync_runs.update()
                            .where(t.sync_runs.c.run_id == run)
                            .values(
                                status="failed",
                                phase="failed",
                                finished_at=datetime.now(UTC),
                                error_code=code,
                                error_summary="本轮接入未发布，已保留上一有效数据",
                            )
                        )
                        if dataset in STAGES:
                            table = STAGES[dataset]
                            connection.execute(
                                table.update()
                                .where(table.c.sync_run_id == run)
                                .values(
                                    validation_status="invalid",
                                    validation_errors=[{"code": "batch_rejected"}],
                                )
                            )
                raise
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(
                    sa.text("SELECT pg_advisory_unlock(hashtext('pdd:m1:ingestion'))")
                )
                connection.commit()

    @staticmethod
    def _stage(connection, raw, runs):
        with connection.begin():
            for dataset, table in STAGES.items():
                records = raw.get(dataset, [])
                id_column = {
                    "product_daily": "z_id",
                    "promotion_records": "id",
                    "product_profile_source": "__id",
                }[dataset]
                for offset in range(0, len(records), 500):
                    values = []
                    for index, row in enumerate(records[offset : offset + 500], offset + 1):
                        source_id = row.get(id_column)
                        values.append(
                            dict(
                                sync_run_id=runs[dataset],
                                row_no=index,
                                source_id=SOURCE_IDS[dataset],
                                source_record_id=None if source_id is None else str(source_id),
                                payload=serializable(row),
                                row_hash=digest(row),
                            )
                        )
                    connection.execute(table.insert(), values)

    @staticmethod
    def _scope(table, windows):
        return sa.and_(
            table.c.shop_id.in_([v[0] for v in SHOPS.values()]),
            sa.or_(*[table.c.business_date.between(a, b) for a, b in windows]),
        )

    def _guard(self, connection, batch):
        """缺旧键不删行、不混合发布；同推广ID跨范围重用也须拒绝。"""
        for dataset, table in TARGETS.items():
            query = sa.select(table)
            if dataset != "product_profile_source":
                query = query.where(self._scope(table, batch.windows))
            existing = connection.execute(query).mappings().all()
            keys = [c.name for c in table.primary_key]
            old = {tuple(row[k] for k in keys) for row in existing}
            new = {tuple(row[k] for k in keys) for row in batch.rows[dataset]}
            if old - new:
                raise IngestionError("EXISTING_KEYS_MISSING_IN_SOURCE")
            if any(row["source_id"] != SOURCE_IDS[dataset] for row in existing):
                raise IngestionError("SOURCE_CUTOVER_NOT_APPROVED")
        table = t.promotion_records
        incoming = {r["source_record_id"]: r for r in batch.rows["promotion_records"]}
        identity = (
            "platform_code",
            "shop_id",
            "product_id",
            "business_date",
            "source_window",
            "source_start_dt",
        )
        # 查已存全部来源ID，不能只查新窗口，否则会漏掉范围外ID碰撞。
        rows = connection.execute(
            sa.select(table).where(table.c.source_id == SOURCE_IDS["promotion_records"])
        ).mappings()
        for row in rows:
            candidate = incoming.get(row["source_record_id"])
            if candidate and any(row[k] != candidate[k] for k in identity):
                raise IngestionError("PROMOTION_SOURCE_ID_REUSED")
        keys = ("source_id", "source_profile_id", "source_column", "product_id")
        old_links = {
            tuple(r)
            for r in connection.execute(
                sa.select(*[t.product_profile_links.c[k] for k in keys]).where(
                    t.product_profile_links.c.source_id == SOURCE_IDS["product_profile_source"]
                )
            )
        }
        new_links = {tuple(row[k] for k in keys) for row in batch.links}
        if old_links - new_links:
            raise IngestionError("PROFILE_LINK_REMOVAL_UNCONFIRMED")

    def _publish(self, connection, batch, runs, base, schema):
        previous = {}
        if base:
            previous = {
                r["dataset_id"]: r
                for r in connection.execute(
                    sa.select(t.dataset_versions)
                    .join(
                        t.publication_datasets,
                        t.publication_datasets.c.version_id == t.dataset_versions.c.version_id,
                    )
                    .where(t.publication_datasets.c.publication_id == base)
                ).mappings()
            }
        versions = {}
        changed = []
        for dataset in DATASETS:
            prior = previous.get(dataset)
            if prior and prior["content_hash"] == batch.hashes[dataset]:
                versions[dataset] = prior["version_id"]
            else:
                version = versions[dataset] = uuid4()
                changed.append(dataset)
                connection.execute(
                    t.dataset_versions.insert().values(
                        version_id=version,
                        dataset_id=dataset,
                        source_id=SOURCE_IDS[dataset],
                        sync_run_id=runs[dataset],
                        schema_hash=digest(schema[dataset]) if dataset in schema else None,
                        projection_hash=digest(COLUMNS.get(dataset, list(SHOPS))),
                        content_hash=batch.hashes[dataset],
                        row_count=len(batch.rows[dataset]),
                        requested_range={
                            "windows": serializable(batch.windows)
                            if dataset in ("product_daily", "promotion_records")
                            else [],
                            "shops": [v[0] for v in SHOPS.values()],
                        },
                        validation_summary={
                            "identity_and_values": "validated",
                            "completeness": "unknown",
                            "profile_resolution": "pending_business_review",
                        },
                    )
                )
        identities = {}
        for dataset in ("product_daily", "promotion_records"):
            for row in batch.rows[dataset]:
                key = tuple(row[k] for k in t.K)
                if key not in identities:
                    identities[key] = dict(
                        zip(t.K, key, strict=True),
                        first_observed_date=row["business_date"],
                        last_observed_date=row["business_date"],
                    )
                identities[key]["first_observed_date"] = min(
                    identities[key]["first_observed_date"], row["business_date"]
                )
                identities[key]["last_observed_date"] = max(
                    identities[key]["last_observed_date"], row["business_date"]
                )
        for row in batch.links:
            key = tuple(row[k] for k in t.K)
            identities.setdefault(
                key,
                dict(zip(t.K, key, strict=True), first_observed_date=None, last_observed_date=None),
            )
        upsert(
            connection,
            t.shops,
            [
                dict(platform_code="pdd", shop_id=shop, shop_name=name)
                for shop, name in SHOPS.values()
            ],
        )
        # 已有观察边界不能因本次范围变窄而收缩，也不能用资料出现时间伪造上架日。
        old_dates = {
            tuple(row[k] for k in t.K): row
            for row in connection.execute(sa.select(t.shop_products)).mappings()
        }
        for key, row in identities.items():
            old = old_dates.get(key)
            if old:
                starts = [
                    d
                    for d in (old["first_observed_date"], row["first_observed_date"])
                    if d is not None
                ]
                ends = [
                    d
                    for d in (old["last_observed_date"], row["last_observed_date"])
                    if d is not None
                ]
                row["first_observed_date"] = min(starts) if starts else None
                row["last_observed_date"] = max(ends) if ends else None
        upsert(connection, t.shop_products, list(identities.values()))
        upsert(
            connection,
            t.shop_source_mappings,
            [
                dict(
                    source_id=SOURCE_IDS["shop_identity"],
                    source_shop_key=col,
                    platform_code="pdd",
                    shop_id=shop,
                    mapping_version=versions["shop_identity"],
                    evidence={"basis": "verified_six_shop_mapping", "source": "data-contract/4.1"},
                )
                for col, (shop, _) in SHOPS.items()
            ],
        )
        for dataset, table in TARGETS.items():
            values = [
                dict(row, last_changed_version_id=versions[dataset], last_seen_run_id=runs[dataset])
                for row in batch.rows[dataset]
            ]
            upsert(connection, table, values, revisioned=True)
        upsert(connection, t.product_profile_links, batch.links)
        for dataset in ("product_daily", "promotion_records"):
            if dataset in changed:
                self._coverage(connection, batch, dataset, versions[dataset])
        publication = base
        if changed:
            publication = uuid4()
            connection.execute(
                t.data_publications.insert().values(
                    publication_id=publication,
                    scope="M1",
                    rule_versions={
                        "ingestion": "m1-raw-v1",
                        "profile_resolution": "pending_business_review",
                        "scoring": "not_generated",
                    },
                    implementation_version="m1-ingestion-v1",
                )
            )
            connection.execute(
                t.publication_datasets.insert(),
                [
                    dict(publication_id=publication, dataset_id=d, version_id=v)
                    for d, v in versions.items()
                ],
            )
            upsert(connection, t.publication_heads, [dict(scope="M1", publication_id=publication)])
        for dataset in DATASETS:
            count = len(batch.rows[dataset])
            flags = Counter()
            for row in batch.rows[dataset]:
                flags.update(row.get("quality_flags", []))
            connection.execute(
                t.sync_runs.update()
                .where(t.sync_runs.c.run_id == runs[dataset])
                .values(
                    status="succeeded",
                    phase="published" if changed else "unchanged",
                    row_count=count,
                    finished_at=datetime.now(UTC),
                    validation_summary={
                        "quality_flags": dict(flags),
                        "completeness": "unknown",
                        "source_snapshot": "not_provided",
                    },
                    watermark={
                        "observed_windows": serializable(batch.windows),
                        "publication_id": str(publication),
                        "incremental_cursor": None,
                    },
                )
            )
            if dataset in STAGES:
                table = STAGES[dataset]
                connection.execute(
                    table.update()
                    .where(table.c.sync_run_id == runs[dataset])
                    .values(validation_status="valid")
                )
        return dict(
            publication_id=str(publication),
            changed_datasets=changed,
            rows={d: len(r) for d, r in batch.rows.items()},
            profile_links=len(batch.links),
            windows=serializable(batch.windows),
            completeness="unknown",
        )

    @staticmethod
    def _coverage(connection, batch, dataset, version):
        rows = batch.rows[dataset]
        values = []
        for shop, _ in SHOPS.values():
            observed = {r["business_date"] for r in rows if r["shop_id"] == shop}
            for start, end in batch.windows:
                days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
                values.append(
                    dict(
                        version_id=version,
                        platform_code="pdd",
                        shop_id=shop,
                        window_from=start,
                        window_to=end,
                        observed_days=len(observed.intersection(days)),
                        completeness="unknown",
                        complete_through=None,
                        missing_dates=[str(d) for d in days if d not in observed],
                        evidence={
                            "absence_semantics": "unknown",
                            "dates_are_observed_not_complete": True,
                        },
                    )
                )
        connection.execute(t.dataset_coverage.insert(), values)
