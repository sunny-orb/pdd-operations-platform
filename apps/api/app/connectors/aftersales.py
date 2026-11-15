"""JST加工来源串行、60日必要字段双读；不读取个人信息或回写数仓。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.connectors.product_board import ProductBoardReader
from app.connectors.retry import execute_read
from app.modules.aftersales.bundle_categories import (
    BUNDLE_CATEGORY_COLUMNS,
    BUNDLE_CATEGORY_ROW_LIMIT,
    parent_key,
)
from app.modules.aftersales.ingestion import (
    COLUMNS,
    MASTER_COLUMNS,
    MASTER_TABLE,
    NUMBERS,
    STYLE_CATEGORY_COLUMNS,
    STYLE_CATEGORY_ROW_LIMIT,
    TABLES,
    enrich_current_master,
    normalize,
    source_code,
    style_consensus_eligible,
)
from app.modules.sales.datasets import REFUND_SHOPS
from app.modules.sales.normalization import serializable
from app.modules.streaming_json import fingerprint


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
        """固定六店60日按源数值ID游标完整读取；ID输出字符串，独立源计数防稳定漏行。"""
        time_col = "confirm_date" if code == "jst_returns" else "pay_time"
        projection = ",".join(
            f"CAST(s.`{c}` AS CHAR) AS `{c}`" if c in NUMBERS else f"s.`{c}`" for c in COLUMNS[code]
        )
        clause = "s.channel=%s AND s.shop_name IN (%s,%s,%s,%s,%s,%s)"
        params = ("拼多多", *REFUND_SHOPS)
        if code != "jst_returns":
            clause += " AND s.order_status=%s"
            params += ("已发货" if code == "jst_shipped_sales" else "取消",)
        scope_sql = (
            f"FROM `{TABLES[code]}` AS s WHERE {clause} "
            f"AND s.`{time_col}`>=%s AND s.`{time_col}`<%s"
        )
        scope_params = (*params, end - timedelta(days=59), end + timedelta(days=1))

        def source_scope():
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    "SELECT COUNT(*) AS row_count, MIN(s.__id) AS min_id, "
                    f"MAX(s.__id) AS max_id {scope_sql}",
                    scope_params,
                    self.retry_budget,
                    self.heartbeat,
                )
                row = c.fetchone()
            return {key: int(value) if value is not None else None for key, value in row.items()}

        expected = source_scope()
        if expected["row_count"] > 600000:
            raise ValueError("SOURCE_ROW_LIMIT")
        result, after = [], -1
        while True:
            self.heartbeat()
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    f"SELECT {projection} {scope_sql} AND s.__id>%s ORDER BY s.__id LIMIT 5000",
                    (*scope_params, after),
                    self.retry_budget,
                    self.heartbeat,
                )
                page = c.fetchall()
            for row in page:
                current_id = int(row["__id"])
                if current_id <= after:
                    raise ValueError("SOURCE_ID_ORDER_INVALID")
                after = current_id
            result.extend(page)
            if len(result) > 600000:
                raise ValueError("SOURCE_ROW_LIMIT")
            if len(page) < 5000:
                if source_scope() != expected:
                    raise ValueError("SOURCE_CHANGED_DURING_READ")
                if len(result) != expected["row_count"]:
                    raise ValueError("SOURCE_ROW_COUNT_MISMATCH")
                if result and (
                    int(result[0]["__id"]) != expected["min_id"]
                    or int(result[-1]["__id"]) != expected["max_id"]
                ):
                    raise ValueError("SOURCE_ID_COVERAGE_MISMATCH")
                return result

    def read_stable(self, code, end):
        """两遍事实与必要当前资料一致后，交付归档证据和既有规范行。"""
        first = self.read(code, end)
        snapshot, master = self.read_current_master(first)
        styles = self.read_current_style_categories(first, master, snapshot)
        bundles = self.read_current_bundle_categories(first, master, snapshot)
        first_digest = fingerprint(
            (first, snapshot, master, styles, sorted(bundles.items())), default=serializable
        )
        # 第一遍只保留摘要；第二遍相等后交付相同原值，不同时常驻两份完整事实。
        del first, snapshot, master, styles, bundles
        second = self.read(code, end)
        next_snapshot, next_master = self.read_current_master(second)
        next_styles = self.read_current_style_categories(second, next_master, next_snapshot)
        next_bundles = self.read_current_bundle_categories(second, next_master, next_snapshot)
        if first_digest != fingerprint(
            (second, next_snapshot, next_master, next_styles, sorted(next_bundles.items())),
            default=serializable,
        ):
            raise ValueError("SOURCE_CHANGED_DURING_READ")
        raw = enrich_current_master(second, next_master, next_snapshot, next_styles, next_bundles)
        del second, next_master, next_styles, next_bundles
        return raw, normalize(code, raw, end)

    def read_current_bundle_categories(self, raw, master, snapshot):
        """原款/SKU精确定位组合父键，读取完整原生分类；不查普通子件或展开事实。"""
        checked = enrich_current_master(raw, master, snapshot)
        keys = sorted(
            {
                parent_key(item.get("product_code"), item.get("spec_code"))
                for item in checked
                if style_consensus_eligible(item)
            }
            - {None}
        )
        result = {key: [] for key in keys}
        projection = ",".join(
            "CAST(`__id` AS CHAR) AS `__id`" if col == "__id" else f"`{col}`"
            for col in BUNDLE_CATEGORY_COLUMNS
        )
        total = 0
        for offset in range(0, len(keys), 250):
            selected = keys[offset : offset + 250]
            pairs = " OR ".join(["(suite_code=%s AND suite_spec_code=%s)"] * len(selected))
            scope = f"FROM `{MASTER_TABLE}` WHERE product_type=%s AND dt=%s AND ({pairs})"
            params = ("组合商品", snapshot, *(value for key in selected for value in key))
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    f"SELECT COUNT(*) AS row_count {scope}",
                    params,
                    self.retry_budget,
                    self.heartbeat,
                )
                expected = int(c.fetchone()["row_count"])
                if total + expected > BUNDLE_CATEGORY_ROW_LIMIT:
                    raise ValueError("SOURCE_BUNDLE_CATEGORY_ROW_LIMIT")
                execute_read(
                    c,
                    f"SELECT {projection} {scope} ORDER BY __id LIMIT 5001",
                    params,
                    self.retry_budget,
                    self.heartbeat,
                )
                page = c.fetchall()
            total += len(page)
            if total > BUNDLE_CATEGORY_ROW_LIMIT:
                raise ValueError("SOURCE_BUNDLE_CATEGORY_ROW_LIMIT")
            if len(page) != expected:
                raise ValueError("SOURCE_BUNDLE_CATEGORY_COUNT_MISMATCH")
            for item in page:
                key = parent_key(item.get("suite_code"), item.get("suite_spec_code"))
                if key not in selected:
                    raise ValueError("SOURCE_BUNDLE_CATEGORY_SCOPE_MISMATCH")
                result[key].append(item)
        return result

    def read_current_style_categories(self, raw, master, snapshot):
        """精确SKU未找到时，另读该款同dt全部普通SKU；总计5000上限，绝不截断共识。"""
        checked = enrich_current_master(raw, master, snapshot)
        styles = sorted(
            {
                source_code(row.get("product_code"))
                for row in checked
                if style_consensus_eligible(row)
            }
        )
        result = {style: [] for style in styles}
        projection = ",".join(
            "CAST(`__id` AS CHAR) AS `__id`" if col == "__id" else f"`{col}`"
            for col in STYLE_CATEGORY_COLUMNS
        )
        count = 0
        for offset in range(0, len(styles), 500):
            selected = styles[offset : offset + 500]
            placeholders = ",".join(["%s"] * len(selected))
            with self.connection.cursor() as c:
                execute_read(
                    c,
                    f"SELECT {projection} FROM `{MASTER_TABLE}` WHERE product_type=%s "
                    "AND (suite_code IS NULL OR suite_code='') "
                    f"AND dt=%s AND product_code IN ({placeholders}) ORDER BY __id LIMIT 5001",
                    ("普通商品", snapshot, *selected),
                    self.retry_budget,
                    self.heartbeat,
                )
                page = c.fetchall()
            count += len(page)
            if count > STYLE_CATEGORY_ROW_LIMIT:
                raise ValueError("SOURCE_STYLE_CATEGORY_ROW_LIMIT")
            for item in page:
                style = source_code(item.get("product_code"))
                if style not in selected:
                    raise ValueError("SOURCE_STYLE_CATEGORY_SCOPE_MISMATCH")
                result[style].append(item)
        return result

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
