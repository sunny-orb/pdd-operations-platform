"""RPA同事维护的已交付表：只读变化监测，不启动采集或接收业务数据。"""

import json
import os
from pathlib import Path

from psycopg.rows import dict_row

from app.connectors.aftersales_rpa import connect_readonly


def configured_connection(root):
    """只在后台检查时加载私有连接配置；不向API、日志或页面返回凭据。"""
    if url := os.environ.get("RPA_DATABASE_URL"):
        return connect_readonly(url)
    path = Path(root) / "var/local-db/aftersales-rpa.json"
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("RPA_CREDENTIALS_REQUIRED")
    values = json.loads(path.read_text())
    if (
        not isinstance(values, dict)
        or not values
        or not all(isinstance(v, (str, int)) for v in values.values())
    ):
        raise ValueError("RPA_CREDENTIALS_INVALID")
    return connect_readonly(**values)


def production_connection(engine):
    """从当前任务连接得到同库只读连接，沿用线上TLS配置。"""
    url = engine.url
    if (url.host, url.port or 5432, url.database, url.username) != (
        "pg.kktree.cn",
        5432,
        "pdd_dashboard",
        "songbing",
    ):
        raise ValueError("RPA_SOURCE_DATABASE")
    args, kwargs = engine.dialect.create_connect_args(url)
    return connect_readonly(*args, **kwargs)


def read_update_status(connect):
    """单个只读快照聚合必要元信息；指纹覆盖历史修订/删除，不将明细传到状态表。"""
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        c.execute("SET LOCAL statement_timeout='30s'")
        c.execute("SET LOCAL TIME ZONE 'UTC'")
        with c.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT count(*) AS count, max(updated_at) AS source_updated,
                min(start_date) AS date_from, max(end_date) AS date,
                count(DISTINCT (time_granularity,start_date,end_date)) AS period_count,
                md5(coalesce(string_agg(md5(jsonb_build_array(
                    id,start_date,end_date,time_granularity,class,json_data,updated_at
                )::text),'' ORDER BY id),'')) AS revision
                FROM public.aftersales_reports_rpa""")
            row = cursor.fetchone()
    return {
        **{k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()},
        "basis": "rpa_table",
    }
