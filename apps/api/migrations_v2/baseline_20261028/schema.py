"""初始迁移的冻结元数据；不依赖应用模型、设置、Markdown或数据库连接。"""

import sqlalchemy as sa

from . import core_constraints, facts_constraints, query_indexes, rules_constraints
from .tables import add_tables


def build_metadata() -> sa.MetaData:
    """生成新的30表元数据，调用者只编译DDL；不创建engine或执行SQL。"""
    metadata = sa.MetaData()
    add_tables(metadata)
    core_constraints.apply(metadata)
    facts_constraints.apply(metadata)
    rules_constraints.apply(metadata)
    query_indexes.apply(metadata)
    return metadata
