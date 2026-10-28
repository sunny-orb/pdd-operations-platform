"""已确认十项计分与试行门控包；正式放量分支保持关闭。"""

from app.modules.product_board.metrics import WEIGHTS

REF = "docs/m1-model-reuse.md#本次接入v2的最小设计"


def parameters():
    """收藏按M1-01日新增确认启用试算；来源/参照效果未验，正式放量关闭。"""
    branches = []
    for code, _, _, _ in WEIGHTS:
        enabled = code != "revenue_multiple_7d"
        branches.append(
            dict(
                code="validate_" + code,
                predicate_code="validate_" + code,
                enabled=enabled,
                confirmation_status="confirmed",
                validation_status="verified_in_scope" if enabled else "pending_evidence",
                contract_ref="docs/data-contract.md#351-m1-商品经营整页含-m4-新品评分",
                evidence_ref=REF if enabled else None,
                depends_on_question_codes=[],
                thresholds={},
                template_code=None,
            )
        )
    thresholds = dict(
        unscored={},
        pending_validation={},
        optimize=dict(score_lt="50", review_window_days=7),
        hold=dict(score_gte="50", score_lt="75", observe_days=3),
        fix_key_metrics=dict(
            score_gte="75",
            roi_min_tier=2,
            conversion_min_tier=2,
            click_conversion_min_tier=1,
            growth_multiple_gt="1",
        ),
        small_scale=dict(
            score_gte="75",
            roi_min_tier=2,
            conversion_min_tier=2,
            click_conversion_min_tier=1,
            growth_multiple_gt="1",
            budget_step_min="0.10",
            budget_step_max="0.20",
            observe_days=3,
            review_interval_days=1,
        ),
    )
    suggestions = []
    for code in thresholds:
        enabled = code in ("unscored", "pending_validation")
        branches.append(
            dict(
                code=code,
                predicate_code=code,
                enabled=enabled,
                confirmation_status="confirmed",
                validation_status="verified_in_scope" if enabled else "pending_evidence",
                contract_ref=REF,
                evidence_ref=REF if enabled else None,
                depends_on_question_codes=[],
                thresholds=thresholds[code],
                template_code="tpl_" + code if enabled else None,
            )
        )
        if enabled:
            suggestions.append(
                dict(
                    template_code="tpl_" + code,
                    branch_codes=[code],
                    text="保留原值，先补相容输入及标准"
                    if code == "unscored"
                    else "新试行参照效果、完整性与推广归因窗口仍待核，暂不放量",
                    verification_metric_codes=[],
                    execution_mode="display_only",
                )
            )
    return dict(
        policy=dict(
            metrics=[
                dict(
                    metric_code=code, weight=str(weight), validation_branch_code="validate_" + code
                )
                for code, _, weight, _ in WEIGHTS
            ],
            gates=dict(
                current_window_days=7,
                previous_window_days=7,
                ad_impressions_min="2000",
                previous_orders_min=5,
                conversion_numerator_code="orders",
                roi_basis_code="total_attributed_revenue_over_total_cost",
                require_source_completeness=True,
                require_verified_reference=True,
                require_attribution_evidence=True,
            ),
            tier_policy_code="highest_inclusive_quarter_weight",
            lifecycle_policy_code=None,
        ),
        branches=branches,
        dictionaries=[],
        suggestions=suggestions,
    )
