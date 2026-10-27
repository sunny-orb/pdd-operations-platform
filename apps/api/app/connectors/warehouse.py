"""固定M1三来源的只读连接器；参数绑定、单连接、有限窗口，不执行来源写入。"""

from datetime import date, timedelta

import pymysql

from app.modules.products.ingestion import (
    AD_NUMBERS,
    COLUMNS,
    PRODUCT_NUMBERS,
    SHOPS,
    IngestionError,
    digest,
)

TABLES = {
    "product_daily": "bwd_rpa_pdd_product_detail_f",
    "promotion_records": "bwd_rpa_pdd_goods_report_promotion_detail_f",
    "product_profile_source": "base_spsjb",
}


class WarehouseReader:
    """凭据只留当前进程；连接器输出必要原投影，不导出经营文件。"""

    def __init__(self, *, host: str, port: int, user: str, password: str):
        self.connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database="cubeappdata",
            charset="utf8mb4",
            connect_timeout=8,
            read_timeout=60,
            write_timeout=10,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.query_count = 0

    def close(self):
        self.connection.close()

    def _query(self, sql, parameters=()):
        self.query_count += 1
        with self.connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()

    def validate_schema(self):
        """仅看三张表结构；必要列缺失即停止，不把新字段自动整表搬入。"""
        definitions = {}
        for dataset, table in TABLES.items():
            columns = self._query(f"SHOW COLUMNS FROM `{table}`")
            definitions[dataset] = {row["Field"]: row["Type"] for row in columns}
            if not set(COLUMNS[dataset]) <= set(definitions[dataset]):
                raise IngestionError("SOURCE_SCHEMA_MISMATCH")
        return definitions

    def latest_observed_day(self) -> date:
        """选商品/推广共同已观察上限；不将最大日期标为完整日。"""
        dates = []
        for dataset in ("product_daily", "promotion_records"):
            placeholders = ",".join(["%s"] * len(SHOPS))
            rows = self._query(
                f"SELECT MAX(dt) AS max_dt FROM `{TABLES[dataset]}` "
                f"WHERE time_granularity=%s AND shop_id IN ({placeholders})",
                ("day", *[value[0] for value in SHOPS.values()]),
            )
            if not rows or rows[0]["max_dt"] is None:
                raise IngestionError("NO_OBSERVED_DAY")
            dates.append(rows[0]["max_dt"].date())
        return min(dates)

    def _read(self, dataset, windows):
        table = TABLES[dataset]
        numeric = (
            set(PRODUCT_NUMBERS.values())
            if dataset == "product_daily"
            else set(AD_NUMBERS.values())
        )
        projection = ",".join(
            f"CAST(`{col}` AS CHAR) AS `{col}`" if col in numeric else f"`{col}`"
            for col in COLUMNS[dataset]
        )
        if dataset == "product_profile_source":
            return self._query(f"SELECT {projection} FROM `{table}` ORDER BY __id")
        records = []
        placeholders = ",".join(["%s"] * len(SHOPS))
        source_id = "z_id" if dataset == "product_daily" else "id"
        for start, end in windows:
            current = start
            while current <= end:
                next_day = min(current + timedelta(days=7), end + timedelta(days=1))
                records.extend(
                    self._query(
                        f"SELECT {projection} FROM `{table}` WHERE time_granularity=%s "
                        f"AND shop_id IN ({placeholders}) AND dt>=%s AND dt<%s "
                        f"ORDER BY dt,shop_id,spu_id,`{source_id}`",
                        ("day", *[v[0] for v in SHOPS.values()], current, next_day),
                    )
                )
                if len(records) > 300_000:
                    raise IngestionError("SOURCE_SCOPE_ROW_LIMIT")
                current = next_day
        return records

    def read_stable(self, windows):
        """前后两次核对同一必要投影；失败不发布，不冒称跨来源事务快照。"""
        raw = {key: self._read(key, windows) for key in TABLES}
        for dataset in TABLES:
            again = self._read(dataset, windows)
            before = sorted(digest(row) for row in raw[dataset])
            after = sorted(digest(row) for row in again)
            if before != after:
                raise IngestionError("SOURCE_CHANGED_DURING_READ")
        return raw
