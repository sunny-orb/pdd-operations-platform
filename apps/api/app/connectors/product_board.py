"""M1三表有限只读连接，单连接两遍完整必要投影，变化时再核对一次。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pymysql

from app.connectors.retry import RetryBudget, execute_read
from app.modules.product_board.source import (
    AD_NUM,
    COLUMNS,
    PRODUCT_NUM,
    TABLES,
    fingerprint,
    normalize,
    windows,
)
from app.modules.sales.source_policy import SHOPS


class ProductBoardReader:
    def __init__(self, *, password, heartbeat=lambda: None):
        self.connection = pymysql.connect(
            host="14.103.40.80",
            port=9030,
            user="ro",
            password=password,
            database="cubeappdata",
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=8,
            read_timeout=35,
            write_timeout=8,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.heartbeat = heartbeat
        self.retry_budget = RetryBudget()

    def close(self):
        self.connection.close()

    def latest(self, selected=None):
        selected = ("product_daily", "product_promotion") if selected is None else selected
        yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
        latest = []
        for code in selected:
            if code == "profile_records":
                continue
            self.heartbeat()
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    f"SELECT MAX(dt) latest FROM `{TABLES[code]}` WHERE time_granularity=%s "
                    "AND shop_id IN (%s,%s,%s,%s,%s,%s)",
                    ("day", *SHOPS),
                    getattr(self, "retry_budget", None),
                    self.heartbeat,
                    stage="latest",
                    source=code,
                )
                value = c.fetchone()["latest"]
                if not value:
                    raise ValueError("SOURCE_NO_DATES")
                latest.append(value.date())
        # 接收覆盖两源最新观察；页面分别按事实MAX日期展示，不以较慢源截断。
        return min(max(latest), yesterday) if latest else yesterday

    def projection(self, code, end):
        numbers = set(PRODUCT_NUM.values()) if code == "product_daily" else set(AD_NUM.values())
        columns = ",".join(
            f"CAST(`{k}` AS CHAR) AS `{k}`" if k in numbers else f"`{k}`" for k in COLUMNS[code]
        )
        key = "__id" if code == "profile_records" else "z_id" if code == "product_daily" else "id"
        result = []
        periods = (None,) if code == "profile_records" else windows(end, code)
        for period in periods:
            offset = 0
            while True:
                self.heartbeat()
                clause = (
                    ""
                    if period is None
                    else (
                        "WHERE time_granularity=%s AND shop_id IN (%s,%s,%s,%s,%s,%s) "
                        "AND dt>=%s AND dt<%s"
                    )
                )
                params = (
                    ()
                    if period is None
                    else ("day", *SHOPS, period[0], period[1] + timedelta(days=1))
                )
                with self.connection.cursor() as c:
                    execute_read(
                        c,
                        f"SELECT {columns} FROM `{TABLES[code]}` {clause} "
                        f"ORDER BY `{key}` LIMIT %s OFFSET %s",
                        (*params, 1000, offset),
                        self.retry_budget,
                        self.heartbeat,
                        stage="projection",
                        source=code,
                    )
                    batch = c.fetchall()
                result.extend(batch)
                if len(result) > 300000:
                    raise ValueError("SOURCE_ROW_LIMIT")
                if len(batch) < 1000:
                    break
                offset += 1000
        return normalize(code, result, end)

    def read_stable(self, selected=None):
        """两次读取选中来源；MAX日期只用于批准窗口，不作为上游更新标记。"""
        selected = tuple(TABLES) if selected is None else tuple(selected)
        if not selected or not set(selected) <= TABLES.keys():
            raise ValueError("INVALID_SOURCE_SELECTION")
        end = self.latest(selected)
        for _ in range(2):
            first = {code: self.projection(code, end) for code in selected}
            second = {code: self.projection(code, end) for code in selected}
            if fingerprint(first) == fingerprint(second):
                return end, second
        raise ValueError("SOURCE_CHANGED_DURING_READ")
