"""完整版本引用不可变事实块；不改变来源接收、版本身份或指标规则。

写入口只接受随机本机测试库。调用者持有业务锁并在同一事务中完成验证与发布；
既有物理事实保留，通过 read_* 视图同时读取两种表示。
"""

import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert

from app.core.config import Settings
from migrations_v2.baseline_20261028.schema import build_metadata

FACTS = {
    "product_daily_facts": ("product_daily",),
    "product_promotion_records": ("product_promotion",),
    "product_region_records": ("product_regions",),
    "product_sales_records": ("jst_shipped_sales", "jst_cancelled_sales"),
    "return_records": ("jst_returns",),
}
_BASE = build_metadata()


def enabled(c):
    """默认关闭；显式开启也不能对生产或普通本地项目库启用新写入。"""
    if not Settings(_env_file=None).content_reuse_enabled:
        return False
    from app.modules.database_retention import require_local_test

    require_local_test(c.engine)
    return True


def read_name(name):
    return "read_" + name if name in FACTS else name


def read_table(c, name):
    """固定已知列的查询表，不通过用户输入构造标识符。"""
    original = _BASE.tables["public." + name]
    return sa.table(
        read_name(name), *[sa.column(x.name, x.type) for x in original.c], schema="public"
    )


def _value(value, kind):
    if value is None:
        return None, None
    if isinstance(kind, sa.Numeric):
        if isinstance(value, (float, bool)):
            raise ValueError("CONTENT_NUMERIC_TYPE")
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("CONTENT_NONFINITE")
        # 保留PG NUMERIC的小数位表示：既有发布器逐字段序列化核对也区分100.00与100。
        # 不用normalize()或当前context运算，避免超长NUMERIC精度损失。
        token = format(number.copy_abs() if number == 0 else number, "f")
        return number, token
    if isinstance(kind, sa.DateTime):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("CONTENT_TIMEZONE")
        value = value.astimezone(UTC)
        return value, value.isoformat(timespec="microseconds")
    if isinstance(kind, sa.Date):
        if type(value) is not date:
            raise ValueError("CONTENT_DATE_TYPE")
        return value, value.isoformat()
    if isinstance(kind, JSONB):
        # JSON 的数字/字符串/null 保持不同；键顺序不影响内容身份。
        encoded = json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False)
        decoded = json.loads(encoded)
        if decoded != value:
            raise ValueError("CONTENT_JSON_TYPE")
        return decoded, decoded
    if isinstance(kind, sa.Integer):
        if type(value) is not int:
            raise ValueError("CONTENT_INTEGER_TYPE")
    elif not isinstance(value, str):
        raise ValueError("CONTENT_TEXT_TYPE")
    return value, value


def _blocks(table, version, rows):
    columns = [
        x for x in _BASE.tables["public." + table].c if x.name not in ("id", "dataset_version_id")
    ]
    names = {x.name for x in columns}
    grouped = defaultdict(list)
    keys = set()
    for raw in rows:
        if raw.keys() - names:
            raise ValueError("CONTENT_UNKNOWN_COLUMN")
        values, tokens = {}, {}
        for col in columns:
            values[col.name], tokens[col.name] = _value(raw.get(col.name), col.type)
        key = values["source_row_key"]
        if not isinstance(key, str) or not key.strip():
            raise ValueError("CONTENT_ROW_KEY")
        if key in keys:
            raise ValueError("CONTENT_DUPLICATE_KEY")
        keys.add(key)
        suffix = int(hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:8], 16) % 64
        period = (
            str(values.get("business_date") or "?")
            if table == "product_daily_facts"
            else "/".join(str(values.get(k) or "?") for k in ("period_start", "period_end"))
        )
        bucket = f"{period}:{suffix}"
        grouped[bucket].append((values, tokens))
    result = []
    for bucket, items in grouped.items():
        items.sort(key=lambda x: x[0]["source_row_key"])
        identity = [
            1,
            table,
            version["dataset_code"],
            version["row_schema_revision"],
            bucket,
            [x[1] for x in items],
        ]
        key = hashlib.sha256(
            json.dumps(
                identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        result.append((key, bucket, [x[0] for x in items]))
    return sorted(result)


def write_version(c, table_name, version_id, rows):
    """写完整候选；同内容块共享，删除通过新成员集合表达，失败由外层事务回滚。

    返回物理新增/复用块数及逻辑行数。所有块按同一顺序争用唯一键，避免并发
    相互等待；已提交块必须 sealed，不能先发布半成品再补写。
    """
    from app.modules.database_retention import require_local_test

    require_local_test(c.engine)
    if table_name not in FACTS:
        raise ValueError("CONTENT_TABLE")
    version = (
        c.execute(
            sa.text("SELECT * FROM public.dataset_versions WHERE id=:v FOR UPDATE"),
            {"v": version_id},
        )
        .mappings()
        .one()
    )
    if version["state"] != "draft":
        raise ValueError("CONTENT_VERSION_SEALED")
    if version["dataset_code"] not in FACTS[table_name]:
        raise ValueError("CONTENT_NAMESPACE")
    blocks = _blocks(table_name, version, rows)
    count = sum(len(b[2]) for b in blocks)
    previous = (
        c.execute(
            sa.text("SELECT * FROM public.fact_content_manifests WHERE version_id=:v"),
            {"v": version_id},
        )
        .mappings()
        .one_or_none()
    )
    if previous:
        members = set(
            c.execute(
                sa.text(
                    "SELECT block_id,bucket FROM public.fact_content_members WHERE version_id=:v"
                ),
                {"v": version_id},
            ).all()
        )
        if (
            previous["table_name"] != table_name
            or previous["row_count"] != count
            or members != {(b[0], b[1]) for b in blocks}
        ):
            raise ValueError("CONTENT_VERSION_CONFLICT")
        return {"new_blocks": 0, "reused_blocks": len(blocks), "rows": count}
    c.execute(
        sa.text(
            "INSERT INTO public.fact_content_manifests(version_id,table_name,row_count) "
            "VALUES (:v,:t,:n)"
        ),
        {"v": version_id, "t": table_name, "n": count},
    )
    original = _BASE.tables["public." + table_name]
    shared = sa.table(
        "shared_" + table_name,
        sa.column("block_id", sa.Text()),
        *[
            sa.column(x.name, JSONB(none_as_null=True) if isinstance(x.type, JSONB) else x.type)
            for x in original.c
            if x.name not in ("id", "dataset_version_id")
        ],
        schema="public",
    )
    existing = (
        set(
            c.execute(
                sa.text(
                    "SELECT id FROM public.fact_content_blocks "
                    "WHERE id=ANY(:ids) AND state='sealed'"
                ),
                {"ids": [b[0] for b in blocks]},
            ).scalars()
        )
        if blocks
        else set()
    )
    new = 0
    for key, bucket, values in blocks:
        if key in existing:
            continue
        created = c.scalar(
            sa.text("""INSERT INTO public.fact_content_blocks
          (id,table_name,dataset_code,row_schema_revision,bucket,row_count)
          VALUES (:id,:t,:d,:r,:b,:n) ON CONFLICT(id) DO NOTHING RETURNING id"""),
            dict(
                id=key,
                t=table_name,
                d=version["dataset_code"],
                r=version["row_schema_revision"],
                b=bucket,
                n=len(values),
            ),
        )
        if created:
            for offset in range(0, len(values), 1000):
                c.execute(
                    insert(shared),
                    [dict(row, block_id=key) for row in values[offset : offset + 1000]],
                )
            c.execute(
                sa.text("UPDATE public.fact_content_blocks SET state='sealed' WHERE id=:k"),
                {"k": key},
            )
            new += 1
    members = sa.table(
        "fact_content_members",
        sa.column("version_id", sa.Text()),
        sa.column("bucket", sa.Text()),
        sa.column("block_id", sa.Text()),
        schema="public",
    )
    for offset in range(0, len(blocks), 1000):
        c.execute(
            insert(members),
            [
                dict(version_id=version_id, bucket=b, block_id=k)
                for k, b, _ in blocks[offset : offset + 1000]
            ],
        )
    return {"new_blocks": new, "reused_blocks": len(blocks) - new, "rows": count}
