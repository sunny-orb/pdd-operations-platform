"""只读已发布相容评分及标准，按冻结输入Decimal重演展示，不读私有JSON。"""

from datetime import timedelta
from decimal import Decimal, localcontext

import sqlalchemy as sa

from app.modules.product_board.metrics import WEIGHTS, divide

GRADES = ("未达基础", "基础", "良好", "优秀", "突出")


def published_scores(repo):
    """返回相容当前四头的已发布评分；任一输入改变立即待重算，不沿用旧分。"""
    from .service import producer_hash

    version = repo.params.get("product_scores")
    standard = repo.params.get("score_standards")
    if not version or not standard:
        return {}, "standards_not_connected"
    producers = dict(
        repo.c.execute(
            sa.text("SELECT id,producer_revision FROM public.dataset_versions WHERE id=ANY(:ids)"),
            dict(ids=[version, standard]),
        ).all()
    )
    if any(producers.get(vid) != producer_hash() for vid in (version, standard)):
        return {}, "pending_recompute"
    inputs = {
        r["input_role"]: dict(r)
        for r in repo.c.execute(
            sa.text("SELECT * FROM public.version_inputs WHERE output_version_id=:v"),
            dict(v=version),
        ).mappings()
    }
    end = repo.end
    expected = dict(
        product_current=(repo.params["product_daily"], end - timedelta(days=6), end),
        product_previous=(
            repo.params["product_daily"],
            end - timedelta(days=13),
            end - timedelta(days=7),
        ),
        ad_current=(repo.params["product_promotion"], end - timedelta(days=6), end),
        profile=(repo.params["profile_links"], None, None),
        standard=(standard, None, None),
    )
    if set(inputs) != set(expected) or any(
        (inputs[role]["input_version_id"], inputs[role]["window_start"], inputs[role]["window_end"])
        != value
        for role, value in expected.items()
    ):
        return {}, "pending_recompute"
    rows = {
        r["id"]: dict(r)
        for r in repo.c.execute(
            sa.text(
                "SELECT p.*,s.dataset_version_id standard_version,s.basis_kind "
                "FROM public.product_scores p JOIN public.score_standards s "
                "ON s.id=p.standard_id WHERE p.dataset_version_id=:v"
            ),
            dict(v=version),
        ).mappings()
    }
    if any(
        r["standard_version"] != standard or r["basis_kind"] != "proposed" for r in rows.values()
    ):
        return {}, "incompatible_standard"
    items = repo.c.execute(
        sa.text(
            "SELECT i.*,t.basic_threshold,t.good_threshold,t.excellent_threshold,"
            "t.outstanding_threshold,t.reference_group_label,t.declared_sample_count "
            "FROM public.product_score_items i JOIN public.product_scores p ON p.id=i.score_id "
            "JOIN public.score_standard_items t ON t.standard_id=p.standard_id "
            "AND t.metric_code=i.metric_code WHERE p.dataset_version_id=:v "
            "ORDER BY i.score_id,i.metric_code"
        ),
        dict(v=version),
    ).mappings()
    grouped = {key: {} for key in rows}
    for item in items:
        grouped[item["score_id"]][item["metric_code"]] = dict(item)
    result = {}
    for sid, row in rows.items():
        if set(grouped[sid]) != {code for code, _, _, _ in WEIGHTS}:
            return {}, "incompatible_score_items"
        raw = row
        values = dict(
            visitors_7d=raw["visitors_7d"],
            product_revenue_7d=raw["product_revenue_7d"],
            units_7d=raw["units_7d"],
            favorites_7d=raw["favorites_7d"],
            score_conversion_rate_7d=divide(raw["orders_7d"], raw["visitors_7d"]),
            revenue_per_visitor_7d=divide(raw["product_revenue_7d"], raw["visitors_7d"]),
            ad_roi_7d=divide(raw["ad_attributed_revenue_7d"], raw["ad_spend_7d"]),
            ad_click_through_rate_7d=divide(raw["ad_clicks_7d"], raw["ad_impressions_7d"]),
            ad_click_conversion_rate_7d=divide(raw["ad_deals_7d"], raw["ad_clicks_7d"]),
            revenue_multiple_7d=divide(raw["product_revenue_7d"], raw["previous_revenue_7d"]),
        )
        signals = []
        score = Decimal(0)
        pending = 0
        with localcontext() as context:
            context.prec = 50
            for code, label, weight, kind in WEIGHTS:
                item = grouped[sid][code]
                bands = [
                    item[key]
                    for key in (
                        "basic_threshold",
                        "good_threshold",
                        "excellent_threshold",
                        "outstanding_threshold",
                    )
                ]
                valid = (
                    item["signal_status"] == "valid"
                    and values[code] is not None
                    and all(v is not None for v in bands)
                )
                tier = (
                    sum(Decimal(values[code]) >= v for v in bands)
                    if valid and values[code] > 0
                    else 0
                )
                points = Decimal(weight) * tier / 4 if valid else None
                if points is not None:
                    score += points
                else:
                    pending += weight
                signals.append(
                    dict(
                        code=code,
                        label=label,
                        weight=weight,
                        type=kind,
                        value=str(values[code]) if values[code] is not None else None,
                        basic=bands[0],
                        good=bands[1],
                        excellent=bands[2],
                        outstanding=bands[3],
                        grade=GRADES[tier] if valid else None,
                        points=points,
                        status="trial_valid" if valid else "pending_validation",
                        reason="新试行参照，待业务与来源验证"
                        if valid
                        else item["missing_reason_text"] or "输入或标准不相容",
                        reference_group=item["reference_group_label"],
                        sample_count=str(item["declared_sample_count"])
                        if item["declared_sample_count"] is not None
                        else None,
                    )
                )
        scored = pending < 100
        result[row["listing_id"]] = dict(
            signals=signals,
            score=score if scored else None,
            pending_weight=str(pending),
            complete_score=None,
            decision="pending_validation" if scored else "unscored",
            decision_label="先补验证 · 暂不放量" if scored else "暂未评分",
            reason="新试行标准已启用；收藏按日新增累计未扣取消，推广沿来源支付日汇总，参照效果与完整日仍待核",
            action="按新试行标准观察表现，先补验证，暂不放量",
            score_status="partial" if scored else "unscored",
            standard_status="trial",
        )
    return result, "trial_active"
