"""该revision专用约束构造器；输入为冻结常量，输出为SQLAlchemy结构，无数据库连接。"""

import hashlib

import sqlalchemy as sa


def name(prefix: str, table_name: str, suffix: str) -> str:
    """生成不超过PG标识长度的稳定ASCII约束名。"""
    value = f"{prefix}_{table_name}_{suffix}"
    if len(value) <= 63:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:10]
    return f"{value[:52]}_{digest}"


def table(metadata: sa.MetaData, table_name: str) -> sa.Table:
    return metadata.tables[f"public.{table_name}"]


def check(metadata: sa.MetaData, table_name: str, suffix: str, expression: str) -> None:
    # 这些表达式全部来自冻结代码常量；不能转为text()，JSON的:7/:true会被
    # 当成绑定参数并在DDL字面量编译中变成NULL。literal_column原样保留SQL。
    table(metadata, table_name).append_constraint(
        sa.CheckConstraint(sa.literal_column(expression), name=name("ck", table_name, suffix))
    )


def enum(metadata: sa.MetaData, table_name: str, column: str, values: tuple[str, ...]) -> None:
    """可空列由列NULL规则决定；非NULL值必须在固定有限集合内。"""
    literals = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    check(metadata, table_name, f"{column}_values", f"{column} IN ({literals})")


def unique(
    metadata: sa.MetaData,
    table_name: str,
    suffix: str,
    *columns: str,
    nulls_not_distinct: bool = False,
) -> None:
    options = {"postgresql_nulls_not_distinct": True} if nulls_not_distinct else {}
    table(metadata, table_name).append_constraint(
        sa.UniqueConstraint(*columns, name=name("uq", table_name, suffix), **options)
    )


def index(
    metadata: sa.MetaData,
    table_name: str,
    suffix: str,
    *columns: str,
    where: str | None = None,
    is_unique: bool = False,
) -> None:
    target = table(metadata, table_name)
    expressions = []
    for column in columns:
        parts = column.split()
        value = target.c[parts[0]]
        expressions.append(value.desc() if len(parts) == 2 and parts[1] == "DESC" else value)
    options = {"postgresql_where": sa.text(where)} if where else {}
    sa.Index(
        name("ux" if is_unique else "ix", table_name, suffix),
        *expressions,
        unique=is_unique,
        **options,
    )
