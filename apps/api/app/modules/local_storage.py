"""本项目本机空间策略；只扩大本地边界，不提供生产写入开关。"""

import json
import os
import stat
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[4]
POLICY_FILE = ROOT / "var/local-db/content-reuse.json"
LOCAL_TABLES = frozenset(
    {"product_daily_facts", "product_promotion_records", "product_region_records"}
)


def project_endpoint(engine, *, roles=("pdd_admin", "pdd_ingest")):
    """检查显式本机目标；不打开连接，也不使发布器产生隐式事务。"""
    u = engine.url
    return (
        u.drivername == "postgresql+psycopg"
        and u.host == "127.0.0.1"
        and u.port == 55432
        and u.database == "pdd_operations"
        and u.username in roles
    )


def require_local_maintenance(engine):
    """维护只允许本机项目管理员或既有随机测试管理员。"""
    if project_endpoint(engine, roles=("pdd_admin",)):
        return
    from app.modules.database_retention import require_local_test

    require_local_test(engine)


def verify_connection(c):
    """URL通过后还核对真实会话；不能通过改连接描述将其他库冒充本项目。"""
    database, session, current, server = c.execute(
        sa.text(
            "SELECT current_database(),session_user,current_user,"
            "current_setting('server_version_num')"
        )
    ).one()
    if (
        database != c.engine.url.database
        or session != c.engine.url.username
        or current != session
        or int(server) // 10000 != 17
    ):
        raise ValueError("LOCAL_STORAGE_IDENTITY")


def content_enabled(engine, table):
    """本机运行策略逐表启用；无配置沿原格式，售后永不随本机策略切换。"""
    if not project_endpoint(engine):
        return False
    if not POLICY_FILE.exists() and not POLICY_FILE.is_symlink():
        return False
    from app.modules.archive_store import _safe

    try:
        _safe(POLICY_FILE)
        descriptor = os.open(POLICY_FILE, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid != os.getuid()
                or info.st_size > 4096
            ):
                raise ValueError("LOCAL_STORAGE_POLICY")
            value = json.load(stream)
        tables = value.get("tables")
        if (
            set(value) != {"revision", "tables"}
            or type(value["revision"]) is not int
            or value["revision"] != 1
            or not isinstance(tables, list)
            or any(not isinstance(t, str) for t in tables)
            or len(tables) != len(set(tables))
            or not set(tables) <= LOCAL_TABLES
        ):
            raise ValueError("LOCAL_STORAGE_POLICY")
        return table in tables
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError("LOCAL_STORAGE_POLICY") from exc


def require_content_write(c, table):
    """随机测试沿原五表；本地业务写入必须显式授权非售后三表并核对head。"""
    if os.environ.get("APP_ENV") == "production":
        from app.modules.deployment_storage import require_content_write as production_write

        production_write(c, table)
        return
    if project_endpoint(c.engine):
        if not content_enabled(c.engine, table):
            raise ValueError("CONTENT_LOCAL_TABLE_DISABLED")
        verify_connection(c)
        from app.modules.database_retention import HEAD

        if c.scalar(sa.text("SELECT version_num FROM public.alembic_version_v2")) != HEAD:
            raise ValueError("CONTENT_SCHEMA_HEAD")
    else:
        from app.modules.database_retention import require_local_test

        require_local_test(c.engine)
