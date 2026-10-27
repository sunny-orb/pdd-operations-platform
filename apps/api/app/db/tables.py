"""M1基础表元数据；只注册结构，导入或启动服务都不执行DDL/同步。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base

metadata = Base.metadata
K = ("platform_code", "shop_id", "product_id")


def key(name: str) -> sa.Column:
    """创建不可空的文本标识；不把外部ID转数字。"""
    return sa.Column(name, sa.Text, nullable=False, info={"identifier": True})


def uid(name: str, *, primary: bool = False) -> sa.Column:
    """内部UUID可用数据库默认值；外部来源ID另用TEXT。"""
    return sa.Column(
        name,
        UUID(as_uuid=True),
        primary_key=primary,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary else None,
    )


def json_column(name: str, *, array: bool = False) -> sa.Column:
    """只用于结构化证据/状态；事实度量与关联键另列。"""
    value = "[]" if array else "{}"
    return sa.Column(
        name,
        JSONB,
        nullable=False,
        server_default=sa.text(f"'{value}'::jsonb"),
        info={"json_kind": "array" if array else "object"},
    )


def identities() -> list[sa.Column]:
    return [key(name) for name in K]


def product_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(K, [f"shop_products.{name}" for name in K])


def revision(dataset: str) -> list:
    """当前行绑定相符的数据集/来源版本与最近读取任务，不保存每批整份副本。"""
    return [
        key("source_id"),
        sa.Column("dataset_id", sa.Text, nullable=False, server_default=dataset),
        uid("last_changed_version_id"),
        uid("last_seen_run_id"),
        sa.Column("row_hash", sa.Text),
        sa.CheckConstraint(f"dataset_id = '{dataset}'", name="dataset_kind"),
        sa.ForeignKeyConstraint(
            ["last_changed_version_id", "dataset_id", "source_id"],
            [
                "dataset_versions.version_id",
                "dataset_versions.dataset_id",
                "dataset_versions.source_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_run_id", "dataset_id", "source_id"],
            ["sync_runs.run_id", "sync_runs.dataset_id", "sync_runs.source_id"],
        ),
    ]


def table(name: str, *items, comment: str) -> sa.Table:
    """统一有限数值、JSON形态与非空标识约束，不添加未确认业务阈值。"""
    constraints = []
    for item in items:
        if not isinstance(item, sa.Column):
            continue
        if item.info.get("identifier"):
            constraints.append(
                sa.CheckConstraint(f"btrim({item.name}) <> ''", name=f"{item.name}_not_blank")
            )
        if item.info.get("json_kind"):
            constraints.append(
                sa.CheckConstraint(
                    f"jsonb_typeof({item.name}) = '{item.info['json_kind']}'",
                    name=f"{item.name}_shape",
                )
            )
        if isinstance(item.type, sa.Numeric):
            constraints.append(
                sa.CheckConstraint(
                    f"{item.name} NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)",
                    name=f"{item.name}_finite",
                )
            )
    return sa.Table(name, metadata, *items, *constraints, comment=comment)


sync_runs = table(
    "sync_runs",
    uid("run_id", primary=True),
    key("dataset_id"),
    key("source_id"),
    sa.Column("status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("phase", sa.Text, nullable=False, server_default="pending"),
    json_column("requested_range"),
    json_column("validation_summary"),
    sa.Column("row_count", sa.BigInteger),
    json_column("watermark"),
    sa.Column(
        "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("error_code", sa.Text),
    sa.Column("error_summary", sa.Text),
    sa.UniqueConstraint("run_id", "dataset_id", "source_id"),
    sa.CheckConstraint(
        "status IN ('pending','running','succeeded','failed','cancelled')", name="status_values"
    ),
    sa.CheckConstraint("row_count >= 0", name="row_count_nonnegative"),
    sa.CheckConstraint("finished_at >= started_at", name="time_order"),
    comment="同步尝试与阶段；不因建表生成成功时间或水位。",
)

dataset_versions = table(
    "dataset_versions",
    uid("version_id", primary=True),
    key("dataset_id"),
    key("source_id"),
    uid("sync_run_id"),
    sa.Column("source_batch_id", sa.Text),
    sa.Column("schema_hash", sa.Text),
    sa.Column("projection_hash", sa.Text),
    sa.Column("content_hash", sa.Text),
    sa.Column("row_count", sa.BigInteger),
    json_column("requested_range"),
    json_column("validation_summary"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("version_id", "dataset_id", name="uq_dataset_versions_version_dataset"),
    sa.UniqueConstraint(
        "version_id", "dataset_id", "source_id", name="uq_dataset_versions_version_dataset_source"
    ),
    sa.ForeignKeyConstraint(
        ["sync_run_id", "dataset_id", "source_id"],
        ["sync_runs.run_id", "sync_runs.dataset_id", "sync_runs.source_id"],
    ),
    sa.CheckConstraint("row_count >= 0", name="row_count_nonnegative"),
    comment="输入修订元信息；不存每批完整经营副本。",
)

shops = table(
    "shops",
    key("platform_code"),
    key("shop_id"),
    sa.Column("shop_name", sa.Text),
    sa.PrimaryKeyConstraint("platform_code", "shop_id"),
    comment="全项目店铺身份；名称不作唯一键。",
)
shop_products = table(
    "shop_products",
    *identities(),
    sa.Column("source_product_name", sa.Text),
    sa.Column("first_observed_date", sa.Date),
    sa.Column("last_observed_date", sa.Date),
    sa.PrimaryKeyConstraint(*K),
    sa.ForeignKeyConstraint(["platform_code", "shop_id"], ["shops.platform_code", "shops.shop_id"]),
    sa.CheckConstraint("last_observed_date >= first_observed_date", name="observed_order"),
    comment="店铺商品身份；首次观察不是实际上架日。",
)
shop_source_mappings = table(
    "shop_source_mappings",
    key("source_id"),
    key("source_shop_key"),
    key("platform_code"),
    key("shop_id"),
    uid("mapping_version"),
    sa.Column("mapping_dataset_id", sa.Text, nullable=False, server_default="shop_identity"),
    json_column("evidence"),
    sa.PrimaryKeyConstraint("source_id", "source_shop_key"),
    sa.UniqueConstraint("source_id", "source_shop_key", "platform_code", "shop_id"),
    sa.ForeignKeyConstraint(["platform_code", "shop_id"], ["shops.platform_code", "shops.shop_id"]),
    sa.ForeignKeyConstraint(
        ["mapping_version", "mapping_dataset_id"],
        ["dataset_versions.version_id", "dataset_versions.dataset_id"],
    ),
    sa.CheckConstraint("mapping_dataset_id='shop_identity'", name="mapping_dataset"),
    comment="当前来源店铺/资料列映射；不伪造历史有效期。",
)
product_profile_records = table(
    "product_profile_records",
    key("source_profile_id"),
    sa.Column("source_style_code", sa.Text),
    sa.Column("product_name", sa.Text),
    sa.Column("category", sa.Text),
    sa.Column("level", sa.Text),
    sa.Column("operator", sa.Text),
    sa.Column("source_listed_date", sa.Text),
    sa.Column("source_listing_status", sa.Text),
    json_column("source_product_ids"),
    *revision("product_profile_source"),
    sa.PrimaryKeyConstraint("source_id", "source_profile_id"),
    comment="当前必要原资料及六列ID原串；不存无关图片/成本字段。",
)
product_profile_links = table(
    "product_profile_links",
    key("source_id"),
    key("source_profile_id"),
    key("source_column"),
    *identities(),
    sa.PrimaryKeyConstraint("source_id", "source_profile_id", "source_column", "product_id"),
    sa.ForeignKeyConstraint(
        ["source_id", "source_profile_id"],
        ["product_profile_records.source_id", "product_profile_records.source_profile_id"],
    ),
    sa.ForeignKeyConstraint(
        ["source_id", "source_column", "platform_code", "shop_id"],
        [
            "shop_source_mappings.source_id",
            "shop_source_mappings.source_shop_key",
            "shop_source_mappings.platform_code",
            "shop_source_mappings.shop_id",
        ],
    ),
    product_fk(),
    comment="资料到店铺商品的多对多关系，禁止直接展开后求和。",
)
product_profiles = table(
    "product_profiles",
    *identities(),
    sa.Column("product_name", sa.Text),
    sa.Column("category", sa.Text),
    sa.Column("level", sa.Text),
    sa.Column("operator", sa.Text),
    sa.Column("listed_date", sa.Date),
    sa.Column("profile_match_status", sa.Text, nullable=False, server_default="unmatched"),
    json_column("field_statuses"),
    json_column("field_candidates"),
    json_column("field_evidence"),
    sa.Column("mapping_version", UUID(as_uuid=True)),
    sa.Column("mapping_dataset_id", sa.Text, nullable=False, server_default="shop_identity"),
    sa.ForeignKeyConstraint(
        ["mapping_version", "mapping_dataset_id"],
        ["dataset_versions.version_id", "dataset_versions.dataset_id"],
    ),
    sa.CheckConstraint("mapping_dataset_id='shop_identity'", name="mapping_dataset"),
    *revision("product_profile"),
    sa.PrimaryKeyConstraint(*K),
    product_fk(),
    sa.CheckConstraint(
        "profile_match_status IN ('matched','partial','unmatched','conflict')",
        name="match_status_values",
    ),
    comment="每店铺商品最多一行当前解析资料；缺项/冲突不任取首末行。",
)

product_days = table(
    "product_days",
    *identities(),
    sa.Column("business_date", sa.Date, nullable=False),
    sa.Column("source_window", sa.Text, nullable=False, server_default="day"),
    key("source_record_id"),
    sa.Column("source_dt", sa.DateTime, nullable=False),
    sa.Column("source_relevant_dt", sa.DateTime),
    sa.Column("source_created_at", sa.DateTime),
    sa.Column("date_basis", sa.Text, nullable=False, server_default="source_dt_day"),
    sa.Column("source_product_name", sa.Text),
    sa.Column("source_shop_name", sa.Text),
    *[
        sa.Column(name, sa.BigInteger)
        for name in ("visitors", "views", "favorites", "buyers", "orders", "units")
    ],
    sa.Column("product_revenue", sa.Numeric),
    sa.Column("source_conversion_rate", sa.Numeric),
    json_column("source_numeric_text"),
    json_column("quality_flags", array=True),
    *revision("product_daily"),
    sa.PrimaryKeyConstraint(*K, "business_date"),
    product_fk(),
    sa.CheckConstraint("source_window='day'", name="day_window"),
    sa.CheckConstraint("source_dt::date=business_date", name="source_date"),
    sa.CheckConstraint("date_basis='source_dt_day'", name="date_basis_value"),
    comment="商品日原值；不额外扣退款/做单，不默认零，不限制买家≤访客。",
)
sa.Index(
    "ix_product_days_date_shop_product",
    product_days.c.business_date,
    product_days.c.shop_id,
    product_days.c.product_id,
)

promotion_records = table(
    "promotion_records",
    key("source_record_id"),
    *identities(),
    sa.Column("business_date", sa.Date, nullable=False),
    sa.Column("source_window", sa.Text, nullable=False, server_default="day"),
    sa.Column("source_dt", sa.DateTime, nullable=False),
    sa.Column("source_start_dt", sa.DateTime, nullable=False),
    sa.Column("source_created_at", sa.DateTime),
    sa.Column("source_product_name", sa.Text),
    sa.Column("source_shop_name", sa.Text),
    sa.Column("promotion_name", sa.Text),
    sa.Column("spu_grouping", sa.Text),
    sa.Column("promotion_type", sa.Text),
    sa.Column("bidding_method", sa.Text),
    sa.Column("is_deleted", sa.Text),
    *[
        sa.Column(name, sa.Numeric)
        for name in (
            "spend",
            "transaction_spend",
            "attributed_revenue",
            "attributed_deals",
            "net_attributed_revenue",
            "net_attributed_deals",
            "settled_attributed_revenue",
            "settled_attributed_deals",
            "impressions",
            "clicks",
            "source_roi",
            "source_net_roi",
            "source_settlement_roi",
            "source_click_through_rate",
            "source_click_conversion_rate",
        )
    ],
    json_column("source_numeric_text"),
    json_column("quality_flags", array=True),
    *revision("promotion_records"),
    sa.PrimaryKeyConstraint("source_id", "source_record_id"),
    product_fk(),
    sa.CheckConstraint("source_window='day'", name="day_window"),
    sa.CheckConstraint(
        "source_dt::date=business_date AND source_start_dt::date=business_date", name="source_date"
    ),
    comment="推广来源记录；不同推广方式保留，合计分子分母后算率；已删除标签不删事实。",
)
sa.Index(
    "ix_promotion_records_date_shop_product",
    promotion_records.c.business_date,
    promotion_records.c.shop_id,
    promotion_records.c.product_id,
)

for name, dataset in (
    ("stg_product_records", "product_daily"),
    ("stg_profile_records", "product_profile_source"),
    ("stg_promotion_records", "promotion_records"),
):
    table(
        name,
        uid("sync_run_id"),
        sa.Column("row_no", sa.BigInteger, nullable=False),
        key("source_id"),
        sa.Column("dataset_id", sa.Text, nullable=False, server_default=dataset),
        sa.Column("source_record_id", sa.Text),
        json_column("payload"),
        sa.Column("row_hash", sa.Text),
        sa.Column("validation_status", sa.Text, nullable=False, server_default="pending"),
        json_column("validation_errors", array=True),
        sa.PrimaryKeyConstraint("sync_run_id", "row_no"),
        sa.ForeignKeyConstraint(
            ["sync_run_id", "dataset_id", "source_id"],
            ["sync_runs.run_id", "sync_runs.dataset_id", "sync_runs.source_id"],
        ),
        sa.CheckConstraint(f"dataset_id='{dataset}'", name="dataset_kind"),
        sa.CheckConstraint("row_no>0", name="row_number_positive"),
        sa.CheckConstraint(
            "validation_status IN ('pending','valid','invalid')", name="status_values"
        ),
        comment="单来源必要投影暂存；不丢重复/冲突行，不作为页面数据源。",
    )

dataset_coverage = table(
    "dataset_coverage",
    uid("version_id"),
    key("platform_code"),
    key("shop_id"),
    sa.Column("window_from", sa.Date, nullable=False),
    sa.Column("window_to", sa.Date, nullable=False),
    sa.Column("observed_days", sa.Integer),
    sa.Column("complete_through", sa.Date),
    sa.Column("completeness", sa.Text, nullable=False, server_default="unknown"),
    json_column("missing_dates", array=True),
    json_column("evidence"),
    sa.PrimaryKeyConstraint("version_id", "platform_code", "shop_id", "window_from", "window_to"),
    sa.ForeignKeyConstraint(["version_id"], ["dataset_versions.version_id"]),
    sa.ForeignKeyConstraint(["platform_code", "shop_id"], ["shops.platform_code", "shops.shop_id"]),
    sa.CheckConstraint("window_to>=window_from", name="window_order"),
    sa.CheckConstraint(
        "observed_days>=0 AND observed_days<=window_to-window_from+1", name="observed_days_range"
    ),
    sa.CheckConstraint(
        "completeness IN ('unknown','observed','partial','complete')", name="completeness_values"
    ),
    sa.CheckConstraint("complete_through<=window_to", name="complete_date_limit"),
    sa.CheckConstraint(
        "completeness<>'complete' OR (complete_through IS NOT NULL "
        'AND complete_through=window_to)',
        name="complete_requires_date",
    ),
    comment="实际覆盖与来源完整性证据；观察日期不自动等于完整日。",
)
data_publications = table(
    "data_publications",
    uid("publication_id", primary=True),
    key("scope"),
    json_column("rule_versions"),
    json_column("rule_config"),
    sa.Column("implementation_version", sa.Text),
    sa.Column(
        "published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("publication_id", "scope"),
    comment="发布清单；不存历史事实副本，旧ID不保证旧事实可查询。",
)
publication_datasets = table(
    "publication_datasets",
    uid("publication_id"),
    key("dataset_id"),
    uid("version_id"),
    sa.PrimaryKeyConstraint("publication_id", "dataset_id"),
    sa.ForeignKeyConstraint(["publication_id"], ["data_publications.publication_id"]),
    sa.ForeignKeyConstraint(
        ["version_id", "dataset_id"], ["dataset_versions.version_id", "dataset_versions.dataset_id"]
    ),
    comment="发布所用相容数据集版本；不能误指另一个数据集的批次。",
)
publication_heads = table(
    "publication_heads",
    key("scope"),
    uid("publication_id"),
    sa.PrimaryKeyConstraint("scope"),
    sa.ForeignKeyConstraint(
        ["publication_id", "scope"], ["data_publications.publication_id", "data_publications.scope"]
    ),
    comment="当前发布指针；后续发布任务须同事务锁定并更新事实与指针。",
)

model_versions = table(
    "model_versions",
    uid("model_id", primary=True),
    key("rule_version"),
    key("content_hash"),
    key("status"),
    json_column("parameters"),
    json_column("provenance"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("content_hash"),
    sa.CheckConstraint("status IN ('fixed_display_trial','validated')", name="status_values"),
    comment="不可变评分参数版本；只存标准、适用身份和出处，不导入旧评分或全量事实。",
)

product_scores = table(
    "product_scores",
    uid("score_id", primary=True),
    uid("result_version_id"),
    uid("model_id"),
    *identities(),
    sa.Column("as_of_date", sa.Date, nullable=False),
    key("score_status"),
    sa.Column("verified_score", sa.Numeric),
    sa.Column("complete_score", sa.Numeric),
    sa.Column("verified_weight", sa.Integer, nullable=False),
    sa.Column("pending_weight", sa.Integer, nullable=False),
    sa.Column("active_signal_count", sa.Integer, nullable=False),
    sa.Column("pending_signal_count", sa.Integer, nullable=False),
    sa.Column("met_signal_count", sa.Integer, nullable=False),
    key("decision_code"),
    json_column("decision"),
    json_column("inputs"),
    json_column("context"),
    sa.UniqueConstraint("result_version_id", *K),
    sa.ForeignKeyConstraint(["result_version_id"], ["dataset_versions.version_id"]),
    sa.ForeignKeyConstraint(["model_id"], ["model_versions.model_id"]),
    product_fk(),
    sa.CheckConstraint("score_status IN ('complete','partial','unscored')", name="status_values"),
    sa.CheckConstraint(
        "verified_weight>=0 AND pending_weight>=0 AND verified_weight+pending_weight=100",
        name="weight_sum",
    ),
    sa.CheckConstraint(
        'active_signal_count>=0 AND pending_signal_count>=0 AND '
        'active_signal_count+pending_signal_count=10 AND '
        'met_signal_count BETWEEN 0 AND active_signal_count',
        name="signal_counts",
    ),
    sa.CheckConstraint(
        'verified_score>=0 AND verified_score<=verified_weight AND '
        'complete_score BETWEEN 0 AND 100',
        name="score_range",
    ),
    sa.CheckConstraint(
        "(score_status='unscored' AND verified_score IS NULL AND "
        "active_signal_count=0) OR (score_status='partial' AND "
        'verified_score IS NOT NULL AND active_signal_count BETWEEN 1'
        " AND 9) OR (score_status='complete' AND verified_score IS "
        'NOT NULL AND active_signal_count=10)',
        name="score_state",
    ),
    sa.CheckConstraint(
        "(score_status='complete' AND pending_weight=0 AND "
        "complete_score=verified_score) OR (score_status<>'complete' "
        'AND complete_score IS NULL)',
        name="complete_score_state",
    ),
    comment="评分结果与当次必要聚合证据；绑定输入和模型，不复制历史逐日事实。",
)
sa.Index(
    "ix_product_scores_version_status_score",
    product_scores.c.result_version_id,
    product_scores.c.score_status,
    product_scores.c.verified_score,
)

score_metrics = table(
    "score_metrics",
    uid("score_id"),
    key("metric_key"),
    key("label"),
    key("unit"),
    sa.Column("weight", sa.Integer, nullable=False),
    sa.Column("current_value", sa.Numeric),
    *[
        sa.Column(name, sa.Numeric)
        for name in ("basic", "good", "excellent", "outstanding", "points")
    ],
    sa.Column("tier", sa.Text),
    key("status"),
    json_column("missing_reasons", array=True),
    sa.Column("reference_group", sa.Text),
    sa.Column("reference_sample_count", sa.Integer),
    sa.Column("fallback_level", sa.Text),
    sa.PrimaryKeyConstraint("score_id", "metric_key"),
    sa.ForeignKeyConstraint(["score_id"], ["product_scores.score_id"]),
    sa.CheckConstraint(
        "weight>0 AND weight<=100 AND points BETWEEN 0 AND weight", name="points_range"
    ),
    sa.CheckConstraint(
        "status IN ('valid','partial','missing','not_computable')", name="status_values"
    ),
    sa.CheckConstraint(
        "(status='valid' AND points IS NOT NULL AND current_value IS "
        "NOT NULL AND tier IS NOT NULL) OR (status<>'valid' AND "
        'points IS NULL AND tier IS NULL)',
        name="points_state",
    ),
    comment="每次评分十项值、实际四线与判分；缺项NULL，不重分权重。",
)
