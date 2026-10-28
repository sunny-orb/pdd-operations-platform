"""JST加工来源串行、60日必要字段双读；不读取个人信息或回写数仓。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.connectors.product_board import ProductBoardReader
from app.connectors.retry import execute_read
from app.modules.aftersales.ingestion import COLUMNS, NUMBERS, TABLES, normalize
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
        raw = self.read(code, end)
        if fingerprint(raw) != fingerprint(self.read(code, end)):
            raise ValueError("SOURCE_CHANGED_DURING_READ")
        return raw, normalize(code, raw, end)
