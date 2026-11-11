"""以主键分批计算备份内容摘要；调用方先校验库和表范围，全程只读。"""

import hashlib

from psycopg import sql

ALGORITHM = "sha256-pk-ordered-row-sha256-v1"


def table_digest(connection, table: str, *, batch_size: int = 10000) -> dict:
    """返回行数/内容摘要；以PK稳定排序，查询及内存均按批次有界，不汇总全表字符串。"""
    if not 1 <= batch_size <= 50000:
        raise ValueError("BACKUP_BATCH_SIZE_INVALID")
    keys = [
        row[0]
        for row in connection.execute(
            "SELECT a.attname FROM pg_constraint k "
            "JOIN pg_class t ON t.oid=k.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN LATERAL unnest(k.conkey) WITH ORDINALITY key(attnum,position) ON true "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=key.attnum "
            "WHERE n.nspname='public' AND t.relname=%s AND k.contype='p' "
            "ORDER BY key.position",
            (table,),
        )
    ]
    if not keys:
        raise ValueError("BACKUP_PRIMARY_KEY_REQUIRED")
    columns = sql.SQL(",").join(sql.Identifier("t", key) for key in keys)
    digest = hashlib.sha256()
    count = 0
    last = None
    while True:
        where = (
            sql.SQL("")
            if last is None
            else sql.SQL(" WHERE ROW({}) > ROW({})").format(
                columns, sql.SQL(",").join(sql.Placeholder() for _ in keys)
            )
        )
        query = sql.SQL(
            "SELECT {},sha256(convert_to(to_jsonb(t)::text,'UTF8')) "
            "FROM public.{} t{} ORDER BY {} LIMIT %s"
        ).format(columns, sql.Identifier(table), where, columns)
        batch = connection.execute(query, (*(last or ()), batch_size)).fetchall()
        if not batch:
            break
        for row in batch:
            digest.update(bytes(row[-1]))
        count += len(batch)
        last = batch[-1][:-1]
    return {"rows": count, "sha256": digest.hexdigest()}
