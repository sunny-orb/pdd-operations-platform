"""评分后台发布与只读查询；评分版本独立，不重发经营事实。"""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from app.db import tables as t
from app.modules.products.ingestion import IngestionError, digest, serializable
from app.modules.products.query import QueryError
from app.modules.products.repository import ProductRepository, ProductView
from app.modules.products.score_inputs import candidates, read_inputs
from app.modules.products.score_standards import latest_model
from app.modules.products.scoring import LABELS, METRICS, RULE_VERSION, TIERS, score_product

DATASET, SOURCE = "product_scores", "m1_score_engine"
DEPENDENCIES = (
    "product_daily",
    "promotion_records",
    "product_profile_source",
    "shop_identity",
    "product_profile",
)


def dependencies(view):
    return {k: str(view.versions[k]["version_id"]) for k in DEPENDENCIES}


def fingerprint(view, model, as_of):
    return digest(
        dict(
            inputs=dependencies(view), model=str(model["model_id"]), rule=RULE_VERSION, as_of=as_of
        )
    )


class ScoreRepository:
    """计算在一致只读快照中完成；发布前重新核对依赖，变化则作废本次。"""

    def __init__(self, engine):
        self.engine = engine

    def run(self, as_of=None, *, before_publish=None):
        run_id = uuid4()
        with self.engine.connect() as lock:
            if not lock.scalar(sa.text("SELECT pg_try_advisory_lock(hashtext('pdd:m1:scoring'))")):
                lock.rollback()
                raise IngestionError("SCORING_ALREADY_RUNNING")
            lock.commit()
            try:
                with lock.begin():
                    # 独占锁证明没有仍存活的同任务持有者；收敛上次进程中断状态。
                    lock.execute(
                        t.sync_runs.update()
                        .where(
                            t.sync_runs.c.dataset_id == DATASET,
                            t.sync_runs.c.source_id == SOURCE,
                            t.sync_runs.c.status.in_(["running", "pending"]),
                        )
                        .values(
                            status="failed",
                            phase="interrupted",
                            finished_at=datetime.now(UTC),
                            error_code="INTERRUPTED",
                            error_summary="评分进程已中断；上一有效发布保留",
                        )
                    )
                    lock.execute(
                        t.sync_runs.insert().values(
                            run_id=run_id,
                            dataset_id=DATASET,
                            source_id=SOURCE,
                            status="running",
                            phase="compute",
                        )
                    )
                with ProductRepository(self.engine).snapshot() as view:
                    model = latest_model(view.connection)
                    if not model:
                        raise IngestionError("SCORE_MODEL_NOT_IMPORTED")
                    day = as_of or view.latest
                    if day > view.latest:
                        raise IngestionError("SCORE_DATE_NOT_RECEIVED")
                    inputs = dependencies(view)
                    token = fingerprint(view, model, day)
                    prepared = read_inputs(view, model, day)
                    for item in prepared:
                        item["result"] = score_product(
                            item["inputs"]["values"],
                            item["standard"],
                            blocked=item["inputs"]["blocked"],
                            evidence_reasons=item["context"]["evidence_reasons"],
                        )
                if before_publish:
                    before_publish()
                with lock.begin():
                    # 与标准导入互斥；经营发布行锁只覆盖核对和写入，不阻塞计算期接数。
                    lock.execute(
                        sa.text(
                            "SELECT pg_advisory_xact_lock(hashtext('pdd:m1:score-publication'))"
                        )
                    )
                    lock.execute(
                        sa.select(t.publication_heads)
                        .where(t.publication_heads.c.scope == "M1")
                        .with_for_update()
                    ).one()
                    current = ProductView(lock, None)
                    active_model = latest_model(lock)
                    if (
                        dependencies(current) != inputs
                        or active_model["model_id"] != model["model_id"]
                    ):
                        self._finish(
                            lock, run_id, "failed", "stale", len(prepared), "STALE_SCORE_INPUTS"
                        )
                        return dict(status="stale", changed=False, products=len(prepared))
                    old = score_publication(lock)
                    if old and old["content_hash"] == token:
                        self._finish(lock, run_id, "succeeded", "unchanged", len(prepared))
                        return dict(
                            status="succeeded",
                            changed=False,
                            products=len(prepared),
                            score_version=token,
                        )
                    existing = (
                        lock.execute(
                            sa.select(t.dataset_versions.c.version_id).where(
                                t.dataset_versions.c.dataset_id == DATASET,
                                t.dataset_versions.c.source_id == SOURCE,
                                t.dataset_versions.c.content_hash == token,
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if len(existing) > 1:
                        raise IngestionError("DUPLICATE_SCORE_FINGERPRINT")
                    reused = bool(existing)
                    version, publication = existing[0] if reused else uuid4(), uuid4()
                    if not reused:
                        lock.execute(
                            t.dataset_versions.insert().values(
                                version_id=version,
                                dataset_id=DATASET,
                                source_id=SOURCE,
                                sync_run_id=run_id,
                                content_hash=token,
                                row_count=len(prepared),
                                validation_summary=dict(
                                    inputs=inputs,
                                    model_id=str(model["model_id"]),
                                    rule=RULE_VERSION,
                                    as_of_date=str(day),
                                ),
                            )
                        )
                        self._write(lock, version, model, day, prepared)
                    lock.execute(
                        t.data_publications.insert().values(
                            publication_id=publication,
                            scope="M1_scores",
                            rule_versions={"scoring": RULE_VERSION},
                            rule_config=dict(model_id=str(model["model_id"]), as_of_date=str(day)),
                            implementation_version=RULE_VERSION,
                        )
                    )
                    lock.execute(
                        t.publication_datasets.insert(),
                        [
                            dict(publication_id=publication, dataset_id=k, version_id=v)
                            for k, v in {**inputs, DATASET: version}.items()
                        ],
                    )
                    lock.execute(
                        insert(t.publication_heads)
                        .values(scope="M1_scores", publication_id=publication)
                        .on_conflict_do_update(
                            index_elements=["scope"], set_={"publication_id": publication}
                        )
                    )
                    self._finish(
                        lock,
                        run_id,
                        "succeeded",
                        "reused" if reused else "published",
                        len(prepared),
                    )
                    return dict(
                        status="succeeded",
                        changed=True,
                        products=len(prepared),
                        score_version=token,
                        reused_result=reused,
                    )
            except BaseException as exc:
                if lock.in_transaction():
                    lock.rollback()
                with lock.begin():
                    self._finish(
                        lock,
                        run_id,
                        "failed",
                        "failed",
                        None,
                        str(exc) if isinstance(exc, IngestionError) else type(exc).__name__,
                    )
                raise
            finally:
                lock.execute(sa.text("SELECT pg_advisory_unlock(hashtext('pdd:m1:scoring'))"))
                lock.commit()

    def _finish(self, c, run_id, status, phase, count, error=None):
        c.execute(
            t.sync_runs.update()
            .where(t.sync_runs.c.run_id == run_id)
            .values(
                status=status,
                phase=phase,
                finished_at=datetime.now(UTC),
                row_count=count,
                error_code=error,
                error_summary="评分未发布，上一有效结果保留" if error else None,
            )
        )

    def _write(self, c, version, model, day, prepared):
        """同事务持久化卡片及十项证据；该方法失败时整个候选发布回滚。"""
        rows, metrics = [], []
        fields = (
            "score_status",
            "verified_score",
            "complete_score",
            "verified_weight",
            "pending_weight",
            "active_signal_count",
            "pending_signal_count",
            "met_signal_count",
            "decision",
        )
        for item in prepared:
            score_id = uuid4()
            result = item["result"]
            rows.append(
                dict(
                    score_id=score_id,
                    result_version_id=version,
                    model_id=model["model_id"],
                    as_of_date=day,
                    **dict(zip(t.K, item["key"], strict=True)),
                    **{k: result[k] for k in fields},
                    decision_code=result["decision"]["code"],
                    inputs=serializable(item["inputs"]),
                    context=serializable(item["context"]),
                )
            )
            for signal in result["signals"]:
                signal = dict(signal)
                thresholds = signal.pop("thresholds") or {}
                metrics.append(
                    dict(score_id=score_id, **signal, **{x: thresholds.get(x) for x in TIERS})
                )
        if rows:
            c.execute(t.product_scores.insert(), rows)
        if metrics:
            c.execute(t.score_metrics.insert(), metrics)

    @contextmanager
    def snapshot(self, query):
        with ProductRepository(self.engine).snapshot(query.data_version) as view:
            yield ScoreView(view, query)


def score_publication(c):
    return (
        c.execute(
            sa.select(t.dataset_versions)
            .join(
                t.publication_datasets,
                t.publication_datasets.c.version_id == t.dataset_versions.c.version_id,
            )
            .join(
                t.publication_heads,
                t.publication_heads.c.publication_id == t.publication_datasets.c.publication_id,
            )
            .where(
                t.publication_heads.c.scope == "M1_scores",
                t.dataset_versions.c.dataset_id == DATASET,
            )
        )
        .mappings()
        .first()
    )


class ScoreView:
    def __init__(self, view, query):
        self.view, self.query, self.c = view, query, view.connection
        self.model = latest_model(self.c)
        self.published = score_publication(self.c)
        self.as_of = view.latest
        # API默认取当前已发布评分日；不受总览日期影响，不按GET触发重算。
        if self.published:
            self.as_of = datetime.fromisoformat(
                self.published["validation_summary"]["as_of_date"]
            ).date()
        if query.as_of_date and query.as_of_date != self.as_of:
            raise QueryError(
                409, "SCORE_DATE_NOT_PUBLISHED", "该评分日期尚未发布，请使用当前评分日期"
            )
        self.token = fingerprint(view, self.model, self.as_of) if self.model else "not_configured"
        if query.score_version and query.score_version != self.token:
            raise QueryError(409, "SCORE_VERSION_CHANGED", "评分输入或标准已变化，请重新查询评分区")
        self.compatible = bool(
            self.published and self.model and self.published["content_hash"] == self.token
        )
        self.latest_run = (
            self.c.execute(
                sa.select(t.sync_runs)
                .where(t.sync_runs.c.dataset_id == DATASET, t.sync_runs.c.source_id == SOURCE)
                .order_by(t.sync_runs.c.started_at.desc(), t.sync_runs.c.run_id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )

    def predicates(self):
        p = t.product_profiles.c
        q = self.query
        result = [
            p.level == "新品",
            p.platform_code == "pdd",
            p.last_changed_version_id == self.view.versions["product_profile"]["version_id"],
        ]
        if q.shop_id:
            result.append(p.shop_id == q.shop_id)
        if q.product_id:
            result.append(p.product_id == q.product_id)
        if q.product_key:
            result.append(
                sa.tuple_(*(p[k] for k in t.K)).in_([tuple(k.split(":")) for k in q.product_key])
            )
        for field in ("operator", "category"):
            value = getattr(q, field)
            if value is not None:
                result.append(
                    p[field].is_(None) if value == "__unassigned__" else p[field] == value
                )
        if q.keyword:
            result.append(
                sa.or_(
                    sa.func.coalesce(
                        p.product_name, t.shop_products.c.source_product_name, ""
                    ).icontains(q.keyword, autoescape=True),
                    p.product_id.icontains(q.keyword, autoescape=True),
                )
            )
        return result

    def source(self):
        p, s = t.product_profiles.c, t.shop_products.c
        return t.product_profiles.join(t.shop_products, sa.and_(*(p[k] == s[k] for k in t.K)))

    def list(self):
        total = len(candidates(self.view))
        summary = dict(
            total_count=total,
            filtered_count=0,
            scored_count=0,
            complete_count=0,
            partial_count=0,
            unscored_count=0,
        )
        items = []
        if self.compatible:
            scores = t.product_scores
            joined = self.source().join(
                scores, sa.and_(*(scores.c[k] == t.product_profiles.c[k] for k in t.K))
            )
            pred = [*self.predicates(), scores.c.result_version_id == self.published["version_id"]]
            if self.query.decision:
                pred.append(scores.c.decision_code == self.query.decision)
            counts = self.c.execute(
                sa.select(scores.c.score_status, sa.func.count())
                .select_from(joined)
                .where(*pred)
                .group_by(scores.c.score_status)
            ).all()
            for status, count in counts:
                summary[status + "_count"] = count
            summary["scored_count"] = summary["complete_count"] + summary["partial_count"]
            summary["filtered_count"] = sum(
                summary[s + "_count"] for s in ("complete", "partial", "unscored")
            )
            stmt = (
                sa.select(scores)
                .select_from(joined)
                .where(*pred)
                .order_by(
                    sa.case(
                        (scores.c.score_status == "complete", 0),
                        (scores.c.score_status == "partial", 1),
                        else_=2,
                    ),
                    scores.c.verified_score.desc().nulls_last(),
                    scores.c.shop_id,
                    scores.c.product_id,
                )
                .offset((self.query.page - 1) * self.query.page_size)
                .limit(self.query.page_size)
            )
            items = [self.card(dict(r)) for r in self.c.execute(stmt).mappings()]
        return dict(
            items=items,
            summary=summary,
            total=summary["filtered_count"],
            page=self.query.page,
            page_size=self.query.page_size,
            as_of_date=self.as_of,
        )

    def card(self, row):
        profile = row["context"]["profile"]
        return {
            **{
                k: row[k]
                for k in (
                    "score_id",
                    *t.K,
                    "as_of_date",
                    "score_status",
                    "verified_score",
                    "complete_score",
                    "verified_weight",
                    "pending_weight",
                    "active_signal_count",
                    "pending_signal_count",
                    "met_signal_count",
                    "decision",
                )
            },
            "score_id": str(row["score_id"]),
            **{
                k: profile.get(k)
                for k in (
                    "category",
                    "operator",
                    "shop_name",
                    "profile_match_status",
                    "field_statuses",
                )
            },
            "product_name": profile.get("product_name")
            or profile.get("source_product_name")
            or row["product_id"],
            "current_values": row["inputs"]["values"],
            "ad_impressions_7d": row["inputs"]["promotion"].get("impressions"),
            "ad_spend_7d": row["inputs"]["promotion"].get("spend"),
            "model_category": row["context"]["model_category"],
            "season": row["context"]["season"],
            "lifecycle": row["context"]["lifecycle"],
            "reference_context_basis": row["context"]["reference_context_basis"],
            "threshold_status": row["context"]["threshold_status"],
        }

    def detail(self, score_id):
        row = (
            self.c.execute(
                sa.select(t.product_scores).where(t.product_scores.c.score_id == score_id)
            )
            .mappings()
            .first()
        )
        if not row:
            raise QueryError(404, "SCORE_NOT_FOUND", "评分结果不存在")
        if not self.compatible or row["result_version_id"] != self.published["version_id"]:
            raise QueryError(409, "SCORE_VERSION_CHANGED", "该评分输入已变化，请重新查询评分区")
        signals = []
        by_key = {
            r["metric_key"]: dict(r)
            for r in self.c.execute(
                sa.select(t.score_metrics).where(t.score_metrics.c.score_id == score_id)
            ).mappings()
        }
        for metric in METRICS:
            signal = by_key[metric]
            signal.pop("score_id")
            signal["thresholds"] = {x: signal.pop(x) for x in TIERS}
            signals.append(signal)
        return dict(
            **self.card(row),
            signals=signals,
            inputs=row["inputs"],
            context=row["context"],
            model_id=str(row["model_id"]),
            rule_version=RULE_VERSION,
            threshold_provenance=self.model["provenance"],
        )

    def filters(self):
        p = t.product_profiles.c
        pred = self.predicates()
        if self.query.decision:
            if not self.compatible:
                pred.append(sa.false())
            else:
                s = t.product_scores.c
                pred.append(
                    sa.exists().where(
                        *(s[k] == p[k] for k in t.K),
                        s.result_version_id == self.published["version_id"],
                        s.decision_code == self.query.decision,
                    )
                )
        rows = [
            dict(r)
            for r in self.c.execute(
                sa.select(t.product_profiles, t.shop_products.c.source_product_name)
                .select_from(self.source())
                .where(*pred)
                .order_by(p.shop_id, p.product_id)
            ).mappings()
        ]
        # 选项小集合只含当前新品资料，不向前端交付经营事实。
        all_rows = candidates(self.view)
        return dict(
            scope="scores",
            as_of_date=self.as_of,
            shops=sorted({r["shop_id"] for r in all_rows}),
            operators=sorted({r["operator"] or "__unassigned__" for r in all_rows}),
            categories=sorted({r["category"] or "__unassigned__" for r in all_rows}),
            decisions=[dict(code=k, label=v) for k, v in LABELS.items()],
            products=[
                dict(
                    product_key=":".join(r[k] for k in t.K),
                    shop_id=r["shop_id"],
                    product_id=r["product_id"],
                    product_name=r["product_name"] or r["source_product_name"] or r["product_id"],
                )
                for r in rows[
                    (self.query.page - 1) * self.query.page_size : self.query.page
                    * self.query.page_size
                ]
            ],
            product_total=len(rows),
            page=self.query.page,
            page_size=self.query.page_size,
        )

    def metadata(self):
        state = "ready" if self.compatible else "pending"
        if (
            not self.compatible
            and self.latest_run
            and self.latest_run["phase"] != "stale"
            and self.latest_run["status"] == "failed"
        ):
            state = "failed"
        if self.latest_run and self.latest_run["status"] == "running":
            state = "running"
        return dict(
            data_version=self.view.token,
            score_version=self.token,
            previous_score_version=self.published["content_hash"]
            if self.published and not self.compatible
            else None,
            input_batches=dependencies(self.view),
            model_id=str(self.model["model_id"]) if self.model else None,
            rule_version=RULE_VERSION,
            model_status=self.model["status"] if self.model else "not_configured",
            as_of_date=self.as_of,
            complete_through=None,
            completeness="unknown",
            profile_basis="current",
            computation_state=state,
            missing_reasons=[]
            if self.compatible
            else [
                "score_model_not_configured"
                if not self.model
                else "no_compatible_score_publication"
            ],
            has_compatible_result=self.compatible,
            computed_at=self.published["created_at"] if self.compatible else None,
            last_attempt_at=self.latest_run["started_at"] if self.latest_run else None,
            last_attempt_status=self.latest_run["status"] if self.latest_run else "not_started",
            last_error=dict(
                code=self.latest_run["error_code"], message=self.latest_run["error_summary"]
            )
            if self.latest_run and self.latest_run["status"] == "failed"
            else None,
            source_update_basis="warehouse_webpage",
            source_updated_at=None,
            source_update_status="not_recorded_for_current_publication",
            is_preview=False,
        )
