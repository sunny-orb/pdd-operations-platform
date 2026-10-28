"""JST加工来源串行、60日必要字段双读；不读取个人信息或回写数仓。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.connectors.product_board import ProductBoardReader
from app.connectors.retry import execute_read
from app.modules.aftersales.ingestion import (
    COLUMNS,
    MASTER_COLUMNS,
    MASTER_TABLE,
    NUMBERS,
    TABLES,
    enrich_current_master,
    normalize,
    source_code,
)
from app.modules.sales.datasets import REFUND_SHOPS
from app.modules.sales.normalization import fingerprint


class AftersalesReader(ProductBoardReader):
    def latest(self, code):
        time_col = "confirm_date" if code == "jst_returns" else "pay_time"
        clause = "channel=%s AND shop_name IN (%s,%s,%s,%s,%s,%s)"
        params = ("拼多多", *REFUND_SHOPS)
        if code != "jst_returns":
            clause += " AND order_status=%s"
            params += ("已发货" if code == "jst_shipped_sales" else "取消",)
        with self.connection.cursor() as c:
            execute_read(
                c,
                f"SELECT MAX(`{time_col}`) latest FROM `{TABLES[code]}` WHERE {clause}",
                params,
                self.retry_budget,
                self.heartbeat,
            )
            value = c.fetchone()["latest"]
        if value is None:
            raise ValueError("SOURCE_NO_DATES")
        yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
        return min(value.date(), yesterday)

    def read(self, code, end):
        time_col = "confirm_date" if code == "jst_returns" else "pay_time"
        projection = ",".join(
            f"CAST(`{c}` AS CHAR) AS `{c}`" if c in NUMBERS else f"`{c}`" for c in COLUMNS[code]
        )
        clause = "channel=%s AND shop_name IN (%s,%s,%s,%s,%s,%s)"
        params = ("拼多多", *REFUND_SHOPS)
        if code != "jst_returns":
            clause += " AND order_status=%s"
            params += ("已发货" if code == "jst_shipped_sales" else "取消",)
        result, after = [], -1
        while True:
            self.heartbeat()
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    f"SELECT {projection} FROM `{TABLES[code]}` WHERE {clause} "
                    f"AND `{time_col}`>=%s AND `{time_col}`<%s "
                    "AND __id>%s ORDER BY __id LIMIT 5000",
                    (*params, end - timedelta(days=59), end + timedelta(days=1), after),
                    self.retry_budget,
                    self.heartbeat,
                )
                page = c.fetchall()
            result.extend(page)
            if len(result) > 600000:
                raise ValueError("SOURCE_ROW_LIMIT")
            if len(page) < 5000:
                return result
            after = int(page[-1]["__id"])

    def read_stable(self, code, end):
        """两遍事实与必要当前资料一致后，交付归档证据和既有规范行。"""
        first = self.read(code, end)
        snapshot, master = self.read_current_master(first)
        second = self.read(code, end)
        next_snapshot, next_master = self.read_current_master(second)
        if fingerprint((first, snapshot, master)) != fingerprint(
            (second, next_snapshot, next_master)
        ):
            raise ValueError("SOURCE_CHANGED_DURING_READ")
        raw = enrich_current_master(first, master, snapshot)
        return raw, normalize(code, raw, end)

    def read_current_master(self, raw):
        """只读当前dt的必要普通SKU资料；按SKU取所有候选，跨款或重复由归一化拒绝。"""
        ordinary = "product_type=%s AND (suite_code IS NULL OR suite_code='')"
        with self.connection.cursor() as c:
            execute_read(
                c,
                f"SELECT MAX(dt) snapshot FROM `{MASTER_TABLE}` WHERE {ordinary}",
                ("普通商品",),
                self.retry_budget,
                self.heartbeat,
            )
            snapshot = c.fetchone()["snapshot"]
        if snapshot is None:
            raise ValueError("SOURCE_CURRENT_MASTER_NO_SNAPSHOT")
        snapshot = date.fromisoformat(str(snapshot)).isoformat()
        skus = sorted({source_code(row.get("spec_code")) for row in raw} - {None})
        projection = ",".join(
            "CAST(`__id` AS CHAR) AS `__id`" if col == "__id" else f"`{col}`"
            for col in MASTER_COLUMNS
        )
        master = []
        for offset in range(0, len(skus), 500):
            selected = skus[offset : offset + 500]
            placeholders = ",".join(["%s"] * len(selected))
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    f"SELECT {projection} FROM `{MASTER_TABLE}` WHERE {ordinary} "
                    f"AND dt=%s AND spec_code IN ({placeholders}) ORDER BY __id LIMIT 5001",
                    ("普通商品", snapshot, *selected),
                    self.retry_budget,
                    self.heartbeat,
                )
                page = c.fetchall()
            if len(page) > 5000:
                raise ValueError("SOURCE_CURRENT_MASTER_ROW_LIMIT")
            master.extend(page)
            if len(master) > 600000:
                raise ValueError("SOURCE_CURRENT_MASTER_ROW_LIMIT")
        return snapshot, master
