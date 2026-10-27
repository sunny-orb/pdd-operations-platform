"""仅M3整页已核的固定来源；不是动态来源注册平台。"""

from dataclasses import dataclass

from .source_policy import SHOPS

EXTENDED_REVISION = "m3-full-page-v2-m2-promotion"
SOURCE_PAGE_SIZE, SOURCE_MAX_ROWS, INSERT_BATCH_SIZE = 2000, 300000, 1000
REFUND_BASE = "XGzTbd9xJaASV3soYnycMmf0nY3"
REFUND_TABLE = "tblISBHrFF4jFJqW"
REFUND_FIELDS = (
    "fldodviEvt",
    "fldxlTUMmE",
    "fldDyvIfoA",
    "fldvOjI359",
    "fldu3N8PlZ",
    "fldvlY94V8",
    "fldEgWCTVc",
)
REFUND_SHOPS = dict(
    zip(
        (
            "kk树-kocotree官方旗舰店-配饰",
            "kk树-kocotree运动户外旗舰店-配饰",
            "kk树-kocotree儿童服饰旗舰店-配饰",
            "kk树-kocotree旗舰店-配饰",
            "kk树-母婴玩具-配饰",
            "kk树-kocotree童装童鞋旗舰店-配饰",
        ),
        (value[0] for value in SHOPS.values()),
        strict=True,
    )
)


@dataclass(frozen=True)
class DatasetSpec:
    code: str
    fact_table: str
    source_table: str
    columns: tuple[str, ...]
    date_column: str = "business_date"
    channel: str | None = None
    source_system: str = "warehouse"

    @property
    def locator(self):
        return (
            "cubeappdata." + self.source_table
            if self.source_system == "warehouse"
            else f"Feishu Base/{REFUND_BASE}/{self.source_table}"
        )

    @property
    def shop_ids(self):
        return ("663599807",) if self.channel == "star_shop" else tuple(SHOPS)


IDENTITY = ("id", "dt", "time_granularity", "shop_id", "shop_name")
SPECS = {
    s.code: s
    for s in (
        DatasetSpec(
            "shop_ratings",
            "shop_rating_daily",
            "ods_rpa_pdd_evaluation_overview_f",
            IDENTITY + ("shop_rating_score", "create_time"),
            "rating_date",
        ),
        DatasetSpec(
            "shop_channel_product_promotion",
            "shop_channel_promotion_daily",
            "ods_rpa_pdd_product_promotion_overview_f",
            IDENTITY + ("json_data", "updated_at"),
            channel="product_promotion",
        ),
        DatasetSpec(
            "shop_channel_star_shop",
            "shop_channel_promotion_daily",
            "pdd_star_shop",
            IDENTITY
            + (
                "cost",
                "transaction_amount",
                "transaction_count",
                "impression_count",
                "click_count",
                "create_time",
            ),
            channel="star_shop",
        ),
        DatasetSpec(
            "shop_regions",
            "shop_region_daily",
            "bwd_rpa_pdd_region_sales_summary_pddht_f",
            IDENTITY
            + (
                "province",
                "transaction_amount",
                "transaction_orders",
                "transaction_users",
                "price_per_buyer",
                "price_per_order",
                "updated_at",
            ),
        ),
        DatasetSpec(
            "product_regions",
            "product_region_records",
            "bwd_rpa_pdd_region_sales_summary_jst_f",
            IDENTITY
            + (
                "province",
                "style_code",
                "product_code",
                "product_name",
                "product_category",
                "virtual_category",
                "color_specification",
                "actual_shipped_qty",
                "actual_shipped_amount",
                "actual_returned_qty",
                "actual_returned_amount",
                "refund_rate",
                "updated_at",
            ),
            "period_start",
        ),
        DatasetSpec(
            "shop_refunds",
            "shop_refund_records",
            REFUND_TABLE,
            ("record_id", "日期", "平台", "部门", "店铺", "统计退款金额", "统计金额", "退款占比"),
            source_system="feishu",
        ),
    )
}
PROMOTION_METRICS = {
    "总花费(元)": "spend",
    "交易额(元)": "attributed_revenue",
    "净交易额(元)": "net_attributed_revenue",
    "成交笔数": "attributed_deals",
    "曝光量": "impressions",
    "点击量": "clicks",
}
