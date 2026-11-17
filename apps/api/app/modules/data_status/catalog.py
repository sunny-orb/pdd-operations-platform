"""只列实际在用的物理来源；同表多用途合并，继承连接器唯一定位。"""

from dataclasses import dataclass

from app.connectors.weather import BULLETIN_URL, MID_RANGE_URL
from app.modules.aftersales.ingestion import MASTER_TABLE
from app.modules.aftersales.ingestion import TABLES as JST_TABLES
from app.modules.product_board.source import TABLES
from app.modules.sales.datasets import REFUND_BASE, REFUND_TABLE, SPECS
from app.modules.sales.source_policy import SOURCE_TABLE
from app.modules.sales.targets import TARGET_BASE, TARGET_TABLE


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    system: str
    locator: str
    datasets: tuple[str, ...]
    group: str
    pages: tuple[str, ...]
    kind: str = "daily"
    monitor_only: bool = False


def warehouse(code, name, table, datasets, group, pages, kind="daily"):
    return Source(code, name, "warehouse", "cubeappdata." + table, datasets, group, pages, kind)


SOURCES = (
    warehouse(
        "shop_daily",
        "店铺日经营",
        SOURCE_TABLE,
        ("shop_daily",),
        "shop_daily",
        ("overview", "sales", "promotion"),
    ),
    warehouse(
        "product_daily",
        "商品日经营",
        TABLES["product_daily"],
        ("product_daily",),
        "products",
        ("overview", "products"),
    ),
    warehouse(
        "product_promotion",
        "商品推广明细",
        TABLES["product_promotion"],
        ("product_promotion",),
        "products",
        ("overview", "products", "promotion"),
    ),
    warehouse(
        "profile_records",
        "商品上架资料",
        TABLES["profile_records"],
        ("profile_records",),
        "products",
        ("overview", "products", "promotion"),
        "reference",
    ),
    *(
        warehouse(code, name, SPECS[code].source_table, (code,), code, pages)
        for code, name, pages in (
            ("shop_ratings", "店铺评分", ("sales",)),
            (
                "shop_channel_product_promotion",
                "店铺商品推广汇总",
                ("sales", "promotion", "overview"),
            ),
            ("shop_channel_star_shop", "明星店铺推广", ("sales", "promotion", "overview")),
            ("shop_regions", "店铺地区成交", ("sales",)),
            ("product_regions", "商品地区实发实退", ("sales",)),
        )
    ),
    Source(
        "shop_refunds",
        "销售日报 · 店铺退款",
        "feishu",
        f"Feishu Base/{REFUND_BASE}/{REFUND_TABLE}",
        ("shop_refunds",),
        "shop_refunds",
        ("sales", "overview"),
    ),
    Source(
        "sales_targets",
        "拼多多渠道 GMV 目标",
        "feishu",
        f"Feishu Base/{TARGET_BASE}/{TARGET_TABLE}",
        ("sales_targets",),
        "sales_targets",
        ("sales", "overview"),
        "target",
    ),
    Source(
        "aftersales_rpa",
        "RPA售后报表",
        "rpa",
        "public.aftersales_reports_rpa",
        ("rpa_returns", "rpa_shipped_sales", "rpa_cancelled_sales"),
        "aftersales_rpa",
        ("aftersales", "overview"),
        "period",
        monitor_only=True,
    ),
    warehouse(
        "jst_returns",
        "聚水潭售后退货",
        JST_TABLES["jst_returns"],
        ("jst_returns",),
        "aftersales",
        ("aftersales", "overview"),
    ),
    warehouse(
        "jst_sales",
        "聚水潭销售 · 已发货 / 已取消",
        JST_TABLES["jst_shipped_sales"],
        ("jst_shipped_sales", "jst_cancelled_sales"),
        "aftersales",
        ("aftersales", "overview"),
    ),
    warehouse(
        "jst_master",
        "聚水潭货品及分类资料",
        MASTER_TABLE,
        ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales"),
        "aftersales",
        ("aftersales", "overview"),
        "reference",
    ),
    Source(
        "weather_bulletin",
        "中央气象台 · 天气公报",
        "nmc",
        BULLETIN_URL,
        ("weather_publication",),
        "weather",
        ("overview",),
        "publication",
    ),
    Source(
        "weather_mid_range",
        "中央气象台 · 中期预报",
        "nmc",
        MID_RANGE_URL,
        ("weather_publication",),
        "weather",
        ("overview",),
        "publication",
    ),
)
BY_ID = {s.id: s for s in SOURCES}

# 网页原名与入口按2026-11-04实读登记；时间属于运行证据，不硬编码进目录。
WAREHOUSE_PAGES = {
    "shop_daily": ("ODS_流量_拼多多店铺流量概览表(拼多多RPA)", "cAc3IVT4i3"),
    "product_daily": ("BWD_流量_拼多多商品经营指标明细表(拼多多RPA)", "pvKk7pwVwt"),
    "product_promotion": ("BWD_广告_拼多多商品推广报表明细表(拼多多RPA)", "lZAtqRsy0R"),
    "profile_records": (
        "多维表格_主数据_拼多多-商品上架及多店铺商品映射表（飞书Base）",
        "f24Gev8TgN",
    ),
    "shop_ratings": ("ODS_服务_拼多多店铺评价概览表(拼多多RPA)", "1OMxr9q3VL"),
    "shop_channel_product_promotion": (
        "ODS_广告_拼多多店铺商品推广概览表(拼多多RPA)",
        "WeuYq9W2IS",
    ),
    "shop_channel_star_shop": ("ODS_广告_拼多多明星店铺推广日报表(拼多多RPA)", "fewtsqJwmN"),
    "shop_regions": ("BWD_交易_拼多多后台地区销售汇总表(拼多多RPA)", "OYO8zcwXAw"),
    "product_regions": ("BWD_交易_聚水潭拼多多地区销售额数据汇总(拼多多RPA)", "ARMR5iWDUz"),
    "jst_returns": ("DWD_交易_聚水潭系统售后商品明细表", "wMeiTNCs6V"),
    "jst_sales": ("加工表_电商运营销售明细表", "xihXkZKgJs10"),
    "jst_master": ("STD_主数据_商品规格信息表(聚水潭标准API)", "OqA5YCsbzz"),
}
FEISHU_NAMES = {
    "shop_refunds": "拼多多销售统计（日报表）",
    "sales_targets": "拼多多月度销售+利润进度表",
}


def source_identity(source):
    """返回固定清单的可读表名、网页原名与已核入口；不从上游动态取配置。"""
    if source.system == "rpa":
        return dict(
            table_name=source.locator.removeprefix("public."),
            source_title=source.name,
            source_url=None,
        )
    if source.system == "warehouse":
        title, table_id = WAREHOUSE_PAGES[source.id]
        return dict(
            table_name=source.locator.removeprefix("cubeappdata."),
            source_title=title,
            source_url=f"https://bi.kktree.cn/#/data_source?tableId={table_id}",
        )
    if source.system == "feishu":
        _, base, table = source.locator.split("/")
        return dict(
            table_name=FEISHU_NAMES[source.id],
            source_title=FEISHU_NAMES[source.id],
            source_url=f"https://kocotree.feishu.cn/base/{base}?table={table}",
        )
    return dict(
        table_name=source.name.split(" · ")[-1], source_title=source.name, source_url=source.locator
    )


DATASET_NAMES = {s.datasets[0]: s.name for s in SOURCES if len(s.datasets) == 1}
DATASET_NAMES.update(
    rpa_returns="售后退货",
    rpa_shipped_sales="已发货销售",
    rpa_cancelled_sales="已取消销售",
    jst_shipped_sales="已发货销售",
    jst_cancelled_sales="已取消销售",
    weather_publication="全国天气",
    product_scores="商品评分",
    aftersales_report="售后分析报告",
    refund_targets="售后退款率目标配置",
)


def job_for(code):
    if code in ("rpa_returns", "rpa_shipped_sales", "rpa_cancelled_sales"):
        return "sync_rpa_aftersales"
    if code in TABLES:
        return "sync_product_board"
    return {
        "weather_publication": "sync_weather",
        "product_scores": "score_product_board",
        "refund_targets": "publish_refund_targets",
        "aftersales_report": None,
    }.get(code, "sync_" + code)
