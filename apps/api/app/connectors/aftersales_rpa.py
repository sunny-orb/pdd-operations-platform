"""公司RPA源只读分页双读；每次独立快照，不能在同一RR事务内假证稳定。"""

from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

from app.modules.aftersales import rpa_ingestion
from app.modules.streaming_json import fingerprint_rows

READ_TIMEOUT_SECONDS = 45


class RpaConnection(psycopg.Connection):
    """服务器statement_timeout不能终止断网后的客户端等待，额外约束每次读等待。"""

    def wait(self, gen, interval=0.1, timeout=None):
        try:
            return super().wait(
                gen,
                interval=interval,
                timeout=min(timeout or READ_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except psycopg.OperationalError:
            self.close()
            raise


def connect_readonly(conninfo="", **parameters):
    """复用私有加载器已有参数，强制只读及有限连接等待，不重复传同名参数。"""
    parameters.update(options="-c default_transaction_read_only=on", connect_timeout=15)
    return RpaConnection.connect(conninfo, **parameters)


def read_snapshot(connect, heartbeat=lambda: None):
    """按原ID顺序读完整快照；页大小限传输，期间/类别行限约束单份交付。"""
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        c.execute("SET LOCAL statement_timeout='45s'")
        rows = []
        counts = defaultdict(int)
        with c.cursor(name="aftersales_rpa", row_factory=dict_row) as cursor:
            cursor.execute("""SELECT id,start_date,end_date,time_granularity,
                class,json_data,updated_at
                FROM public.aftersales_reports_rpa ORDER BY id""")
            while batch := cursor.fetchmany(1000):
                for item in batch:
                    group = tuple(
                        item[k] for k in ("time_granularity", "start_date", "end_date", "class")
                    )
                    counts[group] += 1
                    if counts[group] > rpa_ingestion.MAX_GROUP_ROWS:
                        raise ValueError("RPA_GROUP_ROW_LIMIT")
                rows.extend(batch)
                heartbeat()
        if not rows:
            raise ValueError("RPA_EMPTY_OR_LIMIT")
        return rows


def receive_stable(connect, heartbeat=lambda: None):
    first = read_snapshot(connect, heartbeat)
    digest = fingerprint_rows(first)
    del first
    second = read_snapshot(connect, heartbeat)
    if fingerprint_rows(second) != digest:
        raise ValueError("RPA_SOURCE_CHANGED")
    return second
