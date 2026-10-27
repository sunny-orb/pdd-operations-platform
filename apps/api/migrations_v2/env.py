"""独立v2迁移：离线不接库；在线仅接受已确认范围的显式本地连接。

2026-10-28用户确认隔离真实PG验证后创建本地空表，故开放受控在线路径。
不读取.env/DATABASE_URL，不构造engine，不导入旧应用设置或模型。
"""

import re

import sqlalchemy as sa
from alembic import context
from sqlalchemy.engine import Connection

options = {
    "transactional_ddl": True,
    "version_table": "alembic_version_v2",
    "version_table_schema": "public",
}
if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        dialect_opts={"paramstyle": "named"},
        literal_binds=True,
        **options,
    )
else:
    connection = context.config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("v2在线迁移须显式传入受控连接；不读取环境连接配置")
    expected = context.config.attributes.get("v2_expected_database", "")
    url = connection.engine.url
    allowed = expected == "pdd_operations" or re.fullmatch(
        r"pdd_operations_v2_test_[0-9a-f]{12}", expected
    )
    if (
        not allowed
        or url.database != expected
        or url.host != "127.0.0.1"
        or url.port != 55432
        or url.username != "pdd_admin"
        or connection.dialect.name != "postgresql"
    ):
        raise RuntimeError("v2目标不在已确认的本地数据库范围内")
    database, role, version = connection.execute(
        sa.text(
            "SELECT current_database(), current_user, "
            "current_setting('server_version_num')::integer"
        )
    ).one()
    if database != expected or role != "pdd_admin" or version // 10000 != 17:
        raise RuntimeError("v2实际服务器身份或PG版本不符")
    context.configure(connection=connection, **options)
with context.begin_transaction():
    context.run_migrations()
