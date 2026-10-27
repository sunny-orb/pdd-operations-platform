"""从已接收资料解析当前展示属性；不读取数仓、不发明历史归属。"""

from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.db import tables as t
from app.modules.products.ingestion import IngestionError, digest
from app.modules.products.ingestion_repository import upsert
from app.modules.products.profile_rules import parse_ids, resolve_fields

RULE_VERSION = "m1-profile-consensus-v1"
SOURCE = "m1_profile_resolution"


class ProfileRepository:
    """发布唯一资料解析；使用接入同一会话锁和单事务，失败不改变当前指针。"""

    def __init__(self, engine):
        self.engine = engine

    def run(self):
        run_id = uuid4()
        with self.engine.connect() as c:
            locked = c.execute(
                sa.text("SELECT pg_try_advisory_lock(hashtext('pdd:m1:ingestion'))")
            ).scalar_one()
            c.commit()
            if not locked:
                raise IngestionError("INGESTION_ALREADY_RUNNING")
            try:
                with c.begin():
                    c.execute(
                        t.sync_runs.insert().values(
                            run_id=run_id,
                            dataset_id="product_profile",
                            source_id=SOURCE,
                            status="running",
                            phase="resolve",
                        )
                    )
                with c.begin():
                    result = self._publish(c, run_id)
                    c.execute(
                        t.sync_runs.update()
                        .where(t.sync_runs.c.run_id == run_id)
                        .values(
                            status="succeeded",
                            phase="published" if result["changed"] else "unchanged",
                            finished_at=datetime.now(UTC),
                            row_count=result["profiles"],
                            validation_summary={"rule": RULE_VERSION, "completeness": "unknown"},
                        )
                    )
                return result
            except BaseException as exc:
                if c.in_transaction():
                    c.rollback()
                with c.begin():
                    c.execute(
                        t.sync_runs.update()
                        .where(t.sync_runs.c.run_id == run_id)
                        .values(
                            status="failed",
                            phase="failed",
                            finished_at=datetime.now(UTC),
                            error_code=str(exc)
                            if isinstance(exc, IngestionError)
                            else type(exc).__name__,
                            error_summary="资料解析未发布，上一有效发布保留",
                        )
                    )
                raise
            finally:
                c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('pdd:m1:ingestion'))"))
                c.commit()

    def _publish(self, c, run_id):
        head = c.execute(
            sa.select(t.publication_heads.c.publication_id)
            .where(t.publication_heads.c.scope == "M1")
            .with_for_update()
        ).scalar_one_or_none()
        if not head:
            raise IngestionError("M1_SOURCE_NOT_PUBLISHED")
        versions = dict(
            c.execute(
                sa.select(
                    t.publication_datasets.c.dataset_id, t.publication_datasets.c.version_id
                ).where(t.publication_datasets.c.publication_id == head)
            ).all()
        )
        inputs = {k: str(versions[k]) for k in ("product_profile_source", "shop_identity")}
        source = c.execute(
            sa.select(t.dataset_versions.c.source_id).where(
                t.dataset_versions.c.version_id == versions["product_profile_source"]
            )
        ).scalar_one()
        records = (
            c.execute(
                sa.select(t.product_profile_records).where(
                    t.product_profile_records.c.source_id == source
                )
            )
            .mappings()
            .all()
        )
        mappings = (
            c.execute(
                sa.select(t.shop_source_mappings).where(
                    t.shop_source_mappings.c.mapping_version == versions["shop_identity"]
                )
            )
            .mappings()
            .all()
        )
        identities = {
            tuple(r)
            for r in c.execute(
                sa.select(*(t.shop_products.c[k] for k in t.K)).where(
                    t.shop_products.c.platform_code == "pdd"
                )
            )
        }
        candidates, links = defaultdict(list), []
        unresolved = 0
        for record in records:
            for mapping in mappings:
                if mapping["source_id"] != source:
                    continue
                column = mapping["source_shop_key"]
                raw = record["source_product_ids"].get(column)
                ids = parse_ids(raw)
                if raw and str(raw).strip() and not ids:
                    unresolved += 1
                for product_id in ids:
                    key = (mapping["platform_code"], mapping["shop_id"], product_id)
                    candidates[key].append(record)
                    links.append(
                        dict(
                            source_id=source,
                            source_profile_id=record["source_profile_id"],
                            source_column=column,
                            **dict(zip(t.K, key, strict=True)),
                        )
                    )
                    identities.add(key)
        profiles = [
            dict(
                zip(t.K, key, strict=True),
                **resolve_fields(candidates[key]),
                mapping_version=str(versions["shop_identity"]),
            )
            for key in sorted(identities)
        ]
        content_hash = digest({"rule": RULE_VERSION, "inputs": inputs, "profiles": profiles})
        prior = (
            c.execute(
                sa.select(t.dataset_versions).where(
                    t.dataset_versions.c.version_id == versions.get("product_profile")
                )
            )
            .mappings()
            .first()
        )
        if prior and prior["content_hash"] == content_hash:
            return dict(
                publication_id=str(head),
                changed=False,
                profiles=len(profiles),
                links=len(links),
                unresolved_cells=unresolved,
            )
        version = uuid4()
        c.execute(
            t.dataset_versions.insert().values(
                version_id=version,
                dataset_id="product_profile",
                source_id=SOURCE,
                sync_run_id=run_id,
                content_hash=content_hash,
                row_count=len(profiles),
                validation_summary={
                    "inputs": inputs,
                    "rule": RULE_VERSION,
                    "unresolved_cells": unresolved,
                    "field_status_counts": dict(
                        Counter(s for r in profiles for s in r["field_statuses"].values())
                    ),
                },
            )
        )
        for offset in range(0, len(profiles), 500):
            c.execute(
                insert(t.shop_products)
                .values([{k: r[k] for k in t.K} for r in profiles[offset : offset + 500]])
                .on_conflict_do_nothing(index_elements=list(t.K))
            )
        upsert(c, t.product_profile_links, links)
        for row in profiles:
            row.update(
                source_id=SOURCE,
                last_changed_version_id=version,
                last_seen_run_id=run_id,
                row_hash=digest(row),
            )
        upsert(c, t.product_profiles, profiles)
        publication = uuid4()
        previous = (
            c.execute(
                sa.select(t.data_publications).where(t.data_publications.c.publication_id == head)
            )
            .mappings()
            .one()
        )
        c.execute(
            t.data_publications.insert().values(
                publication_id=publication,
                scope="M1",
                rule_versions={**previous["rule_versions"], "profile_resolution": RULE_VERSION},
                rule_config=previous["rule_config"],
                implementation_version=RULE_VERSION,
            )
        )
        versions["product_profile"] = version
        c.execute(
            t.publication_datasets.insert(),
            [
                dict(publication_id=publication, dataset_id=k, version_id=v)
                for k, v in versions.items()
            ],
        )
        c.execute(
            t.publication_heads.update()
            .where(t.publication_heads.c.scope == "M1")
            .values(publication_id=publication)
        )
        return dict(
            publication_id=str(publication),
            changed=True,
            profiles=len(profiles),
            links=len(links),
            unresolved_cells=unresolved,
        )
