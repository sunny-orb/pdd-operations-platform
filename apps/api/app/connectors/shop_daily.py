"""数仓M3必要投影的单连接有限读取；不执行任何来源写入。"""

import json
from datetime import timedelta
from decimal import Decimal

import pymysql

from app.connectors.retry import RetryBudget, execute_read
from app.modules.sales.contracts import SalesError
from app.modules.sales.source_policy import (
    MAX_ATTEMPTS,
    MAX_ROWS,
    METRICS,
    PAGE_SIZE,
    SOURCE_TABLE,
    validate_read_window,
)
from app.modules.sales.window_incremental import normalize_sales_window
from app.modules.source_read_policy import read_spans


class ShopDailyReader:
    def __init__(self, *, password: str, heartbeat=lambda: None):
        self.connection = pymysql.connect(
            host="14.103.40.80",
            port=9030,
            user="ro",
            password=password,
            database="cubeappdata",
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=8,
            read_timeout=25,
            write_timeout=8,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.heartbeat = heartbeat
        self.retry_budget = RetryBudget()
        self.read = self._read

    @classmethod
    def for_test(cls, read):
        reader = cls.__new__(cls)
        reader.read = read
        return reader

    def close(self):
        self.connection.close()

    def _read(self, window):
        """JSON是来源原子容器，解析后立即只保留十键，不记录其他内容。"""
        records = []
        for start, last in read_spans(window.start, window.end):
            end = last + timedelta(days=1)
            offset, received = 0, 0
            while True:
                self.heartbeat()
                with self.connection.cursor() as cursor:
                    execute_read(
                        cursor,
                        f"SELECT id,dt,time_granularity,shop_id,shop_name,updated_at,"
                        f"json_data FROM `{SOURCE_TABLE}` WHERE time_granularity=%s AND "
                        "shop_id IN (%s,%s,%s,%s,%s,%s) AND dt>=%s AND dt<%s "
                        "ORDER BY dt,shop_id,id LIMIT %s OFFSET %s",
                        ("day", *window.shop_ids, start, end, PAGE_SIZE, offset),
                        self.retry_budget,
                        self.heartbeat,
                    )
                    rows = cursor.fetchall()
                received += len(rows)
                if received > MAX_ROWS:
                    raise SalesError("SOURCE_ROW_LIMIT")
                for row in rows:
                    try:
                        metrics = json.loads(row.pop("json_data"), parse_float=Decimal)
                        if not isinstance(metrics, dict):
                            raise ValueError
                    except (TypeError, ValueError) as exc:
                        raise SalesError("SOURCE_JSON_INVALID") from exc
                    row["metrics"] = {key: metrics.get(key) for key in METRICS}
                    records.append(row)
                if len(rows) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
        return records

    def read_stable(self, window, *, allow_empty=False):
        """两遍完整必要投影相同才返回候选；不是上游快照或完整性保证。"""
        validate_read_window(window)
        for _ in range(MAX_ATTEMPTS):
            before = normalize_sales_window(
                "shop_daily", self.read(window), window, allow_empty=allow_empty
            )
            after = normalize_sales_window(
                "shop_daily", self.read(window), window, allow_empty=allow_empty
            )
            if before.fingerprint == after.fingerprint:
                return after
        raise SalesError("SOURCE_CHANGED_DURING_READ", 503)
