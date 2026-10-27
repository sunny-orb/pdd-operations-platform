"""从环境变量取得数据库连接，不把凭据写进迁移配置。"""

from alembic import context

from app.core.config import Settings
from app.db import tables  # noqa: F401  # 注册M1元数据，不执行DDL
from app.db.base import Base
from app.db.connection import build_engine

settings = Settings()
target_metadata = Base.metadata


def run_migrations() -> None:
    """运行离线 SQL 或在线迁移；无参数，未配置 PostgreSQL 时明确失败。"""
    url = settings.database_url.get_secret_value()
    if not url:
        raise RuntimeError("执行迁移前必须配置 DATABASE_URL")
    if context.is_offline_mode():
        context.configure(
            url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = build_engine(settings)
    assert engine is not None
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
