"""惰性建立 PostgreSQL 连接，不在应用启动时创建业务表。"""

from sqlalchemy import Engine, create_engine, text

from app.core.config import Settings


def build_engine(settings: Settings) -> Engine | None:
    """按配置建立连接池；输入设置，返回引擎，未配置时返回 None。"""
    url = settings.database_url.get_secret_value()
    if not url:
        return None
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_timeout=settings.db_connect_timeout_seconds,
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": "-c statement_timeout=5000",
        },
    )


def database_available(engine: Engine | None) -> bool:
    """检查连接可用性；输入引擎，返回布尔值，业务迁移和数据质量另行检查。"""
    if engine is None:
        return False
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
