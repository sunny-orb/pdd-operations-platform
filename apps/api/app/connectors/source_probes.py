"""固定来源的有限变化检查；信号不等于完整交付，不读取未接入表或私密字段。"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from pymysql import MySQLError

from app.connectors.product_board import ProductBoardReader
from app.connectors.retry import execute_read
from app.connectors.sales_feishu import cli_command
from app.connectors.weather import receive_weather
from app.modules.archive_store import close_temporary, run_source_cli, store_for
from app.modules.data_status.catalog import BY_ID, SOURCES
from app.modules.data_status.schedule_policy import RECONCILIATION_SOURCES
from app.modules.sales.datasets import REFUND_BASE, REFUND_FIELDS, REFUND_SHOPS, SPECS
from app.modules.sales.source_policy import SHOPS, approved_window
from app.modules.sales.targets import TARGET_FIELDS


def warehouse_query(code, end):
    """元信息查询限固定表/六店；近7日变化线索由原每日全量对账补历史盲区。"""
    source = BY_ID[code]
    table = source.locator.removeprefix("cubeappdata.")
    if code == "jst_master":
        return (
            f"SELECT MAX(dt) AS date FROM `{table}` WHERE product_type=%s "
            "AND (suite_code IS NULL OR suite_code='')",
            ("普通商品",),
        )
    start = end - timedelta(days=6)
    if code in ("jst_returns", "jst_sales"):
        column = "confirm_date" if code == "jst_returns" else "pay_time"
        placeholders = ",".join(["%s"] * len(REFUND_SHOPS))
        where = f"channel=%s AND shop_name IN ({placeholders})"
        params = ("拼多多", *REFUND_SHOPS)
        if code == "jst_sales":
            where += " AND order_status IN (%s,%s)"
            params += ("已发货", "取消")
        return (
            f"SELECT MAX(`{column}`) AS date,COUNT(*) AS count FROM `{table}` WHERE {where} "
            f"AND `{column}`>=%s AND `{column}`<%s",
            (*params, start, end + timedelta(days=1)),
        )
    shops = SPECS[code].shop_ids if code in SPECS else tuple(SHOPS)
    updated = (
        "updated_at"
        if code
        in ("shop_daily", "shop_channel_product_promotion", "shop_regions", "product_regions")
        else "create_time"
    )
    placeholders = ",".join(["%s"] * len(shops))
    if code in SPECS or code == "shop_daily":
        window = approved_window(code)
        end, start = min(end, window.end), max(start, window.start)
    return (
        f"SELECT MAX(dt) AS date,COUNT(*) AS count,MAX({updated}) AS source_updated "
        f"FROM `{table}` WHERE time_granularity=%s AND shop_id IN ({placeholders}) "
        "AND dt>=%s AND dt<%s",
        ("day", *shops, start, end + timedelta(days=1)),
    )


def feishu_revision(source, root):
    """沿既有user身份读一行必要字段，使用分页manifest表修订；不取全表记录。"""
    folder = Path(root) / "var/ingestion/source-checks" / str(uuid4())
    folder.mkdir(mode=0o700, parents=True)
    try:
        store_for(folder / "revision.ndjson").check(64 * 1024**2)
        output = folder / "revision.ndjson"
        field = REFUND_FIELDS[0] if source.id == "shop_refunds" else next(iter(TARGET_FIELDS))
        result = run_source_cli(
            cli_command()
            + [
                "base",
                "+record-list",
                "--base-token",
                REFUND_BASE,
                "--table-id",
                source.locator.rsplit("/", 1)[-1],
                "--as",
                "user",
                "--field-id",
                field,
                "--limit",
                "1",
                "--format",
                "ndjson",
                "--output",
                str(output.relative_to(root)),
            ],
            cwd=root,
            capture_output=True,
            timeout=50,
        )
        if result.returncode:
            raise ValueError("FEISHU_CHECK_FAILED")
        manifest = output.with_suffix(".manifest.json")
        output.chmod(0o600)
        manifest.chmod(0o600)
        meta = json.loads(manifest.read_text())
        if (
            meta.get("base_token") != REFUND_BASE
            or meta.get("table_id") != source.locator.rsplit("/", 1)[-1]
            or meta.get("rev") is None
        ):
            raise ValueError("FEISHU_CHECK_METADATA_INVALID")
        return {"revision": str(meta["rev"]), "basis": "table_revision"}
    finally:
        close_temporary(folder)


class SourceProbes:
    """只在后台需要检查时建连接；所有数仓检查复用一个连接、共用有限重试预算。"""

    def __init__(self, password, root, *, rpa_connect=None):
        self.password, self.root = password, Path(root)
        self.reader = None
        self.weather = {}
        self.rpa_connect = rpa_connect

    def close(self):
        if self.reader:
            self.reader.close()

    def check_upstream_day(self, code, day):
        """仅对已登记的日粒度数仓表核对昨日是否有记录，最多返回一行。"""
        source = BY_ID[code]
        if code not in RECONCILIATION_SOURCES or source.kind != "daily":
            return None
        window = approved_window(code)
        if not window.start <= day <= window.end:
            return None
        shops = SPECS[code].shop_ids if code in SPECS else tuple(SHOPS)
        table = source.locator.removeprefix("cubeappdata.")
        try:
            if self.reader is None:
                self.reader = ProductBoardReader(password=self.password())
            else:
                self.reader.connection.ping(reconnect=True)
            with self.reader.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT 1 FROM `{table}` WHERE time_granularity=%s "
                    f"AND shop_id IN ({','.join(['%s'] * len(shops))}) "
                    "AND dt>=%s AND dt<%s LIMIT 1",
                    ("day", *shops, day, day + timedelta(days=1)),
                )
                return {"date": day.isoformat(), "present": cursor.fetchone() is not None}
        except (MySQLError, OSError, ValueError):
            # 时效证据不可用时照常按元数据接收，不用查询失败证明上游缺数。
            return None

    def available_gap_days(self, code, gaps):
        """只读固定表和已批准的店铺日；同日其他店有数据不能证明该缺口已补。"""
        from app.modules.data_status.history_coverage import WINDOW_DAYS
        from app.modules.data_status.schedule_policy import DAILY_GAP_SOURCES

        if code not in DAILY_GAP_SOURCES:
            raise ValueError("INVALID_GAP_SOURCE")
        if not gaps:
            return ()
        shops = sorted({shop for values in gaps.values() for shop in values})
        allowed = SPECS[code].shop_ids if code in SPECS else SHOPS
        window = approved_window(code)
        first, last = min(gaps), max(gaps)
        if (
            not shops
            or not set(shops) <= set(allowed)
            or not window.start <= first <= last <= window.end
            or (last - first).days >= WINDOW_DAYS
        ):
            raise ValueError("GAP_WINDOW_NOT_APPROVED")
        table = BY_ID[code].locator.removeprefix("cubeappdata.")
        category_filter = (
            "AND REGEXP_REPLACE(COALESCE(product_category,''),'[[:space:]_－—-]','') "
            "NOT IN ('包材','儿童赠品','分类未设置') "
            if code == "product_regions"
            else ""
        )
        sql = (
            f"SELECT DATE(dt) AS d,shop_id FROM `{table}` WHERE time_granularity=%s "
            f"AND shop_id IN ({','.join(['%s'] * len(shops))}) "
            "AND dt>=%s AND dt<%s " + category_filter + "GROUP BY DATE(dt),shop_id"
        )
        if self.reader is None:
            self.reader = ProductBoardReader(password=self.password())
        else:
            self.reader.connection.ping(reconnect=True)
        with self.reader.connection.cursor() as cursor:
            execute_read(
                cursor,
                sql,
                ("day", *shops, first, last + timedelta(days=1)),
                self.reader.retry_budget,
                self.reader.heartbeat,
            )
            return tuple(
                sorted(
                    {
                        row["d"]
                        for row in cursor.fetchall()
                        if str(row["shop_id"]) in gaps.get(row["d"], ())
                    }
                )
            )

    def read(self, code):
        source = BY_ID[code]
        if source.system == "rpa":
            from app.connectors.aftersales_rpa_status import (
                configured_connection,
                read_update_status,
            )

            connect = self.rpa_connect or (lambda: configured_connection(self.root))
            return read_update_status(connect)
        if source.system == "feishu":
            return feishu_revision(source, self.root)
        if source.system == "nmc":
            if code not in self.weather:
                docs = {doc.url: doc for doc in receive_weather()}
                self.weather = {s.id: docs[s.locator] for s in SOURCES if s.system == "nmc"}
            doc = self.weather.pop(code)
            return {
                "date": doc.issue_time,
                "revision": hashlib.sha256(doc.text.encode()).hexdigest(),
                "basis": "publication_content",
            }
        if self.reader is None:
            self.reader = ProductBoardReader(password=self.password())
        else:
            self.reader.connection.ping(reconnect=True)
        end = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
        if code == "profile_records":
            records = self.reader.projection(code, end)
            return {
                "count": len(records),
                "revision": hashlib.sha256(
                    json.dumps(records, default=str, sort_keys=True).encode()
                ).hexdigest(),
                "basis": "profile_projection",
            }
        sql, params = warehouse_query(code, end)
        with self.reader.connection.cursor() as cursor:
            execute_read(cursor, sql, params, self.reader.retry_budget, self.reader.heartbeat)
            row = cursor.fetchone()
        result = {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in row.items()
        }
        result["basis"] = "snapshot_date" if code == "jst_master" else "recent_7_days_signal"
        if code in RECONCILIATION_SOURCES and source.kind == "daily":
            window = approved_window(code)
            if window.start <= end <= window.end:
                result["upstream_day"] = {
                    "date": end.isoformat(),
                    "present": str(row["date"])[:10] == end.isoformat(),
                }
        return result

    def functions(self):
        return {s.id: lambda code=s.id: self.read(code) for s in SOURCES}
