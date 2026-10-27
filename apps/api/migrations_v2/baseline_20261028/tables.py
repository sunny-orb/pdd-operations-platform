"""从01—04字典冻结的20261028字段快照；运行时不读取Markdown，不接数据库。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from .helpers import name


def add_tables(metadata: sa.MetaData) -> None:
    """向给定MetaData加入30表494字段及50处FK；不执行DDL。"""
    sa.Table(
        "shops",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="项目统一店铺ID"),
        sa.Column("shop_name", sa.Text(), nullable=True, comment="已核实的当前规范店铺名称"),
        sa.Column("status", sa.Text(), nullable=False, comment="该身份在本项目中的启用状态"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="本项目首次建立该身份的时刻",
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="本项目最后修订该主记录的时刻",
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="本项目将该身份软删除的时刻",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "shops", "identity")),
        schema="public",
        comment="经营店铺",
    )

    sa.Table(
        "listings",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="项目链接ID"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "listings", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="链接所属统一店铺",
        ),
        sa.Column("platform_code", sa.Text(), nullable=False, comment="已核实的平台命名空间"),
        sa.Column("platform_product_id", sa.Text(), nullable=False, comment="该店铺内的平台商品ID"),
        sa.Column("status", sa.Text(), nullable=False, comment="该身份在本项目中的启用状态"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="本项目首次建立链接身份的时刻",
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="本项目最后修订链接主记录时刻",
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="本项目将该链接身份软删除的时刻",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "listings", "identity")),
        schema="public",
        comment="店铺商品链接",
    )

    sa.Table(
        "profile_source_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本版本中的资料原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "profile_source_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="固定必要原行投影的来源资料版本",
        ),
        sa.Column(
            "source_row_key", sa.Text(), nullable=False, comment="本份输入内可重读的原行定位键"
        ),
        sa.Column(
            "source_record_id", sa.Text(), nullable=True, comment="来源原生主键，如W01的__id"
        ),
        sa.Column("source_product_name_raw", sa.Text(), nullable=True, comment="运营商品名原格"),
        sa.Column("source_category_raw", sa.Text(), nullable=True, comment="运营品类原格"),
        sa.Column("source_level_raw", sa.Text(), nullable=True, comment="产品等级原格"),
        sa.Column("source_operator_raw", sa.Text(), nullable=True, comment="负责运营原标签"),
        sa.Column(
            "source_listed_at_raw", sa.Text(), nullable=True, comment="实际上架日期/时间的原表示"
        ),
        sa.Column(
            "source_listing_status_raw", sa.Text(), nullable=True, comment="来源上架/下架状态原格"
        ),
        sa.Column("source_style_codes_raw", sa.Text(), nullable=True, comment="款号原格完整内容"),
        sa.Column(
            "source_listing_ids_official_raw", sa.Text(), nullable=True, comment="官方店ID列原格"
        ),
        sa.Column(
            "source_listing_ids_apparel_raw", sa.Text(), nullable=True, comment="服饰店ID列原格"
        ),
        sa.Column(
            "source_listing_ids_outdoor_raw", sa.Text(), nullable=True, comment="户外店ID列原格"
        ),
        sa.Column(
            "source_listing_ids_raingear_raw", sa.Text(), nullable=True, comment="雨具别名列ID原格"
        ),
        sa.Column(
            "source_listing_ids_luggage_raw", sa.Text(), nullable=True, comment="箱包别名列ID原格"
        ),
        sa.Column(
            "source_listing_ids_kids_footwear_raw",
            sa.Text(),
            nullable=True,
            comment="童装童鞋店ID列原格",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "profile_source_records", "identity")),
        schema="public",
        comment="商品资料原行",
    )

    sa.Table(
        "profile_listing_links",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本版本的原行—链接关系ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "profile_listing_links", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="固定关系结果与规则的版本",
        ),
        sa.Column(
            "profile_record_id",
            sa.Text(),
            sa.ForeignKey(
                "public.profile_source_records.id",
                name=name("fk", "profile_listing_links", "profile_record_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="该关系涉及的固定资料原行",
        ),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey(
                "public.listings.id",
                name=name("fk", "profile_listing_links", "listing_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="该关系涉及的已核店铺商品链接",
        ),
        sa.Column("relationship_status", sa.Text(), nullable=False, comment="本关系的核验结果"),
        sa.Column(
            "relationship_reason", sa.Text(), nullable=False, comment="确认或排除关系的可核查依据"
        ),
        sa.Column(
            "listed_date_applicability_status",
            sa.Text(),
            nullable=False,
            comment="本原行日期对本链接的适用状态",
        ),
        sa.Column(
            "listed_date_evidence_ref",
            sa.Text(),
            nullable=True,
            comment="日期适用/排除/冲突的固定证据引用",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "profile_listing_links", "identity")),
        schema="public",
        comment="资料原行与链接关系",
    )

    sa.Table(
        "product_daily_facts",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本项目事实行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "product_daily_facts", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="可重读的商品日输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源z_id原标识"),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey(
                "public.listings.id",
                name=name("fk", "product_daily_facts", "listing_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实的店铺商品链接",
        ),
        sa.Column("listing_match_status", sa.Text(), nullable=False, comment="链接身份匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源shop_id原值"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源观察店名"),
        sa.Column("source_product_id", sa.Text(), nullable=True, comment="来源spu_id原值"),
        sa.Column("source_product_name", sa.Text(), nullable=True, comment="来源spu_name观察值"),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="来源dt原表示"),
        sa.Column("business_date", sa.Date(), nullable=True, comment="来源dt对应的日标签"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="行日期解析/核验状态"),
        sa.Column("source_related_date_raw", sa.Text(), nullable=True, comment="relevant_dt原值"),
        sa.Column("views", sa.BigInteger(), nullable=True, comment="商品浏览量product_pv"),
        sa.Column("visitors", sa.BigInteger(), nullable=True, comment="商品访客product_uv"),
        sa.Column(
            "favorites",
            sa.BigInteger(),
            nullable=True,
            comment="收藏商品人数favorite_product_users",
        ),
        sa.Column("buyers", sa.BigInteger(), nullable=True, comment="成交买家transaction_users"),
        sa.Column("orders", sa.BigInteger(), nullable=True, comment="成交订单transaction_orders"),
        sa.Column("units", sa.BigInteger(), nullable=True, comment="支付件数transaction_products"),
        sa.Column(
            "product_revenue", sa.Numeric(), nullable=True, comment="商品成交金额transaction_amount"
        ),
        sa.Column(
            "source_conversion_rate",
            sa.Numeric(),
            nullable=True,
            comment="来源transaction_conversion_rate",
        ),
        sa.Column(
            "source_first_category", sa.Text(), nullable=True, comment="来源平台一级类目观察值"
        ),
        sa.Column(
            "source_second_category", sa.Text(), nullable=True, comment="来源平台二级类目观察值"
        ),
        sa.Column(
            "source_third_category", sa.Text(), nullable=True, comment="来源平台三级类目观察值"
        ),
        sa.Column(
            "source_created_time_raw", sa.Text(), nullable=True, comment="来源create_time原表示"
        ),
        sa.Column(
            "source_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="已有时区依据的来源创建时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "product_daily_facts", "identity")),
        schema="public",
        comment="商品日经营原行",
    )

    sa.Table(
        "shop_daily_facts",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="店铺经营原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "shop_daily_facts", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="店铺日输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源id"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "shop_daily_facts", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实经营店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源shop_id"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源观察店名"),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="来源dt原表示"),
        sa.Column("business_date", sa.Date(), nullable=True, comment="可解释的来源日标签"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="日期解析/核验状态"),
        sa.Column("shop_revenue", sa.Numeric(), nullable=True, comment="JSON成交金额"),
        sa.Column("shop_visitors", sa.Numeric(), nullable=True, comment="JSON店铺访客数"),
        sa.Column("shop_views", sa.Numeric(), nullable=True, comment="JSON店铺浏览量"),
        sa.Column("product_visitors", sa.Numeric(), nullable=True, comment="JSON商品访客数"),
        sa.Column("product_views", sa.Numeric(), nullable=True, comment="JSON商品浏览量"),
        sa.Column("buyers", sa.Numeric(), nullable=True, comment="JSON成交买家数"),
        sa.Column("orders", sa.Numeric(), nullable=True, comment="JSON成交订单数"),
        sa.Column("source_aov", sa.Numeric(), nullable=True, comment="JSON来源客单价"),
        sa.Column("source_uv_value", sa.Numeric(), nullable=True, comment="JSON来源成交UV价值"),
        sa.Column(
            "source_conversion_rate", sa.Numeric(), nullable=True, comment="JSON来源成交转化率"
        ),
        sa.Column(
            "source_updated_time_raw", sa.Text(), nullable=True, comment="来源updated_at原表示"
        ),
        sa.Column(
            "source_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有时区依据的来源更新时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "shop_daily_facts", "identity")),
        schema="public",
        comment="店铺日经营原行",
    )

    sa.Table(
        "product_promotion_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="推广原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "product_promotion_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="推广输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源id原标识"),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey(
                "public.listings.id",
                name=name("fk", "product_promotion_records", "listing_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实商品链接",
        ),
        sa.Column("listing_match_status", sa.Text(), nullable=False, comment="链接匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="原shop_id"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源观察店名"),
        sa.Column("source_product_id", sa.Text(), nullable=True, comment="原spu_id"),
        sa.Column("source_product_name", sa.Text(), nullable=True, comment="原spu_name"),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="原dt表示"),
        sa.Column("source_start_dt_raw", sa.Text(), nullable=True, comment="原start_dt表示"),
        sa.Column("source_granularity", sa.Text(), nullable=True, comment="原time_granularity"),
        sa.Column("period_start", sa.Date(), nullable=True, comment="已解释的实际统计起日"),
        sa.Column("period_end", sa.Date(), nullable=True, comment="已解释的实际统计末日"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="期间解析/核验状态"),
        sa.Column("source_grouping", sa.Text(), nullable=True, comment="来源spu_grouping"),
        sa.Column("promotion_name", sa.Text(), nullable=True, comment="来源推广名称"),
        sa.Column("promotion_type", sa.Text(), nullable=True, comment="来源推广场景/方式"),
        sa.Column("bidding_method", sa.Text(), nullable=True, comment="来源出价方式"),
        sa.Column("source_deleted_label", sa.Text(), nullable=True, comment="来源is_deleted原标签"),
        sa.Column("spend", sa.Numeric(), nullable=True, comment="总花费total_cost"),
        sa.Column(
            "transaction_spend", sa.Numeric(), nullable=True, comment="成交花费transaction_cost"
        ),
        sa.Column(
            "attributed_revenue", sa.Numeric(), nullable=True, comment="总归因transaction_amount"
        ),
        sa.Column(
            "attributed_deals", sa.Numeric(), nullable=True, comment="总归因transaction_count"
        ),
        sa.Column(
            "net_attributed_revenue",
            sa.Numeric(),
            nullable=True,
            comment="净归因net_transaction_amount",
        ),
        sa.Column(
            "net_attributed_deals",
            sa.Numeric(),
            nullable=True,
            comment="净归因net_transaction_count",
        ),
        sa.Column(
            "settled_attributed_revenue", sa.Numeric(), nullable=True, comment="结算归因金额"
        ),
        sa.Column("settled_attributed_deals", sa.Numeric(), nullable=True, comment="结算归因笔数"),
        sa.Column("impressions", sa.Numeric(), nullable=True, comment="impression_count广告曝光量"),
        sa.Column("clicks", sa.Numeric(), nullable=True, comment="click_count广告点击量"),
        sa.Column("source_roi", sa.Numeric(), nullable=True, comment="来源actual_roi"),
        sa.Column("source_net_roi", sa.Numeric(), nullable=True, comment="来源net_actual_roi"),
        sa.Column(
            "source_settlement_roi", sa.Numeric(), nullable=True, comment="来源settlement_roi"
        ),
        sa.Column("source_ctr", sa.Numeric(), nullable=True, comment="来源click_through_rate"),
        sa.Column("source_cvr", sa.Numeric(), nullable=True, comment="来源click_conversion_rate"),
        sa.Column(
            "source_created_time_raw", sa.Text(), nullable=True, comment="来源创建时间原表示"
        ),
        sa.Column(
            "source_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有依据的来源创建时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "product_promotion_records", "identity")),
        schema="public",
        comment="商品推广原行",
    )

    sa.Table(
        "shop_channel_promotion_daily",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="店铺渠道推广原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "shop_channel_promotion_daily", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="渠道来源输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源id"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "shop_channel_promotion_daily", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("channel_code", sa.Text(), nullable=True, comment="已核来源对应的有限渠道代码"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源店铺ID"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源观察店名"),
        sa.Column(
            "source_channel_label", sa.Text(), nullable=True, comment="来源明确提供的渠道标签"
        ),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="来源dt原表示"),
        sa.Column("business_date", sa.Date(), nullable=True, comment="已解释的来源日标签"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="日期解析/核验状态"),
        sa.Column("spend", sa.Numeric(), nullable=True, comment="概览总花费(元)/明星cost"),
        sa.Column(
            "attributed_revenue",
            sa.Numeric(),
            nullable=True,
            comment="概览交易额(元)/明星transaction_amount",
        ),
        sa.Column(
            "net_attributed_revenue", sa.Numeric(), nullable=True, comment="概览净交易额(元)"
        ),
        sa.Column(
            "attributed_deals",
            sa.Numeric(),
            nullable=True,
            comment="概览成交笔数/明星transaction_count",
        ),
        sa.Column("impressions", sa.Numeric(), nullable=True, comment="广告曝光量"),
        sa.Column("clicks", sa.Numeric(), nullable=True, comment="广告点击量"),
        sa.Column(
            "source_updated_time_raw", sa.Text(), nullable=True, comment="概览updated_at原表示"
        ),
        sa.Column(
            "source_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="可解释的来源更新时间",
        ),
        sa.Column(
            "source_created_time_raw", sa.Text(), nullable=True, comment="明星create_time原表示"
        ),
        sa.Column(
            "source_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="可解释的来源创建时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "shop_channel_promotion_daily", "identity")),
        schema="public",
        comment="店铺渠道推广日原行",
    )

    sa.Table(
        "shop_rating_daily",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="评分原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "shop_rating_daily", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="评分输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源id"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "shop_rating_daily", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源shop_id"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源观察店名"),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="原VARCHAR dt"),
        sa.Column("rating_date", sa.Date(), nullable=True, comment="可解析的来源评分日"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="日期解析/核验状态"),
        sa.Column("source_score", sa.Numeric(), nullable=True, comment="shop_rating_score原评分"),
        sa.Column("source_created_time_raw", sa.Text(), nullable=True, comment="create_time原表示"),
        sa.Column(
            "source_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有依据的来源创建/采集时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "shop_rating_daily", "identity")),
        schema="public",
        comment="店铺评分日原行",
    )

    sa.Table(
        "shop_refund_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="退款原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "shop_refund_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="退款输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="飞书record_id等来源标识"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "shop_refund_records", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实经营店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源明确提供的店铺ID"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源店铺标签"),
        sa.Column("source_platform", sa.Text(), nullable=True, comment="本行来源平台维度"),
        sa.Column("source_department", sa.Text(), nullable=True, comment="本行来源部门维度"),
        sa.Column("source_date_raw", sa.Text(), nullable=True, comment="来源日期原表示"),
        sa.Column("business_date", sa.Date(), nullable=True, comment="可解释的统计日标签"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="日期解析/核验状态"),
        sa.Column(
            "reported_refund_amount", sa.Numeric(), nullable=True, comment="统计退款金额原值"
        ),
        sa.Column("source_reported_amount", sa.Numeric(), nullable=True, comment="来源统计金额"),
        sa.Column("source_refund_ratio", sa.Numeric(), nullable=True, comment="来源已算退款占比"),
        sa.Column(
            "source_modified_time_raw",
            sa.Text(),
            nullable=True,
            comment="来源明确提供的修改时间原表示",
        ),
        sa.Column(
            "source_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有依据的来源修改时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "shop_refund_records", "identity")),
        schema="public",
        comment="店铺退款统计原行",
    )

    sa.Table(
        "shop_region_daily",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="后台地区原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "shop_region_daily", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="后台地区输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源id"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "shop_region_daily", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源shop_id"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源观察店名"),
        sa.Column("source_region_name", sa.Text(), nullable=True, comment="来源province标签"),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="来源dt原表示"),
        sa.Column("business_date", sa.Date(), nullable=True, comment="可解释的来源日"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="日期解析/核验状态"),
        sa.Column("transaction_amount", sa.Numeric(), nullable=True, comment="地区成交金额"),
        sa.Column("transaction_orders", sa.Numeric(), nullable=True, comment="地区成交订单"),
        sa.Column("transaction_buyers", sa.Numeric(), nullable=True, comment="地区成交买家"),
        sa.Column("source_price_per_buyer", sa.Numeric(), nullable=True, comment="来源买家均额"),
        sa.Column("source_price_per_order", sa.Numeric(), nullable=True, comment="来源订单均额"),
        sa.Column(
            "source_updated_time_raw", sa.Text(), nullable=True, comment="来源updated_at原表示"
        ),
        sa.Column(
            "source_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有依据的来源更新时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "shop_region_daily", "identity")),
        schema="public",
        comment="店铺地区成交日原行",
    )

    sa.Table(
        "product_region_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="产品地区原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "product_region_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="聚水潭地区输入版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源id"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "product_region_records", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="原shop_id"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="原shop_name"),
        sa.Column("source_style_code", sa.Text(), nullable=True, comment="原style_code"),
        sa.Column("source_product_code", sa.Text(), nullable=True, comment="原product_code"),
        sa.Column("source_product_name", sa.Text(), nullable=True, comment="原product_name"),
        sa.Column(
            "source_color_specification", sa.Text(), nullable=True, comment="原color_specification"
        ),
        sa.Column(
            "source_product_category", sa.Text(), nullable=True, comment="原product_category"
        ),
        sa.Column(
            "source_virtual_category", sa.Text(), nullable=True, comment="原virtual_category"
        ),
        sa.Column("source_region_name", sa.Text(), nullable=True, comment="原province"),
        sa.Column("source_dt_raw", sa.Text(), nullable=True, comment="原dt表示"),
        sa.Column("source_granularity", sa.Text(), nullable=True, comment="原time_granularity"),
        sa.Column("period_start", sa.Date(), nullable=True, comment="已解释的实际统计起日"),
        sa.Column("period_end", sa.Date(), nullable=True, comment="已解释的实际统计末日"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="日期解析/核验状态"),
        sa.Column("actual_shipped_qty", sa.Numeric(), nullable=True, comment="来源实发件数"),
        sa.Column("actual_shipped_amount", sa.Numeric(), nullable=True, comment="来源实发金额"),
        sa.Column("actual_returned_qty", sa.Numeric(), nullable=True, comment="来源实退件数"),
        sa.Column("actual_returned_amount", sa.Numeric(), nullable=True, comment="来源实退金额"),
        sa.Column(
            "source_refund_rate", sa.Numeric(), nullable=True, comment="来源refund_rate核对值"
        ),
        sa.Column(
            "source_updated_time_raw", sa.Text(), nullable=True, comment="来源updated_at原表示"
        ),
        sa.Column(
            "source_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="可解释的来源更新时间",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "product_region_records", "identity")),
        schema="public",
        comment="产品地区实发实退原行",
    )

    sa.Table(
        "product_sales_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="销售期间原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "product_sales_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="一份J2或J3类型化事实版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源原生行ID"),
        sa.Column("row_kind", sa.Text(), nullable=False, comment="来源行的明细/合计性质"),
        sa.Column("source_row_kind", sa.Text(), nullable=True, comment="来源明确提供的行类型标签"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "product_sales_records", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源店铺ID"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源店铺标签"),
        sa.Column("source_style_code", sa.Text(), nullable=True, comment="来源款号/款式编码"),
        sa.Column("source_product_code", sa.Text(), nullable=True, comment="来源商品编码"),
        sa.Column("source_product_name", sa.Text(), nullable=True, comment="正式商品名称原值"),
        sa.Column("source_color_specification", sa.Text(), nullable=True, comment="来源颜色规格"),
        sa.Column("source_product_category", sa.Text(), nullable=True, comment="来源产品分类"),
        sa.Column(
            "source_subcategory",
            sa.Text(),
            nullable=True,
            comment="来源实际参与分组/搜索的子分类原值",
        ),
        sa.Column("source_virtual_category", sa.Text(), nullable=True, comment="来源虚拟分类"),
        sa.Column("source_period_label", sa.Text(), nullable=True, comment="来源本行期间原标签"),
        sa.Column("period_start", sa.Date(), nullable=True, comment="本行明确提供的统计起日"),
        sa.Column("period_end", sa.Date(), nullable=True, comment="本行明确提供的统计末日"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="本行期间核验状态"),
        sa.Column(
            "sales_amount", sa.Numeric(), nullable=True, comment="本版本角色对应的来源销售金额"
        ),
        sa.Column(
            "source_sales_qty", sa.Numeric(), nullable=True, comment="本版本角色对应的来源销售数量"
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "product_sales_records", "identity")),
        schema="public",
        comment="商品销售履约期间原行",
    )

    sa.Table(
        "return_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="售后原行ID"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "return_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="J1类型化事实版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="本版本内原行定位符"),
        sa.Column("source_record_id", sa.Text(), nullable=True, comment="来源原生行ID"),
        sa.Column("row_kind", sa.Text(), nullable=False, comment="明细/小计/合计性质"),
        sa.Column("source_row_kind", sa.Text(), nullable=True, comment="来源实际行类型标签"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "return_records", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核实店铺",
        ),
        sa.Column("shop_match_status", sa.Text(), nullable=False, comment="店铺匹配状态"),
        sa.Column("source_shop_id", sa.Text(), nullable=True, comment="来源店铺ID"),
        sa.Column("source_shop_name", sa.Text(), nullable=True, comment="来源店铺标签"),
        sa.Column("source_style_code", sa.Text(), nullable=True, comment="来源款式编码"),
        sa.Column("source_product_code", sa.Text(), nullable=True, comment="来源商品编码"),
        sa.Column("source_product_name", sa.Text(), nullable=True, comment="正式商品名称原值"),
        sa.Column("source_color_specification", sa.Text(), nullable=True, comment="来源颜色规格"),
        sa.Column("source_product_category", sa.Text(), nullable=True, comment="来源产品分类原值"),
        sa.Column(
            "source_subcategory",
            sa.Text(),
            nullable=True,
            comment="来源实际参与分组/搜索的子分类原值",
        ),
        sa.Column("source_virtual_category", sa.Text(), nullable=True, comment="来源虚拟分类原值"),
        sa.Column("source_return_reason", sa.Text(), nullable=True, comment="来源退货原因原文本"),
        sa.Column(
            "source_analysis_fields",
            JSONB(),
            nullable=True,
            comment="实际分类配置需要的额外原文本投影",
        ),
        sa.Column(
            "source_aftersale_type", sa.Text(), nullable=True, comment="来源明确给出的售后分类"
        ),
        sa.Column(
            "source_aftersale_status", sa.Text(), nullable=True, comment="来源明确给出的售后状态"
        ),
        sa.Column("source_period_label", sa.Text(), nullable=True, comment="来源本行期间标签"),
        sa.Column("period_start", sa.Date(), nullable=True, comment="本行明确提供的确认统计起日"),
        sa.Column("period_end", sa.Date(), nullable=True, comment="本行明确提供的确认统计末日"),
        sa.Column("date_status", sa.Text(), nullable=False, comment="本行期间核验状态"),
        sa.Column("actual_returned_qty", sa.Numeric(), nullable=True, comment="实退数量Q"),
        sa.Column("actual_inbound_amount", sa.Numeric(), nullable=True, comment="实际入库金额A"),
        sa.PrimaryKeyConstraint("id", name=name("pk", "return_records", "identity")),
        schema="public",
        comment="售后退货期间原行",
    )

    sa.Table(
        "sales_targets",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本目标记录标识"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "sales_targets", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="目标原值及解释的规范化版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="原交付中本行的确定定位"),
        sa.Column("source_target_key", sa.Text(), nullable=True, comment="来源已有目标记录ID"),
        sa.Column("source_scope_label", sa.Text(), nullable=True, comment="来源实际范围名称/说明"),
        sa.Column(
            "source_period_label", sa.Text(), nullable=True, comment="来源年份、月份或期间原标签"
        ),
        sa.Column("period_kind", sa.Text(), nullable=False, comment="目标期间类型"),
        sa.Column("period_start", sa.Date(), nullable=True, comment="经核实的目标起日，含当日"),
        sa.Column("period_end", sa.Date(), nullable=True, comment="经核实的目标止日，含当日"),
        sa.Column("period_status", sa.Text(), nullable=False, comment="期间依据状态"),
        sa.Column("target_amount", sa.Numeric(), nullable=False, comment="这一范围的一份目标金额"),
        sa.Column("scope_status", sa.Text(), nullable=False, comment="固定店铺集合核验状态"),
        sa.Column(
            "evidence_note",
            sa.Text(),
            nullable=True,
            comment="原入口、重复月份、范围和期间核验说明",
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "sales_targets", "identity")),
        schema="public",
        comment="销售目标",
    )

    sa.Table(
        "sales_target_shops",
        metadata,
        sa.Column(
            "target_id",
            sa.Text(),
            sa.ForeignKey(
                "public.sales_targets.id",
                name=name("fk", "sales_target_shops", "target_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="所属销售目标",
        ),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "sales_target_shops", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="固定范围内的统一店铺",
        ),
        sa.Column(
            "source_member_key", sa.Text(), nullable=True, comment="原范围中该成员的确定定位"
        ),
        sa.Column("evidence_note", sa.Text(), nullable=True, comment="原别名对应及成员核验说明"),
        sa.PrimaryKeyConstraint(
            "target_id", "shop_id", name=name("pk", "sales_target_shops", "identity")
        ),
        schema="public",
        comment="销售目标店铺成员",
    )

    sa.Table(
        "refund_targets",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本退款目标记录标识"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "refund_targets", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="数值、原范围及解释的不可变版本",
        ),
        sa.Column("source_row_key", sa.Text(), nullable=False, comment="原目标记录的确定定位"),
        sa.Column("source_target_key", sa.Text(), nullable=True, comment="来源已有目标ID"),
        sa.Column("source_scope_label", sa.Text(), nullable=False, comment="原细类/目标范围简称"),
        sa.Column(
            "source_scope_text", sa.Text(), nullable=False, comment="包含、排除及原筛选条件全文"
        ),
        sa.Column("scope_kind", sa.Text(), nullable=False, comment="总体范围是否受限"),
        sa.Column("category_match_mode", sa.Text(), nullable=False, comment="分类包含集合的算子"),
        sa.Column("style_match_mode", sa.Text(), nullable=False, comment="款码包含集合的算子"),
        sa.Column("scope_status", sa.Text(), nullable=False, comment="整份条件及清单核验状态"),
        sa.Column(
            "unresolved_condition_text",
            sa.Text(),
            nullable=True,
            comment="尚未落入可执行条件的原文",
        ),
        sa.Column("source_period_label", sa.Text(), nullable=True, comment="来源适用期间原标签"),
        sa.Column("period_kind", sa.Text(), nullable=False, comment="来源目标期间类型"),
        sa.Column("period_start", sa.Date(), nullable=True, comment="经核实的目标适用起日"),
        sa.Column("period_end", sa.Date(), nullable=True, comment="经核实的目标适用止日"),
        sa.Column("period_status", sa.Text(), nullable=False, comment="目标期间及适用依据状态"),
        sa.Column("target_rate", sa.Numeric(), nullable=False, comment="本组总退款率目标"),
        sa.Column("evidence_note", sa.Text(), nullable=True, comment="数值、期间、清单及范围依据"),
        sa.PrimaryKeyConstraint("id", name=name("pk", "refund_targets", "identity")),
        schema="public",
        comment="退款率目标",
    )

    sa.Table(
        "refund_target_categories",
        metadata,
        sa.Column(
            "target_id",
            sa.Text(),
            sa.ForeignKey(
                "public.refund_targets.id",
                name=name("fk", "refund_target_categories", "target_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="所属退款目标",
        ),
        sa.Column("source_member_key", sa.Text(), nullable=False, comment="原条件的确定定位"),
        sa.Column("effect", sa.Text(), nullable=False, comment="包含或排除"),
        sa.Column("source_category_system", sa.Text(), nullable=False, comment="实际来源分类体系"),
        sa.Column("source_category_key", sa.Text(), nullable=True, comment="原分类编码"),
        sa.Column("source_category_label", sa.Text(), nullable=True, comment="原分类标签"),
        sa.Column(
            "match_basis", sa.Text(), nullable=False, comment="条件实际按编码还是精确标签匹配"
        ),
        sa.Column("condition_status", sa.Text(), nullable=False, comment="本条件是否已明确可执行"),
        sa.Column("evidence_note", sa.Text(), nullable=True, comment="别名、体系或原条件核验依据"),
        sa.PrimaryKeyConstraint(
            "target_id",
            "source_member_key",
            name=name("pk", "refund_target_categories", "identity"),
        ),
        schema="public",
        comment="退款目标分类条件",
    )

    sa.Table(
        "refund_target_styles",
        metadata,
        sa.Column(
            "target_id",
            sa.Text(),
            sa.ForeignKey(
                "public.refund_targets.id",
                name=name("fk", "refund_target_styles", "target_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="所属退款目标",
        ),
        sa.Column("source_member_key", sa.Text(), nullable=False, comment="原名单/原条件中的定位"),
        sa.Column("effect", sa.Text(), nullable=False, comment="包含或排除"),
        sa.Column(
            "source_style_namespace", sa.Text(), nullable=False, comment="款码实际所属来源命名空间"
        ),
        sa.Column("source_style_code", sa.Text(), nullable=False, comment="原来源款式编码"),
        sa.Column("condition_status", sa.Text(), nullable=False, comment="款码及匹配语义核验状态"),
        sa.Column("evidence_note", sa.Text(), nullable=True, comment="清单、别名或编码冲突说明"),
        sa.PrimaryKeyConstraint(
            "target_id", "source_member_key", name=name("pk", "refund_target_styles", "identity")
        ),
        schema="public",
        comment="退款目标款码条件",
    )

    sa.Table(
        "rule_sets",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="规则包修订标识"),
        sa.Column("set_code", sa.Text(), nullable=False, comment="同一用途规则包的稳定代码"),
        sa.Column("revision", sa.Text(), nullable=False, comment="本包修订号"),
        sa.Column("domain", sa.Text(), nullable=False, comment="包所属业务域"),
        sa.Column("schema_revision", sa.Text(), nullable=False, comment="参数结构的确切版本"),
        sa.Column("parameters", JSONB(), nullable=False, comment="该修订完整结构化参数"),
        sa.Column("confirmation_status", sa.Text(), nullable=False, comment="整包业务确认状态"),
        sa.Column(
            "source_locator", sa.Text(), nullable=False, comment="本包依据的确切SOP/契约/交付定位"
        ),
        sa.Column(
            "source_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="真实观察该依据的时刻",
        ),
        sa.Column(
            "confirmation_ref",
            sa.Text(),
            nullable=True,
            comment="整包确认或逐分支确认记录的固定引用",
        ),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), nullable=True, comment="真实整包确认时刻"
        ),
        sa.Column(
            "content_sha256", sa.Text(), nullable=False, comment="结构版本及参数正文的内容摘要"
        ),
        sa.Column(
            "evidence_note", sa.Text(), nullable=True, comment="已确认范围、未知项及适用边界"
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "rule_sets", "identity")),
        schema="public",
        comment="不可变规则参数包",
    )

    sa.Table(
        "score_standards",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本标准修订标识"),
        sa.Column("standard_code", sa.Text(), nullable=False, comment="标准系列的稳定代码"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "score_standards", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="标准参数及出处的可重读版本",
        ),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey(
                "public.listings.id",
                name=name("fk", "score_standards", "listing_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="已核原绑定的店铺商品链接",
        ),
        sa.Column(
            "source_listing_key", sa.Text(), nullable=True, comment="标准声明的原完整店铺商品定位"
        ),
        sa.Column("source_category_system", sa.Text(), nullable=False, comment="原绑定的品类体系"),
        sa.Column(
            "source_category_label", sa.Text(), nullable=False, comment="原绑定的运营品类原标签"
        ),
        sa.Column("model_category_label", sa.Text(), nullable=True, comment="历史模型品类声明"),
        sa.Column("season_label", sa.Text(), nullable=True, comment="历史季节上下文"),
        sa.Column("lifecycle_label", sa.Text(), nullable=True, comment="历史生命周期上下文"),
        sa.Column("basis_kind", sa.Text(), nullable=False, comment="标准依据类别"),
        sa.Column(
            "identity_match_status", sa.Text(), nullable=False, comment="原链接及品类核验状态"
        ),
        sa.Column(
            "source_effective_date", sa.Date(), nullable=True, comment="有证据的标准适用起日"
        ),
        sa.Column("source_date_status", sa.Text(), nullable=False, comment="上述日期依据状态"),
        sa.Column(
            "source_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="实际观察标准出处的时刻",
        ),
        sa.Column("source_locator", sa.Text(), nullable=False, comment="原标准页面/交付的确切定位"),
        sa.Column("evidence_note", sa.Text(), nullable=True, comment="原精度、适用边界及缺口"),
        sa.PrimaryKeyConstraint("id", name=name("pk", "score_standards", "identity")),
        schema="public",
        comment="新品评分标准",
    )

    sa.Table(
        "score_standard_items",
        metadata,
        sa.Column(
            "standard_id",
            sa.Text(),
            sa.ForeignKey(
                "public.score_standards.id",
                name=name("fk", "score_standard_items", "standard_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="所属不可变标准",
        ),
        sa.Column("metric_code", sa.Text(), nullable=False, comment="十项评分中的一个有限指标代码"),
        sa.Column("basic_threshold", sa.Numeric(), nullable=True, comment="基础档下限"),
        sa.Column("good_threshold", sa.Numeric(), nullable=True, comment="良好档下限"),
        sa.Column("excellent_threshold", sa.Numeric(), nullable=True, comment="优秀档下限"),
        sa.Column("outstanding_threshold", sa.Numeric(), nullable=True, comment="突出档下限"),
        sa.Column(
            "growth_base_floor", sa.Numeric(), nullable=True, comment="交易倍数项的前7日金额基数线"
        ),
        sa.Column("threshold_status", sa.Text(), nullable=False, comment="本项四线可用性"),
        sa.Column("precision_kind", sa.Text(), nullable=False, comment="阈值精度出处"),
        sa.Column(
            "reference_group_label", sa.Text(), nullable=True, comment="本项实际参照组原声明"
        ),
        sa.Column(
            "reference_category_system",
            sa.Text(),
            nullable=True,
            comment="本项借用/参照的原分类体系",
        ),
        sa.Column(
            "reference_category_label",
            sa.Text(),
            nullable=True,
            comment="本项借用/参照的原品类标签",
        ),
        sa.Column(
            "declared_sample_count", sa.BigInteger(), nullable=True, comment="来源声明的参照样本数"
        ),
        sa.Column("declared_sample_unit", sa.Text(), nullable=True, comment="原样本单位声明"),
        sa.Column("sample_basis_status", sa.Text(), nullable=False, comment="样本口径核验状态"),
        sa.Column("history_start", sa.Date(), nullable=True, comment="有证据的参照历史起日"),
        sa.Column("history_end", sa.Date(), nullable=True, comment="有证据的参照历史止日"),
        sa.Column("quantile_method", sa.Text(), nullable=True, comment="有证据的分位/插值方法"),
        sa.Column("fallback_level", sa.Text(), nullable=True, comment="本项实际回退层级/声明"),
        sa.Column("fallback_basis_status", sa.Text(), nullable=False, comment="回退依据状态"),
        sa.Column("source_locator", sa.Text(), nullable=False, comment="本指标四线和参照的原定位"),
        sa.Column("missing_reason", sa.Text(), nullable=True, comment="缺线、单位或参照冲突说明"),
        sa.PrimaryKeyConstraint(
            "standard_id", "metric_code", name=name("pk", "score_standard_items", "identity")
        ),
        schema="public",
        comment="逐指标四线与参照",
    )

    sa.Table(
        "product_scores",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本评分结果定位"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "product_scores", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="本评分结果不可变版本",
        ),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey(
                "public.listings.id",
                name=name("fk", "product_scores", "listing_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="被评分的完整店铺商品链接",
        ),
        sa.Column(
            "standard_id",
            sa.Text(),
            sa.ForeignKey(
                "public.score_standards.id",
                name=name("fk", "product_scores", "standard_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="本次实际适用标准修订",
        ),
        sa.Column("visitors_7d", sa.BigInteger(), nullable=True, comment="当期7日商品访客累计特征"),
        sa.Column("product_revenue_7d", sa.Numeric(), nullable=True, comment="当期7日商品交易额"),
        sa.Column("units_7d", sa.BigInteger(), nullable=True, comment="当期7日支付件数"),
        sa.Column(
            "favorites_7d", sa.BigInteger(), nullable=True, comment="本次实际观察的7日收藏聚合特征"
        ),
        sa.Column("buyers_7d", sa.BigInteger(), nullable=True, comment="当期7日买家累计核对特征"),
        sa.Column("orders_7d", sa.BigInteger(), nullable=True, comment="当期7日订单累计特征"),
        sa.Column("ad_spend_7d", sa.Numeric(), nullable=True, comment="当期7日推广总花费"),
        sa.Column(
            "ad_attributed_revenue_7d", sa.Numeric(), nullable=True, comment="当期7日总归因成交额"
        ),
        sa.Column("ad_impressions_7d", sa.Numeric(), nullable=True, comment="当期7日推广曝光合计"),
        sa.Column("ad_clicks_7d", sa.Numeric(), nullable=True, comment="当期7日推广点击合计"),
        sa.Column("ad_deals_7d", sa.Numeric(), nullable=True, comment="当期7日总归因成交笔数"),
        sa.Column("previous_revenue_7d", sa.Numeric(), nullable=True, comment="前7日商品交易额"),
        sa.Column(
            "previous_orders_7d", sa.BigInteger(), nullable=True, comment="前7日订单累计特征"
        ),
        sa.Column(
            "current_lifecycle", sa.Text(), nullable=True, comment="本次有真实依据的阶段分类结果"
        ),
        sa.Column(
            "lifecycle_reason", sa.Text(), nullable=True, comment="当前阶段判定依据或未知原因"
        ),
        sa.Column(
            "decision_code", sa.Text(), nullable=False, comment="本次确定性门控的实际输出分支"
        ),
        sa.Column(
            "decision_evidence", sa.Text(), nullable=True, comment="本次分支及关键门控的具体原因"
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "product_scores", "identity")),
        schema="public",
        comment="商品评分对象及实际特征",
    )

    sa.Table(
        "product_score_items",
        metadata,
        sa.Column(
            "score_id",
            sa.Text(),
            sa.ForeignKey(
                "public.product_scores.id",
                name=name("fk", "product_score_items", "score_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="所属商品评分结果",
        ),
        sa.Column("metric_code", sa.Text(), nullable=False, comment="固定十项中的指标代码"),
        sa.Column("signal_status", sa.Text(), nullable=False, comment="本项本次可用状态"),
        sa.Column("missing_reason_code", sa.Text(), nullable=True, comment="缺项/不可计分原因代码"),
        sa.Column(
            "missing_reason_text", sa.Text(), nullable=True, comment="具体缺失字段、日期或标准解释"
        ),
        sa.Column("evidence_context", JSONB(), nullable=True, comment="本项核验所需非数值证据"),
        sa.PrimaryKeyConstraint(
            "score_id", "metric_code", name=name("pk", "product_score_items", "identity")
        ),
        schema="public",
        comment="评分分项状态与缺项证据",
    )

    sa.Table(
        "aftersales_reports",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本份报告修订标识"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "aftersales_reports", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="本报告固定输入及规则的结果版本",
        ),
        sa.Column("report_type", sa.Text(), nullable=False, comment="实际报告类型"),
        sa.Column("period_start", sa.Date(), nullable=False, comment="本报告选定起日，含当日"),
        sa.Column("period_end", sa.Date(), nullable=False, comment="本报告选定止日，含当日"),
        sa.Column(
            "revision_no", sa.BigInteger(), nullable=False, comment="同类型/期间内本地修订序号"
        ),
        sa.Column("source_report_key", sa.Text(), nullable=True, comment="来源已有的报告身份"),
        sa.Column("source_label", sa.Text(), nullable=True, comment="来源报告原标签"),
        sa.Column(
            "source_generated_text", sa.Text(), nullable=True, comment="来源实际生成时间原文本"
        ),
        sa.Column(
            "source_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有依据规范化的来源生成时刻",
        ),
        sa.Column(
            "source_generated_time_status",
            sa.Text(),
            nullable=False,
            comment="来源生成时间解释状态",
        ),
        sa.Column(
            "source_generated_time_note",
            sa.Text(),
            nullable=True,
            comment="原时间无法规范化的原因/解释",
        ),
        sa.Column("document_url", sa.Text(), nullable=True, comment="实际飞书报告文档链接"),
        sa.Column("sheet_url", sa.Text(), nullable=True, comment="实际分析表格链接"),
        sa.Column(
            "previous_report_id",
            sa.Text(),
            sa.ForeignKey(
                "public.aftersales_reports.id",
                name=name("fk", "aftersales_reports", "previous_report_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="本次实际绑定的同类型上期报告",
        ),
        sa.Column(
            "previous_comparison_status", sa.Text(), nullable=False, comment="上期比较可用状态"
        ),
        sa.Column(
            "previous_comparison_reason",
            sa.Text(),
            nullable=True,
            comment="上期缺失/不相容/未判定原因",
        ),
        sa.Column(
            "yoy_report_id",
            sa.Text(),
            sa.ForeignKey(
                "public.aftersales_reports.id",
                name=name("fk", "aftersales_reports", "yoy_report_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="本次实际绑定的去年同类型同期报告",
        ),
        sa.Column("yoy_comparison_status", sa.Text(), nullable=False, comment="去年比较可用状态"),
        sa.Column(
            "yoy_comparison_reason", sa.Text(), nullable=True, comment="去年比较缺失或不相容原因"
        ),
        sa.Column(
            "evidence_note", sa.Text(), nullable=True, comment="真实范围、修订、比较及必要限制"
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "aftersales_reports", "identity")),
        schema="public",
        comment="售后报告头",
    )

    sa.Table(
        "dataset_versions",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="版本标识"),
        sa.Column("dataset_code", sa.Text(), nullable=False, comment="逻辑数据集代码"),
        sa.Column("origin_kind", sa.Text(), nullable=False, comment="来源接收或派生产物"),
        sa.Column("state", sa.Text(), nullable=False, comment="校验与发布状态"),
        sa.Column("source_system", sa.Text(), nullable=True, comment="实际物理来源系统"),
        sa.Column("source_locator", sa.Text(), nullable=True, comment="实际交付/表/工作表定位"),
        sa.Column(
            "row_schema_revision", sa.Text(), nullable=False, comment="本版本必要投影的字段结构修订"
        ),
        sa.Column(
            "interpretation_ref",
            sa.Text(),
            nullable=False,
            comment="已冻结的字段、范围及口径解释证据",
        ),
        sa.Column(
            "producer_revision", sa.Text(), nullable=False, comment="实际接收器或计算代码修订"
        ),
        sa.Column(
            "rule_set_id",
            sa.Text(),
            sa.ForeignKey(
                "public.rule_sets.id",
                name=name("fk", "dataset_versions", "rule_set_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="实际使用的解释/计算规则参数包",
        ),
        sa.Column(
            "content_fingerprint", sa.Text(), nullable=True, comment="内容及语义输入的稳定摘要"
        ),
        sa.Column(
            "retained_input_ref", sa.Text(), nullable=True, comment="必要原交付的受控留存位置"
        ),
        sa.Column(
            "upstream_revision", sa.Text(), nullable=True, comment="上游明确提供的版本/交付修订"
        ),
        sa.Column(
            "period_start", sa.Date(), nullable=True, comment="上游或报告明确声明的实际覆盖起日"
        ),
        sa.Column("period_end", sa.Date(), nullable=True, comment="明确声明的实际覆盖止日"),
        sa.Column("source_cursor", sa.Text(), nullable=True, comment="已核可靠的上游续读位置"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="该份source内容首次实际接收完成时刻",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="本地候选版本实际创建时刻",
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.Column(
            "sealed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="内容及输入绑定实际封版时刻",
        ),
        sa.Column(
            "validation_evidence_ref", sa.Text(), nullable=True, comment="校验通过或拒绝的脱敏证据"
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "dataset_versions", "identity")),
        schema="public",
        comment="不可变数据集版本",
    )

    sa.Table(
        "version_inputs",
        metadata,
        sa.Column(
            "output_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "version_inputs", "output_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="派生输出版本",
        ),
        sa.Column("input_role", sa.Text(), nullable=False, comment="该输入在输出中的明确业务角色"),
        sa.Column(
            "input_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "version_inputs", "input_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="实际使用的已封版输入",
        ),
        sa.Column("window_start", sa.Date(), nullable=True, comment="此角色实际使用/请求的起日"),
        sa.Column("window_end", sa.Date(), nullable=True, comment="此角色实际使用/请求的止日"),
        sa.Column("usage_note", sa.Text(), nullable=True, comment="特殊范围或兼容性证据说明"),
        sa.PrimaryKeyConstraint(
            "output_version_id", "input_role", name=name("pk", "version_inputs", "identity")
        ),
        schema="public",
        comment="输出与输入版本的角色血缘",
    )

    sa.Table(
        "current_datasets",
        metadata,
        sa.Column("dataset_code", sa.Text(), nullable=False, comment="逻辑数据集代码"),
        sa.Column(
            "version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "current_datasets", "version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="当前已发布版本",
        ),
        sa.Column(
            "revision",
            sa.BigInteger(),
            nullable=False,
            comment="该入口的并发修订号",
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, comment="入口实际成功切换时刻"
        ),
        sa.PrimaryKeyConstraint("dataset_code", name=name("pk", "current_datasets", "identity")),
        schema="public",
        comment="逻辑数据集当前入口",
    )

    sa.Table(
        "task_runs",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="本次实际尝试标识"),
        sa.Column("job_code", sa.Text(), nullable=False, comment="实际任务种类"),
        sa.Column("request_ref", sa.Text(), nullable=False, comment="该尝试获准请求的冻结描述"),
        sa.Column(
            "retry_of_id",
            sa.Text(),
            sa.ForeignKey(
                "public.task_runs.id",
                name=name("fk", "task_runs", "retry_of_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="直接重试的前次尝试",
        ),
        sa.Column("lock_key", sa.Text(), nullable=False, comment="会相互冲突的实际任务范围键"),
        sa.Column("status", sa.Text(), nullable=False, comment="实际执行状态"),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="实际入队/登记时刻",
            server_default=sa.text("transaction_timestamp()"),
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True, comment="实际开始执行时刻"
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="实际结束或宣布遗失的时刻",
        ),
        sa.Column("worker_ref", sa.Text(), nullable=True, comment="当前/最后实际执行者标记"),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="本次运行租约截止时刻",
        ),
        sa.Column(
            "result_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "task_runs", "result_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="本次产生或复用的版本",
        ),
        sa.Column(
            "source_updated_text",
            sa.Text(),
            nullable=True,
            comment="本次实际看到的来源更新时间原文",
        ),
        sa.Column(
            "source_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="有时区依据可解析的来源更新时间",
        ),
        sa.Column(
            "source_update_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="本次观察到上述原文的实际时刻",
        ),
        sa.Column("error_code", sa.Text(), nullable=True, comment="失败/取消/遗失的稳定原因代码"),
        sa.Column("error_summary", sa.Text(), nullable=True, comment="脱敏的人可读原因"),
        sa.Column("evidence_ref", sa.Text(), nullable=True, comment="实际执行及失败排查证据"),
        sa.PrimaryKeyConstraint("id", name=name("pk", "task_runs", "identity")),
        schema="public",
        comment="同步或计算的一次实际尝试",
    )

    sa.Table(
        "coverage_records",
        metadata,
        sa.Column("id", sa.Text(), nullable=False, comment="覆盖证据标识"),
        sa.Column(
            "dataset_version_id",
            sa.Text(),
            sa.ForeignKey(
                "public.dataset_versions.id",
                name=name("fk", "coverage_records", "dataset_version_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=False,
            comment="被判断的固定版本",
        ),
        sa.Column("scope_kind", sa.Text(), nullable=False, comment="证据适用对象层级"),
        sa.Column(
            "shop_id",
            sa.Text(),
            sa.ForeignKey(
                "public.shops.id",
                name=name("fk", "coverage_records", "shop_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="店铺范围",
        ),
        sa.Column("channel_code", sa.Text(), nullable=True, comment="店铺渠道范围"),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey(
                "public.listings.id",
                name=name("fk", "coverage_records", "listing_id"),
                ondelete="RESTRICT",
                onupdate="NO ACTION",
            ),
            nullable=True,
            comment="商品链接范围",
        ),
        sa.Column("field_code", sa.Text(), nullable=True, comment="必要字段级证据对象"),
        sa.Column("window_start", sa.Date(), nullable=True, comment="本证据覆盖窗口起日"),
        sa.Column("window_end", sa.Date(), nullable=True, comment="本证据覆盖窗口止日"),
        sa.Column(
            "availability_state", sa.Text(), nullable=False, comment="数据存在/缺失的具体状态"
        ),
        sa.Column("completeness_state", sa.Text(), nullable=False, comment="此范围是否完整"),
        sa.Column("complete_through", sa.Date(), nullable=True, comment="有独立证明的完整截至日"),
        sa.Column(
            "expected_record_count",
            sa.BigInteger(),
            nullable=True,
            comment="上游/业务约定明确提供的应有记录数",
        ),
        sa.Column(
            "expectation_ref", sa.Text(), nullable=True, comment="应有范围、行数或完整日的权威依据"
        ),
        sa.Column("reason_code", sa.Text(), nullable=False, comment="该状态的稳定解释代码"),
        sa.Column("evidence_ref", sa.Text(), nullable=False, comment="这次覆盖判断的脱敏证明"),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=False, comment="判断实际完成时刻"
        ),
        sa.PrimaryKeyConstraint("id", name=name("pk", "coverage_records", "identity")),
        schema="public",
        comment="固定版本范围的独立覆盖证据",
    )
