"""冻结01/04行约束及全表通用键/数值约束；只构造元数据，不访问数据库。

跨表归属、输入无环、内容封存、事件转换、来源范围与完整性证明由未来
不可绕过的受控发布器校验；静态CHECK不能代替这些验证。
"""

import sqlalchemy as sa

from .helpers import check, enum, index, unique


def _text(metadata: sa.MetaData, name: str, column: str, *, trimmed: bool = False) -> None:
    """允许列自身的NULL规则生效；有值时非空白，身份还须无首尾空白。"""
    expression = f"btrim({column}) <> ''"
    if trimmed:
        expression += f" AND {column} = btrim({column})"
    check(metadata, name, f"{column}_core_text", f"{column} IS NULL OR ({expression})")


def _pair(metadata: sa.MetaData, name: str, start: str, end: str) -> None:
    check(
        metadata,
        name,
        f"{start}_{end}_core_pair",
        f"({start} IS NULL AND {end} IS NULL) OR "
        f"({start} IS NOT NULL AND {end} IS NOT NULL AND {start} <= {end})",
    )


def _common(metadata: sa.MetaData) -> None:
    """30表PK/FK/原行键规范及所有NUMERIC有限性；原始来源文本不做清洗。"""
    for target in metadata.tables.values():
        for column in target.columns:
            if isinstance(column.type, sa.Text) and (
                column.primary_key or column.foreign_keys or column.name == "source_row_key"
            ):
                _text(metadata, target.name, column.name, trimmed=True)
            if isinstance(column.type, sa.Numeric):
                check(
                    metadata,
                    target.name,
                    f"{column.name}_core_finite",
                    f"{column.name} IS NULL OR {column.name} NOT IN "
                    "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
                )


def _master(metadata: sa.MetaData) -> None:
    for name in ("shops", "listings"):
        enum(metadata, name, "status", ("active", "inactive"))
        check(metadata, name, "identity_time_order", "updated_at >= created_at")
        check(
            metadata,
            name,
            "soft_delete_shape",
            "deleted_at IS NULL OR (status = 'inactive' AND "
            "created_at <= deleted_at AND deleted_at <= updated_at)",
        )
    _text(metadata, "shops", "shop_name")
    for column in ("platform_code", "platform_product_id"):
        _text(metadata, "listings", column, trimmed=True)
    unique(
        metadata,
        "listings",
        "platform_shop_product",
        "platform_code",
        "shop_id",
        "platform_product_id",
    )
    unique(
        metadata,
        "profile_source_records",
        "version_source_row",
        "dataset_version_id",
        "source_row_key",
    )
    _text(metadata, "profile_source_records", "source_record_id", trimmed=True)
    name = "profile_listing_links"
    unique(
        metadata,
        name,
        "version_record_listing",
        "dataset_version_id",
        "profile_record_id",
        "listing_id",
    )
    enum(metadata, name, "relationship_status", ("matched", "rejected"))
    enum(
        metadata,
        name,
        "listed_date_applicability_status",
        ("confirmed", "not_applicable", "unknown", "conflict"),
    )
    _text(metadata, name, "relationship_reason")
    _text(metadata, name, "listed_date_evidence_ref", trimmed=True)
    check(
        metadata,
        name,
        "date_applicability_shape",
        "(listed_date_applicability_status = 'unknown' OR listed_date_evidence_ref IS NOT NULL) "
        "AND (listed_date_applicability_status <> 'confirmed' OR relationship_status = 'matched') "
        "AND (relationship_status <> 'rejected' OR listed_date_applicability_status = "
        "'not_applicable')",
    )


def _versions(metadata: sa.MetaData) -> None:
    name = "dataset_versions"
    for column in (
        "dataset_code",
        "source_system",
        "source_locator",
        "row_schema_revision",
        "interpretation_ref",
        "producer_revision",
        "content_fingerprint",
        "retained_input_ref",
        "upstream_revision",
        "source_cursor",
        "validation_evidence_ref",
    ):
        _text(metadata, name, column)
    enum(metadata, name, "origin_kind", ("source", "derived"))
    enum(metadata, name, "state", ("draft", "validated", "published", "rejected"))
    unique(metadata, name, "dataset_content", "dataset_code", "content_fingerprint")
    _pair(metadata, name, "period_start", "period_end")
    check(
        metadata,
        name,
        "origin_fields_shape",
        "(origin_kind = 'source' AND source_system IS NOT NULL AND source_locator IS NOT NULL) OR "
        "(origin_kind = 'derived' AND source_system IS NULL AND source_locator IS NULL "
        "AND upstream_revision IS NULL AND source_cursor IS NULL AND received_at IS NULL)",
    )
    check(
        metadata,
        name,
        "sealed_state_shape",
        "state NOT IN ('validated', 'published') OR "
        "(content_fingerprint IS NOT NULL AND sealed_at IS NOT NULL "
        "AND validation_evidence_ref IS NOT NULL AND "
        "(origin_kind <> 'source' OR received_at IS NOT NULL))",
    )
    check(
        metadata,
        name,
        "rejection_evidence",
        "state <> 'rejected' OR validation_evidence_ref IS NOT NULL",
    )
    check(metadata, name, "seal_time_order", "sealed_at IS NULL OR sealed_at >= created_at")
    check(
        metadata,
        name,
        "receive_seal_time_order",
        "received_at IS NULL OR sealed_at IS NULL OR received_at <= sealed_at",
    )
    check(
        metadata,
        name,
        "source_seal_received",
        "origin_kind <> 'source' OR sealed_at IS NULL OR received_at IS NOT NULL",
    )
    check(
        metadata,
        name,
        "profile_origin_shape",
        "(dataset_code <> 'profile_records' OR origin_kind = 'source') AND "
        "(dataset_code <> 'profile_links' OR origin_kind = 'derived')",
    )
    check(
        metadata,
        name,
        "source_interpretation_no_business_rules",
        "dataset_code NOT IN ('profile_records', 'profile_links', 'score_standards') "
        "OR rule_set_id IS NULL",
    )
    name = "version_inputs"
    enum(
        metadata,
        name,
        "input_role",
        (
            "source",
            "scope_mapping",
            "profile_records",
            "product_current",
            "product_previous",
            "ad_current",
            "profile",
            "standard",
            "product_history",
            "target_source",
            "scope_list",
            "j1_returns",
            "j2_shipped_sales",
            "j3_cancelled_sales",
            "targets",
        ),
    )
    _text(metadata, name, "usage_note")
    _pair(metadata, name, "window_start", "window_end")
    check(metadata, name, "not_self", "input_version_id <> output_version_id")
    check(metadata, "current_datasets", "revision_nonnegative", "revision >= 0")
    # dataset_code还允许未来有契约的*_source用途，不能猜测具体代码或放任未知来源发布。


def _tasks(metadata: sa.MetaData) -> None:
    name = "task_runs"
    for column in (
        "job_code",
        "request_ref",
        "lock_key",
        "worker_ref",
        "error_code",
        "error_summary",
        "evidence_ref",
    ):
        _text(metadata, name, column)
    enum(
        metadata,
        name,
        "status",
        ("queued", "running", "succeeded", "failed", "cancelled", "abandoned"),
    )
    index(
        metadata, name, "one_running_scope", "lock_key", where="status = 'running'", is_unique=True
    )
    check(metadata, name, "retry_not_self", "retry_of_id IS NULL OR retry_of_id <> id")
    check(
        metadata,
        name,
        "execution_state_shape",
        "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR "
        "(status = 'running' AND started_at IS NOT NULL AND worker_ref IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND finished_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'cancelled', 'abandoned') "
        "AND finished_at IS NOT NULL AND lease_expires_at IS NULL)",
    )
    check(
        metadata,
        name,
        "execution_time_order",
        "(started_at IS NULL OR started_at >= queued_at) AND "
        "(finished_at IS NULL OR finished_at >= queued_at) AND "
        "(started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)",
    )
    check(
        metadata,
        name,
        "result_error_shape",
        "(status <> 'succeeded' OR (result_version_id IS NOT NULL AND error_code IS NULL)) "
        "AND (status NOT IN ('failed', 'abandoned') OR error_code IS NOT NULL)",
    )
    check(
        metadata,
        name,
        "source_update_observation_shape",
        "(source_updated_text IS NULL AND source_updated_at IS NULL AND "
        "source_update_observed_at IS NULL) OR "
        "(source_updated_text IS NOT NULL AND source_update_observed_at IS NOT NULL)",
    )


def _coverage(metadata: sa.MetaData) -> None:
    name = "coverage_records"
    for column in ("field_code", "expectation_ref", "reason_code", "evidence_ref"):
        _text(metadata, name, column)
    enum(metadata, name, "scope_kind", ("dataset", "shop", "shop_channel", "listing"))
    enum(metadata, name, "channel_code", ("product_promotion", "star_shop"))
    enum(
        metadata,
        name,
        "availability_state",
        ("observed", "no_records", "not_received", "field_null", "not_applicable"),
    )
    enum(metadata, name, "completeness_state", ("unknown", "partial", "complete", "not_applicable"))
    unique(
        metadata,
        name,
        "version_scope_field_window",
        "dataset_version_id",
        "scope_kind",
        "shop_id",
        "channel_code",
        "listing_id",
        "field_code",
        "window_start",
        "window_end",
        nulls_not_distinct=True,
    )
    _pair(metadata, name, "window_start", "window_end")
    check(
        metadata,
        name,
        "scope_shape",
        "(scope_kind = 'dataset' AND shop_id IS NULL AND channel_code IS NULL AND "
        "listing_id IS NULL) OR "
        "(scope_kind = 'shop' AND shop_id IS NOT NULL AND channel_code IS NULL AND "
        "listing_id IS NULL) OR "
        "(scope_kind = 'shop_channel' AND shop_id IS NOT NULL AND channel_code IS NOT "
        "NULL AND listing_id IS NULL) OR "
        "(scope_kind = 'listing' AND shop_id IS NULL AND channel_code IS NULL AND "
        "listing_id IS NOT NULL)",
    )
    check(
        metadata,
        name,
        "null_field_present",
        "availability_state <> 'field_null' OR field_code IS NOT NULL",
    )
    check(
        metadata,
        name,
        "expected_count_nonnegative",
        "expected_record_count IS NULL OR expected_record_count >= 0",
    )
    check(
        metadata,
        name,
        "complete_within_window",
        "complete_through IS NULL OR window_end IS NULL OR complete_through <= window_end",
    )
    check(
        metadata,
        name,
        "expectation_required",
        "(completeness_state <> 'complete' AND complete_through IS NULL AND "
        "expected_record_count IS NULL) "
        "OR expectation_ref IS NOT NULL",
    )


def apply(metadata: sa.MetaData) -> None:
    """为全部表已定义的public元数据追加通用及01/04约束；不创建引擎或连接。"""
    _common(metadata)
    _master(metadata)
    _versions(metadata)
    _tasks(metadata)
    _coverage(metadata)
