"""固定发布四线的参数校验与版本化入库；不生成/迁移旧评分。"""

from uuid import uuid4

import sqlalchemy as sa

from app.db import tables as t
from app.modules.products.ingestion import SHOPS, IngestionError, digest
from app.modules.products.scoring import METRICS, RULE_VERSION, TIERS, number


def display_parameters(document):
    """将逐款抄录且已对照DOM的显示标准转为精确十进制参数；百分数除100。"""
    products = {}
    for pid, category, column, model_category, season, lifecycle, floor, family in document[
        "products"
    ]:
        if (
            not isinstance(pid, str)
            or not pid.isascii()
            or not pid.isdigit()
            or column not in SHOPS
        ):
            raise IngestionError("INVALID_THRESHOLD_IDENTITY")
        key = f"pdd:{SHOPS[column][0]}:{pid}"
        if key in products:
            raise IngestionError("DUPLICATE_THRESHOLD_IDENTITY")
        group = document["families"][family]
        if len(group["lines"]) != 10 or len(group["counts"]) != 10:
            raise IngestionError("INVALID_THRESHOLD_METRIC_COUNT")
        metrics = {}
        for i, (metric, (_, _, unit)) in enumerate(METRICS.items()):
            lines = []
            for raw in group["lines"][i]:
                if not isinstance(raw, str) or (unit == "ratio") != raw.endswith("%"):
                    raise IngestionError("INVALID_DISPLAY_THRESHOLD_UNIT")
                value = number(raw.rstrip("%").replace(",", ""))
                if value is None:
                    raise IngestionError("INVALID_THRESHOLD_NUMBER")
                lines.append(str(value / 100 if unit == "ratio" else value))
            reference = group.get("references", {}).get(str(i), group["reference"])
            metrics[metric] = dict(
                unit=unit,
                thresholds=dict(zip(TIERS, lines, strict=True)),
                reference_group=reference,
                reference_sample_count=group["counts"][i],
                fallback_level="published_weighted_fallback"
                if "加权" in reference
                else (
                    "published_all_category_fallback" if "回退" in reference else "published_group"
                ),
            )
        products[key] = dict(
            category=None if category == "未匹配" else category,
            model_category=model_category,
            season=season,
            lifecycle=lifecycle,
            growth_base_floor=floor.replace(",", ""),
            metrics=metrics,
        )
    return dict(products=products)


def validate_parameters(parameters):
    """仅容许有明确复合身份和量纲的有限非递减四线；非法版本整体拒绝。"""
    if not parameters.get("products"):
        raise IngestionError("EMPTY_THRESHOLD_PARAMETERS")
    for key, product in parameters["products"].items():
        parts = key.split(":")
        if len(parts) != 3 or parts[0] != "pdd" or not all(parts):
            raise IngestionError("INVALID_THRESHOLD_IDENTITY")
        if set(product["metrics"]) != set(METRICS):
            raise IngestionError("INVALID_THRESHOLD_METRIC_COUNT")
        for metric, reference in product["metrics"].items():
            values = [number(reference["thresholds"].get(x)) for x in TIERS]
            if (
                reference.get("unit") != METRICS[metric][2]
                or any(v is None or v < 0 for v in values)
                or values != sorted(values)
            ):
                raise IngestionError("INVALID_THRESHOLD_PARAMETERS")
            n = reference.get("reference_sample_count")
            if n is not None and (type(n) is not int or n < 0):
                raise IngestionError("INVALID_REFERENCE_COUNT")
        floor = number(product.get("growth_base_floor"))
        if floor is None or floor < 0:
            raise IngestionError("INVALID_GROWTH_BASE_FLOOR")


def latest_model(c):
    return (
        c.execute(
            sa.select(t.model_versions)
            .where(t.model_versions.c.rule_version == RULE_VERSION)
            .order_by(t.model_versions.c.created_at.desc(), t.model_versions.c.model_id.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )


def import_model(engine, parameters, provenance):
    """幂等追加固定显示基线；来源日期/精度/完整生成链缺口显式保存为试算。"""
    validate_parameters(parameters)
    required = ("source_url", "resource_url", "source_as_of", "precision", "observed_at")
    if any(not provenance.get(k) for k in required):
        raise IngestionError("THRESHOLD_PROVENANCE_REQUIRED")
    content_hash = digest(
        dict(
            parameters=parameters,
            provenance={k: v for k, v in provenance.items() if k != "observed_at"},
            rule=RULE_VERSION,
        )
    )
    with engine.begin() as c:
        c.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('pdd:m1:score-publication'))"))
        previous = (
            c.execute(
                sa.select(t.model_versions).where(t.model_versions.c.content_hash == content_hash)
            )
            .mappings()
            .first()
        )
        if previous:
            return dict(
                model_id=str(previous["model_id"]),
                changed=False,
                products=len(parameters["products"]),
            )
        model_id = uuid4()
        c.execute(
            t.model_versions.insert().values(
                model_id=model_id,
                rule_version=RULE_VERSION,
                content_hash=content_hash,
                status="fixed_display_trial",
                parameters=parameters,
                provenance=provenance,
            )
        )
        return dict(model_id=str(model_id), changed=True, products=len(parameters["products"]))
