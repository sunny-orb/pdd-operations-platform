"""03字典的行级约束；只构造DDL结构，不连接数据库。

边界：这些CHECK不能代替受控发布器。规则包完整v1分支、词典、模板、
跨码引用及真实确认依据；目标成员完整性；标准/评分各自恰有十项、标准所属
输入版本与口径相容；评分特征复算、输入窗口/缺日真实性、日期数组排序去重；
报告比较期间、默认修订选择及引用无环，均须在不可绕过的发布事务中落实。
来源金额两位精度属于发布验收，不能在这里截断原值或限制派生估算精度。
"""

import json

import sqlalchemy as sa

from .helpers import check, enum, index, table, unique

METRICS = (
    "visitors_7d",
    "product_revenue_7d",
    "units_7d",
    "favorites_7d",
    "score_conversion_rate_7d",
    "revenue_per_visitor_7d",
    "ad_roi_7d",
    "ad_click_through_rate_7d",
    "ad_click_conversion_rate_7d",
    "revenue_multiple_7d",
)
INPUT_ROLES = (
    "product_current",
    "product_previous",
    "ad_current",
    "profile",
    "standard",
    "product_history",
)
FEATURE_FIELDS = (
    "visitors_7d",
    "product_revenue_7d",
    "units_7d",
    "favorites_7d",
    "buyers_7d",
    "orders_7d",
    "ad_spend_7d",
    "ad_attributed_revenue_7d",
    "ad_impressions_7d",
    "ad_clicks_7d",
    "ad_deals_7d",
    "previous_revenue_7d",
    "previous_orders_7d",
)
STANDARD_FIELDS = (
    "basic_threshold",
    "good_threshold",
    "excellent_threshold",
    "outstanding_threshold",
    "growth_base_floor",
    "threshold_status",
    "precision_kind",
    "reference_group_label",
    "reference_category_system",
    "reference_category_label",
    "declared_sample_count",
    "declared_sample_unit",
    "sample_basis_status",
    "history_start",
    "history_end",
    "quantile_method",
    "fallback_level",
    "fallback_basis_status",
    "source_locator",
    "missing_reason",
)
GROUP_TABLES = (
    "sales_targets",
    "sales_target_shops",
    "refund_targets",
    "refund_target_categories",
    "refund_target_styles",
    "rule_sets",
    "score_standards",
    "score_standard_items",
    "product_scores",
    "product_score_items",
    "aftersales_reports",
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json_literal(value: object) -> str:
    return _literal(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def _closed_object(expression: str, keys: tuple[str, ...]) -> str:
    """根/子对象键必须恰为指定集合；CASE使非法标量不进入对象运算。"""
    array = "ARRAY[" + ", ".join(map(_literal, keys)) + "]::text[]"
    return (
        f"CASE WHEN jsonb_typeof({expression}) = 'object' THEN "
        f"({expression} ?& {array} AND {expression} - {array} = '{{}}'::jsonb) "
        "IS TRUE ELSE FALSE END"
    )


def _date_pair(metadata: sa.MetaData, name: str, start: str, end: str) -> None:
    check(
        metadata,
        name,
        f"{start}_{end}_pair",
        f"({start} IS NULL AND {end} IS NULL) OR "
        f"({start} IS NOT NULL AND {end} IS NOT NULL AND {start} <= {end})",
    )


def _target_constraints(metadata: sa.MetaData) -> None:
    for name in ("sales_targets", "refund_targets"):
        unique(metadata, name, "version_source_row", "dataset_version_id", "source_row_key")
        _date_pair(metadata, name, "period_start", "period_end")
        check(
            metadata,
            name,
            "confirmed_period",
            "period_status <> 'confirmed' OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_kind <> 'unknown')",
        )
    enum(metadata, "sales_targets", "period_kind", ("month", "year", "custom", "unknown"))
    enum(metadata, "sales_targets", "period_status", ("confirmed", "unknown", "conflict"))
    enum(metadata, "sales_targets", "scope_status", ("complete", "partial", "unmapped", "conflict"))
    check(metadata, "sales_targets", "target_amount_nonnegative", "target_amount >= 0")
    enum(
        metadata,
        "refund_targets",
        "period_kind",
        ("cumulative_reference", "natural_month", "rolling_window", "custom", "unknown"),
    )
    enum(
        metadata,
        "refund_targets",
        "period_status",
        ("confirmed", "reference_only", "unknown", "conflict"),
    )
    enum(metadata, "refund_targets", "scope_kind", ("all", "restricted"))
    for column in ("category_match_mode", "style_match_mode"):
        enum(metadata, "refund_targets", column, ("all", "include_union"))
    enum(
        metadata,
        "refund_targets",
        "scope_status",
        ("complete", "partial", "unmapped", "conflict", "unresolved_condition"),
    )
    check(metadata, "refund_targets", "target_rate_range", "target_rate BETWEEN 0 AND 1")
    check(
        metadata,
        "refund_targets",
        "all_scope_modes",
        "scope_kind <> 'all' OR (category_match_mode = 'all' AND style_match_mode = 'all')",
    )
    check(
        metadata,
        "refund_targets",
        "complete_no_unresolved_condition",
        "scope_status <> 'complete' OR unresolved_condition_text IS NULL",
    )
    for name in ("refund_target_categories", "refund_target_styles"):
        enum(metadata, name, "effect", ("include", "exclude"))
        enum(metadata, name, "condition_status", ("confirmed", "unresolved", "conflict"))
    enum(metadata, "refund_target_categories", "match_basis", ("source_code", "exact_label"))
    check(
        metadata,
        "refund_target_categories",
        "category_present",
        "source_category_key IS NOT NULL OR source_category_label IS NOT NULL",
    )
    check(
        metadata,
        "refund_target_categories",
        "match_basis_shape",
        "(match_basis = 'source_code' AND source_category_key IS NOT NULL) OR "
        "(match_basis = 'exact_label' AND source_category_label IS NOT NULL)",
    )
    # 条件唯一不含effect，防止同一条件同时包含和排除。
    index(
        metadata,
        "refund_target_categories",
        "category_code",
        "target_id",
        "source_category_system",
        "source_category_key",
        is_unique=True,
        where="source_category_key IS NOT NULL",
    )
    index(
        metadata,
        "refund_target_categories",
        "category_label_without_code",
        "target_id",
        "source_category_system",
        "source_category_label",
        is_unique=True,
        where="source_category_key IS NULL",
    )
    unique(
        metadata,
        "refund_target_styles",
        "target_namespace_code",
        "target_id",
        "source_style_namespace",
        "source_style_code",
    )


def _rule_constraints(metadata: sa.MetaData) -> None:
    name = "rule_sets"
    unique(metadata, name, "set_revision", "set_code", "revision")
    enum(
        metadata,
        name,
        "domain",
        ("product_scoring", "product_risk", "promotion", "aftersales", "overview"),
    )
    # 新结构修订须连同校验器/迁移另行评审，不能靠任意schema名绕过v1检查。
    enum(metadata, name, "schema_revision", ("v1",))
    enum(
        metadata,
        name,
        "confirmation_status",
        ("confirmed", "partially_confirmed", "pending_confirmation"),
    )
    check(
        metadata,
        name,
        "confirmation_shape",
        "(confirmation_status <> 'confirmed' OR confirmation_ref IS NOT NULL) AND "
        "(confirmed_at IS NULL OR confirmation_status = 'confirmed')",
    )
    check(metadata, name, "sha256_format", "content_sha256 ~ '^[0-9a-f]{64}$'")
    root = _closed_object("parameters", ("policy", "branches", "dictionaries", "suggestions"))
    check(
        metadata,
        name,
        "parameters_v1_root",
        f"({root}) AND "
        "(jsonb_typeof(parameters->'policy') = 'object' AND "
        "jsonb_typeof(parameters->'branches') = 'array' AND "
        "jsonb_typeof(parameters->'dictionaries') = 'array' AND "
        "jsonb_typeof(parameters->'suggestions') = 'array') IS TRUE",
    )
    fixed_policies = {
        "product_risk": {
            "baseline_days": 7,
            "hit_policy_code": "first_match",
            "sort_policy_code": "priority_score_identity",
            "excluded_level_codes": ["清仓"],
            "blockbuster_policy_code": "legacy_revenue_top_20_percent",
        },
        "promotion": {
            "current_window_days": 7,
            "previous_window_days": 7,
            "end_policy_code": "min_selected_source_latest_shanghai_yesterday",
            "hit_policy_code": "collect_findings_highest_priority",
            "sort_policy_code": "priority_current_spend_identity",
            "focused_level_policy_code": "sa_exclusive_new",
        },
        "aftersales": {
            "comparison_policy_code": "frozen_report_periods",
            "grouping_policy_code": "full_source_groups_unique_denominator",
        },
        "overview": {
            "priority_order": ["P0", "P1", "P2", "observation"],
            "sort_policy_code": "sop_impact_deterioration_scope",
            "deduplication_policy_code": "same_object_group_no_parent_child_amount_sum",
            "weighted_score_policy_code": "not_applicable",
            "count_policy_code": None,
        },
    }
    # 以下是字典规定的允许值，只有CHECK，不给任何业务行填默认参数。
    for domain, policy in fixed_policies.items():
        check(
            metadata,
            name,
            f"{domain}_policy_v1",
            f"domain <> {_literal(domain)} OR "
            f"(parameters->'policy' = {_json_literal(policy)}) IS TRUE",
        )
    policy = "(parameters->'policy')"
    gates = {
        "current_window_days": 7,
        "previous_window_days": 7,
        "ad_impressions_min": "2000",
        "previous_orders_min": 5,
        "conversion_numerator_code": "orders",
        "roi_basis_code": "total_attributed_revenue_over_total_cost",
        "require_source_completeness": True,
        "require_verified_reference": True,
        "require_attribution_evidence": True,
    }
    scoring_shape = _closed_object(
        policy, ("metrics", "gates", "tier_policy_code", "lifecycle_policy_code")
    )
    check(
        metadata,
        name,
        "scoring_policy_v1",
        "domain <> 'product_scoring' OR (" + scoring_shape + " AND "
        f"{policy}->'gates' = {_json_literal(gates)} AND "
        f"{policy}->'tier_policy_code' = '\"highest_inclusive_quarter_weight\"'::jsonb AND "
        f"{policy}->'lifecycle_policy_code' = 'null'::jsonb AND "
        f"CASE WHEN jsonb_typeof({policy}->'metrics') = 'array' "
        f"THEN jsonb_array_length({policy}->'metrics') = 10 ELSE FALSE END) IS TRUE",
    )
    check(
        metadata,
        name,
        "non_aftersales_dictionary_empty",
        "domain = 'aftersales' OR (parameters->'dictionaries' = '[]'::jsonb) IS TRUE",
    )
    # 分支内部和metrics每项全部标量/权重及相互引用仍由受控v1发布器校验。


def _standard_constraints(metadata: sa.MetaData) -> None:
    name = "score_standards"
    unique(metadata, name, "version_standard_code", "dataset_version_id", "standard_code")
    enum(metadata, name, "basis_kind", ("fixed_display_trial", "verified_reference", "proposed"))
    enum(metadata, name, "identity_match_status", ("matched", "unmapped", "conflict"))
    enum(metadata, name, "source_date_status", ("confirmed", "unknown"))
    check(
        metadata,
        name,
        "source_date_shape",
        "(source_date_status = 'confirmed') = (source_effective_date IS NOT NULL)",
    )
    check(
        metadata,
        name,
        "matched_identity",
        "identity_match_status <> 'matched' OR "
        "(listing_id IS NOT NULL AND source_listing_key IS NOT NULL)",
    )
    name = "score_standard_items"
    enum(metadata, name, "metric_code", METRICS)
    enum(metadata, name, "threshold_status", ("available", "partial", "missing", "incompatible"))
    enum(metadata, name, "precision_kind", ("source_display_rounded", "full_precision", "unknown"))
    enum(
        metadata,
        name,
        "sample_basis_status",
        ("verified_in_scope", "declared_unverified", "unknown"),
    )
    enum(
        metadata,
        name,
        "fallback_basis_status",
        ("not_used", "verified_in_scope", "declared_unverified", "unknown"),
    )
    thresholds = (
        "basic_threshold",
        "good_threshold",
        "excellent_threshold",
        "outstanding_threshold",
    )
    count = "num_nonnulls(" + ", ".join(thresholds) + ")"
    check(
        metadata,
        name,
        "threshold_state_shape",
        f"(threshold_status = 'available' AND {count} = 4 AND "
        "basic_threshold <= good_threshold AND good_threshold <= excellent_threshold AND "
        "excellent_threshold <= outstanding_threshold) OR "
        f"(threshold_status = 'partial' AND {count} BETWEEN 1 AND 3) OR "
        f"(threshold_status = 'missing' AND {count} = 0) OR threshold_status = 'incompatible'",
    )
    check(
        metadata,
        name,
        "threshold_missing_reason",
        "threshold_status = 'available' OR missing_reason IS NOT NULL",
    )
    check(
        metadata,
        name,
        "growth_floor_shape",
        "growth_base_floor IS NULL OR "
        "(metric_code = 'revenue_multiple_7d' AND growth_base_floor >= 0)",
    )
    check(
        metadata,
        name,
        "sample_count_nonnegative",
        "declared_sample_count IS NULL OR declared_sample_count >= 0",
    )
    _date_pair(metadata, name, "history_start", "history_end")


def _score_constraints(metadata: sa.MetaData) -> None:
    name = "product_scores"
    unique(metadata, name, "version_listing", "dataset_version_id", "listing_id")
    enum(
        metadata,
        name,
        "decision_code",
        ("unscored", "pending_validation", "optimize", "hold", "fix_key_metrics", "small_scale"),
    )
    # 未知阶段和已判阶段均需解释；不臆造生命周期枚举或禁止真实异常负特征。
    check(metadata, name, "lifecycle_reason_required", "lifecycle_reason IS NOT NULL")
    name = "product_score_items"
    enum(metadata, name, "metric_code", METRICS)
    enum(metadata, name, "signal_status", ("valid", "partial", "missing", "not_computable"))
    check(
        metadata,
        name,
        "missing_reason_shape",
        "(signal_status = 'valid' AND missing_reason_code IS NULL "
        "AND missing_reason_text IS NULL) OR "
        "(signal_status <> 'valid' AND missing_reason_code IS NOT NULL AND "
        "(missing_reason_text IS NOT NULL OR evidence_context IS NOT NULL))",
    )
    context = "evidence_context"
    allowed_keys = "ARRAY['input_role','field_codes','missing_dates','note']::text[]"
    check(
        metadata,
        name,
        "evidence_object_keys",
        f"{context} IS NULL OR CASE WHEN jsonb_typeof({context}) = 'object' THEN "
        f"({context} <> '{{}}'::jsonb AND {context} - {allowed_keys} = '{{}}'::jsonb) "
        "IS TRUE ELSE FALSE END",
    )
    role_literals = ", ".join(map(_literal, INPUT_ROLES))
    check(
        metadata,
        name,
        "evidence_role",
        f"{context} IS NULL OR NOT ({context} ? 'input_role') OR "
        f"(jsonb_typeof({context}->'input_role') = 'string' AND "
        f"{context}->>'input_role' IN ({role_literals})) IS TRUE",
    )
    check(
        metadata,
        name,
        "evidence_note",
        f"{context} IS NULL OR NOT ({context} ? 'note') OR "
        f"(jsonb_typeof({context}->'note') = 'string' AND "
        f"btrim({context}->>'note') <> '') IS TRUE",
    )
    fields = tuple("feature." + value for value in FEATURE_FIELDS) + tuple(
        "standard." + value for value in STANDARD_FIELDS
    )
    # 有限代码数组的包含性与每码最多一次均可在本行证明，不需展开子查询。
    duplicates = " AND ".join(
        f"jsonb_array_length(jsonb_path_query_array({context}->'field_codes', "
        f"{_literal('$[*] ? (@ == ' + json.dumps(value) + ')')})) <= 1"
        for value in fields
    )
    check(
        metadata,
        name,
        "evidence_fields",
        f"{context} IS NULL OR NOT ({context} ? 'field_codes') OR "
        f"CASE WHEN jsonb_typeof({context}->'field_codes') = 'array' THEN "
        f"(jsonb_array_length({context}->'field_codes') > 0 AND "
        f"{context}->'field_codes' <@ {_json_literal(fields)} AND {duplicates}) "
        "IS TRUE ELSE FALSE END",
    )
    invalid_dates = (
        '$[*] ? (@.type() != "string" || !(@ like_regex "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))'
    )
    check(
        metadata,
        name,
        "evidence_dates",
        f"{context} IS NULL OR NOT ({context} ? 'missing_dates') OR "
        f"CASE WHEN jsonb_typeof({context}->'missing_dates') = 'array' THEN "
        f"(jsonb_array_length({context}->'missing_dates') > 0 AND "
        f"{context} ? 'input_role' AND NOT "
        f"jsonb_path_exists({context}->'missing_dates', {_literal(invalid_dates)})) "
        "IS TRUE ELSE FALSE END",
    )
    # ISO外形以外的真实日历日期、升序/不重复与角色窗口属于发布器验证。


def _report_constraints(metadata: sa.MetaData) -> None:
    name = "aftersales_reports"
    unique(metadata, name, "result_version", "dataset_version_id")
    unique(
        metadata,
        name,
        "logical_period_revision",
        "report_type",
        "period_start",
        "period_end",
        "revision_no",
    )
    enum(metadata, name, "report_type", ("weekly", "monthly", "custom"))
    check(metadata, name, "period_order", "period_start <= period_end")
    check(metadata, name, "revision_positive", "revision_no >= 1")
    enum(metadata, name, "source_generated_time_status", ("confirmed", "unparsed", "unknown"))
    check(
        metadata,
        name,
        "source_generated_time_shape",
        "(source_generated_time_status = 'confirmed' AND source_generated_at IS NOT NULL) OR "
        "(source_generated_time_status = 'unparsed' AND source_generated_text IS NOT NULL AND "
        "source_generated_at IS NULL AND source_generated_time_note IS NOT NULL) OR "
        "(source_generated_time_status = 'unknown' AND source_generated_text IS NULL AND "
        "source_generated_at IS NULL)",
    )
    for prefix in ("previous", "yoy"):
        status = f"{prefix}_comparison_status"
        reference = f"{prefix}_report_id"
        reason = f"{prefix}_comparison_reason"
        enum(metadata, name, status, ("available", "missing", "incompatible", "pending"))
        check(
            metadata,
            name,
            f"{prefix}_comparison_shape",
            f"({status} = 'available' AND {reference} IS NOT NULL) OR "
            f"({status} IN ('missing', 'pending') AND {reference} IS NULL "
            f"AND {reason} IS NOT NULL) OR "
            f"({status} = 'incompatible' AND {reason} IS NOT NULL)",
        )
        check(metadata, name, f"{prefix}_not_self", f"{reference} IS NULL OR {reference} <> id")
    # URL真实性、可访问性及敏感参数识别不能由通用正则证明，由发布器核验。


def apply(metadata: sa.MetaData) -> None:
    """为已完整定义的public元数据增加本组约束；不创建表、引擎或默认业务值。"""
    # 共同规范要求全部必填文本、可选文本有值时均非空白；根模块已管键列。
    for name in GROUP_TABLES:
        for column in table(metadata, name).columns:
            if isinstance(column.type, sa.Text) and not (
                column.primary_key or column.foreign_keys or column.name == "source_row_key"
            ):
                check(
                    metadata,
                    name,
                    f"{column.name}_text_nonblank",
                    f"{column.name} IS NULL OR btrim({column.name}) <> ''",
                )
    _target_constraints(metadata)
    _rule_constraints(metadata)
    _standard_constraints(metadata)
    _score_constraints(metadata)
    _report_constraints(metadata)
