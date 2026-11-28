"""真实PG隔离fixture；随机新建/清理本轮测试库，拒绝项目库。"""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from migrations_v2.local import ROOT, local_url, migrate


def downgrade_to(connection, revision):
    """在本机随机库按真实起始 revision 回退，建立指定历史目录的兼容测试。"""
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    config.attributes.update(
        connection=connection, v2_expected_database=connection.engine.url.database,
    )
    command.downgrade(config, revision)


@pytest.fixture
def sales_pg():
    name = "pdd_operations_v2_test_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(
        local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    engine = None
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
    try:
        engine = sa.create_engine(local_url(name), hide_parameters=True)
        with engine.begin() as c:
            migrate(c)
        yield engine
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()
