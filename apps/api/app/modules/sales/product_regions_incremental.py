"""商品地区兼容入口；共享销售窗口接收与原40万行预算。"""

from .contracts import SalesError
from .datasets import PRODUCT_REGION_SOURCE_MAX_ROWS
from .window_incremental import receive_sales_window


def receive_product_regions(
    store, read, *, heartbeat=lambda: None, full=False, repair_dates=(), repair_only=False
):
    """保留商品地区调用边界；完整/回看均使用同一候选发布流程。"""
    if store.code != "product_regions":
        raise SalesError("INVALID_INCREMENTAL_DATASET")
    return receive_sales_window(
        store,
        read,
        heartbeat=heartbeat,
        full=full,
        repair_dates=repair_dates,
        repair_only=repair_only,
        row_limit=PRODUCT_REGION_SOURCE_MAX_ROWS,
    )
