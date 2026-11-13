"""M3其余数仓来源：固定列、串行分页、完整双读，DOUBLE由原协议文本转Decimal。"""

import json
from datetime import timedelta
from decimal import Decimal

import pymysql
from pymysql.constants import FIELD_TYPE

from app.connectors.retry import RetryBudget, execute_read
from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import (
    PRODUCT_REGION_SOURCE_MAX_ROWS,
    PROMOTION_METRICS,
    SOURCE_MAX_ROWS,
    SOURCE_PAGE_SIZE,
    SPECS,
)
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import MAX_ATTEMPTS, validate_read_window


class SalesSourceReader:
    def __init__(self, *, password, heartbeat=lambda: None):
        converters = pymysql.converters.conversions.copy()
        converters.update({FIELD_TYPE.DOUBLE: Decimal, FIELD_TYPE.FLOAT: Decimal})
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
            conv=converters,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.heartbeat = heartbeat
        self.retry_budget = RetryBudget()
        self.scan = self._scan

    @classmethod
    def for_test(cls, scan):
        reader = cls.__new__(cls)
        reader.scan, reader.heartbeat = scan, lambda: None
        return reader

    def close(self):
        self.connection.close()

    def _scan(self, spec, window):
        max_rows = (
            PRODUCT_REGION_SOURCE_MAX_ROWS if spec.code == "product_regions" else SOURCE_MAX_ROWS
        )
        rows, start = [], window.start
        while start <= window.end:
            stop = min(start + timedelta(days=7), window.end + timedelta(days=1))
            offset = 0
            while True:
                self.heartbeat()
                with self.connection.cursor() as cursor:
                    placeholders = ",".join(["%s"] * len(spec.shop_ids))
                    execute_read(
                        cursor,
                        f"SELECT {','.join(spec.columns)} FROM `{spec.source_table}` WHERE "
                        f"shop_id IN ({placeholders}) AND time_granularity=%s AND dt>=%s AND dt<%s "
                        "ORDER BY dt,shop_id,id LIMIT %s OFFSET %s",
                        (*spec.shop_ids, "day", start, stop, SOURCE_PAGE_SIZE, offset),
                        self.retry_budget,
                        self.heartbeat,
                    )
                    page = cursor.fetchall()
                for row in page:
                    if "json_data" in row:
                        try:
                            data = json.loads(row.pop("json_data"), parse_float=Decimal)
                            if not isinstance(data, dict):
                                raise ValueError
                        except (ValueError, TypeError) as exc:
                            raise SalesError("SOURCE_JSON_INVALID") from exc
                        row["metrics"] = {key: data.get(key) for key in PROMOTION_METRICS}
                    rows.append(row)
                if len(rows) > max_rows:
                    raise SalesError("SOURCE_ROW_LIMIT")
                if len(page) < SOURCE_PAGE_SIZE:
                    break
                offset += SOURCE_PAGE_SIZE
            start = stop
        return rows

    def read_stable(self, code, window):
        validate_read_window(window, code)
        spec = SPECS[code]
        if spec.source_system != "warehouse":
            raise SalesError("SOURCE_READER_MISMATCH")
        for _ in range(MAX_ATTEMPTS):
            before = normalize_source(
                code, self.scan(spec, window), window, self.heartbeat
            ).fingerprint
            after = normalize_source(code, self.scan(spec, window), window, self.heartbeat)
            if before == after.fingerprint:
                return after
        raise SalesError("SOURCE_CHANGED_DURING_READ", 503)
