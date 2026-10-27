"""冻结02经营事实的行级约束；只构造元数据，不连接或修改数据库。

跨表版本用途、来源解释、完整性及集合聚合必须由未来受控发布器验证。
本模块不把日期可解析、身份匹配或原行唯一解释为业务数据已齐。
"""

import sqlalchemy as sa

from .helpers import check, enum, index, unique

_MATCH_STATUSES = ("matched", "unmatched", "conflict", "not_provided")
_DATE_STATUSES = (
    "parsed_label",
    "verified_period",
    "not_provided",
    "missing",
    "unparseable",
    "conflict",
)
_ROW_KINDS = ("detail", "subtotal", "total", "unclassified")
_FACT_IDENTITIES = (
    ("product_daily_facts", "listing"),
    ("shop_daily_facts", "shop"),
    ("product_promotion_records", "listing"),
    ("shop_channel_promotion_daily", "shop"),
    ("shop_rating_daily", "shop"),
    ("shop_refund_records", "shop"),
    ("shop_region_daily", "shop"),
    ("product_region_records", "shop"),
    ("product_sales_records", "shop"),
    ("return_records", "shop"),
)
_DAY_DATES = (
    ("product_daily_facts", "business_date"),
    ("shop_daily_facts", "business_date"),
    ("shop_channel_promotion_daily", "business_date"),
    ("shop_rating_daily", "rating_date"),
    ("shop_region_daily", "business_date"),
)
_PERIOD_TABLES = (
    "product_promotion_records",
    "product_region_records",
    "product_sales_records",
    "return_records",
)


def _date_shape(
    metadata: sa.MetaData,
    table_name: str,
    date_column: str,
    *,
    allows_not_provided: bool,
) -> None:
    """日标签与状态双向约束；解析成功不等于已核完整业务日。"""
    statuses = _DATE_STATUSES
    if not allows_not_provided:
        statuses = tuple(value for value in statuses if value != "not_provided")
    enum(metadata, table_name, "date_status", statuses)
    check(
        metadata,
        table_name,
        "date_status_shape",
        "(date_status IN ('parsed_label', 'verified_period') "
        f"AND {date_column} IS NOT NULL) OR "
        "(date_status NOT IN ('parsed_label', 'verified_period') "
        f"AND {date_column} IS NULL)",
    )


def apply(metadata: sa.MetaData) -> None:
    """为已存在的十事实表追加约束；列、PK/FK及通用有限性由基线负责。

    输入要求全部表已以public限定名注册；输出为原地追加的SQLAlchemy约束。
    此函数只用于该冻结revision构造一次，不负责来源校验或发布状态转换。
    """
    for table_name, identity in _FACT_IDENTITIES:
        unique(
            metadata,
            table_name,
            "version_source_row",
            "dataset_version_id",
            "source_row_key",
        )
        status_column = f"{identity}_match_status"
        fk_column = f"{identity}_id"
        enum(metadata, table_name, status_column, _MATCH_STATUSES)
        check(
            metadata,
            table_name,
            f"{identity}_match_shape",
            f"({status_column} = 'matched' AND {fk_column} IS NOT NULL) OR "
            f"({status_column} <> 'matched' AND {fk_column} IS NULL)",
        )

    for table_name, date_column in _DAY_DATES:
        _date_shape(metadata, table_name, date_column, allows_not_provided=False)
    # 退款保留真实统计粒度；它不是类型契约限定day的五张专用日表之一。
    _date_shape(metadata, "shop_refund_records", "business_date", allows_not_provided=True)

    for table_name in _PERIOD_TABLES:
        enum(metadata, table_name, "date_status", _DATE_STATUSES)
        check(
            metadata,
            table_name,
            "period_pair_order",
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL "
            "AND period_start <= period_end)",
        )
        check(
            metadata,
            table_name,
            "date_status_shape",
            "(date_status IN ('parsed_label', 'verified_period') "
            "AND period_start IS NOT NULL AND period_end IS NOT NULL) OR "
            "(date_status NOT IN ('parsed_label', 'verified_period') "
            "AND period_start IS NULL AND period_end IS NULL)",
        )

    # 仅契约3.1已核商品day范围允许此业务唯一性；范围相容性仍须发布器核验。
    index(
        metadata,
        "product_daily_facts",
        "version_listing_date",
        "dataset_version_id",
        "listing_id",
        "business_date",
        where="listing_id IS NOT NULL AND business_date IS NOT NULL",
        is_unique=True,
    )

    for table_name in ("product_promotion_records", "product_region_records"):
        # 原周期值不是已确认的封闭集合，不能用推测枚举丢失未知原标签。
        check(
            metadata,
            table_name,
            "day_period_same_date",
            "source_granularity IS DISTINCT FROM 'day' "
            "OR period_start IS NULL OR period_start = period_end",
        )

    enum(
        metadata,
        "shop_channel_promotion_daily",
        "channel_code",
        ("product_promotion", "star_shop"),
    )
    check(
        metadata,
        "shop_channel_promotion_daily",
        "star_shop_net_unavailable",
        "channel_code IS DISTINCT FROM 'star_shop' OR net_attributed_revenue IS NULL",
    )

    for table_name in ("product_sales_records", "return_records"):
        enum(metadata, table_name, "row_kind", _ROW_KINDS)

    # 仅落实JSON行内结构；冻结键白名单、必要性和不重复显式列由发布器核验。
    check(
        metadata,
        "return_records",
        "analysis_fields_text_object",
        "source_analysis_fields IS NULL OR "
        "(jsonb_typeof(source_analysis_fields) = 'object' AND "
        "NOT jsonb_path_exists(source_analysis_fields, "
        '\'$.* ? (@.type() != "string" && @.type() != "null")\'))',
    )
