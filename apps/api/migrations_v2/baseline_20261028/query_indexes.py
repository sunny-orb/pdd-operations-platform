"""20261028查询路径的22个补充B-tree索引；不重复建立PK/UQ索引。"""

import sqlalchemy as sa

from .helpers import index

INDEXES = (
    ("listings", ("shop_id", "id"), None),
    ("listings", ("platform_code", "platform_product_id", "shop_id"), None),
    ("profile_listing_links", ("dataset_version_id", "listing_id", "profile_record_id"), None),
    ("product_daily_facts", ("dataset_version_id", "business_date", "listing_id", "id"), None),
    ("shop_daily_facts", ("dataset_version_id", "business_date", "shop_id", "id"), None),
    (
        "product_promotion_records",
        ("dataset_version_id", "source_granularity", "period_end", "listing_id", "id"),
        None,
    ),
    (
        "product_promotion_records",
        ("dataset_version_id", "listing_id", "source_granularity", "period_end", "id"),
        None,
    ),
    (
        "shop_channel_promotion_daily",
        ("dataset_version_id", "business_date", "shop_id", "id"),
        None,
    ),
    ("shop_rating_daily", ("dataset_version_id", "shop_id", "rating_date DESC", "id"), None),
    ("shop_refund_records", ("dataset_version_id", "business_date", "shop_id", "id"), None),
    ("shop_region_daily", ("dataset_version_id", "business_date", "shop_id", "id"), None),
    (
        "product_region_records",
        ("dataset_version_id", "source_granularity", "period_end", "shop_id", "id"),
        None,
    ),
    ("product_sales_records", ("dataset_version_id", "source_style_code"), None),
    ("return_records", ("dataset_version_id", "source_style_code"), None),
    ("sales_target_shops", ("shop_id", "target_id"), None),
    ("score_standards", ("dataset_version_id", "listing_id"), None),
    (
        "aftersales_reports",
        ("report_type", "period_end DESC", "period_start DESC", "revision_no DESC", "id"),
        None,
    ),
    ("version_inputs", ("input_version_id", "output_version_id"), None),
    ("task_runs", ("job_code", "queued_at DESC", "id"), None),
    ("task_runs", ("result_version_id", "status", "finished_at DESC", "id"), None),
    ("task_runs", ("lease_expires_at", "id"), "status = 'running'"),
    ("task_runs", ("queued_at", "id"), "status = 'queued'"),
)


def apply(metadata: sa.MetaData) -> None:
    """只冻结批准的查询列序；真实容量/执行计划待PG验收后决定是否调整。"""
    for position, (table_name, columns, predicate) in enumerate(INDEXES, 1):
        index(metadata, table_name, f"query_{position:02}", *columns, where=predicate)
