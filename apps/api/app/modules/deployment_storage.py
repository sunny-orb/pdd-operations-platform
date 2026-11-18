"""固定公司项目的三表复用策略；显式启停，只读检查，不提供生产DELETE入口。"""

import json
import os
import stat
import tempfile
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_store import _safe

ROOT = Path(__file__).resolve().parents[4]
EXPECTED_ROOT = Path("/app")
UID = 10001
PROJECT = "/home/songbing/pdd-operations-platform"
HOST, PORT, DATABASE, ACCOUNT = "pg.kktree.cn", 5432, "pdd_dashboard", "songbing"
TABLES = frozenset({"product_daily_facts", "product_promotion_records", "product_region_records"})


def require_endpoint(engine):
    """URL不得增加hostaddr/options等连接覆盖，不在此创建连接或回显口令。"""
    u = engine.url
    if (
        u.drivername != "postgresql+psycopg"
        or (u.host, u.port or 5432, u.database, u.username) != (HOST, PORT, DATABASE, ACCOUNT)
        or dict(u.query) != {"sslmode": "require"}
    ):
        raise ValueError("STORAGE_ENDPOINT")


def require_context():
    """只允许固定镜像工作目录与worker身份；宿主绑定由deploy/storage.py核验。"""
    if (
        os.environ.get("APP_ENV") != "production"
        or ROOT != EXPECTED_ROOT
        or os.getuid() != UID
        or os.geteuid() != UID
    ):
        raise ValueError("STORAGE_CONTEXT")
    from app.core.config import Settings

    if Settings(_env_file=None).content_reuse_enabled:
        raise ValueError("STORAGE_TEST_SWITCH")
    for path in (ROOT, ROOT / "var"):
        info = _safe(path).stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("STORAGE_DIRECTORY")
    if (ROOT / "var").stat().st_uid != UID:
        raise ValueError("STORAGE_DIRECTORY")


def policy_path():
    return ROOT / "var/storage-policy.json"


def policy_value():
    return dict(
        revision=1, project=PROJECT, account=ACCOUNT, database=DATABASE, tables=sorted(TABLES)
    )


def read_policy():
    require_context()
    path = policy_path()
    if not path.exists() and not path.is_symlink():
        return None
    try:
        _safe(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as f:
            info = os.fstat(f.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or info.st_uid != UID
                or info.st_size > 4096
            ):
                raise ValueError("STORAGE_POLICY")
            value = json.load(f)
        if (
            not isinstance(value, dict)
            or set(value) != set(policy_value())
            or type(value.get("revision")) is not int
            or value.get("revision") != 1
            or any(value.get(k) != v for k, v in policy_value().items() if k != "tables")
            or not isinstance(value.get("tables"), list)
            or any(not isinstance(t, str) for t in value["tables"])
            or len(value["tables"]) != len(set(value["tables"]))
            or not set(value["tables"]) <= TABLES
        ):
            raise ValueError("STORAGE_POLICY")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("STORAGE_POLICY") from exc


def content_enabled(engine, table):
    """售后不读此策略；无生产策略时保持原表示，有效配置才切换指定三表。"""
    if table not in TABLES or os.environ.get("APP_ENV") != "production":
        return False
    require_endpoint(engine)
    value = read_policy()
    return value is not None and table in value["tables"]


def require_session(c):
    """在真实连接核对库/会话/执行身份、TLS、端点与当前迁移，避免伪造URL。"""
    from migrations_v2.company_db_objects import COMPATIBLE_RUNTIME_REVISIONS

    require_endpoint(c.engine)
    require_context()
    actual = c.execute(
        sa.text(
            "SELECT current_database(),session_user,current_user,"
            "current_setting('server_version_num')"
        )
    ).one()
    raw = c.connection.driver_connection
    if (
        tuple(actual[:3]) != (DATABASE, ACCOUNT, ACCOUNT)
        or int(actual[3]) // 10000 != 17
        or (raw.info.host, raw.info.port) != (HOST, PORT)
        or not raw.pgconn.ssl_in_use
    ):
        raise ValueError("STORAGE_SESSION")
    head = c.scalar(sa.text("SELECT version_num FROM public.alembic_version_v2"))
    if head not in COMPATIBLE_RUNTIME_REVISIONS:
        raise ValueError("STORAGE_HEAD")
    return head


def require_content_write(c, table):
    if not content_enabled(c.engine, table):
        raise ValueError("STORAGE_TABLE_DISABLED")
    require_session(c)


def verify_catalog(c):
    """公司对象只读检查复用既有部署校验；外部RPA和monitor不参与维护候选。"""
    from app.modules.database_retention import digest
    from migrations_v2.company_db_objects import (
        application_signature,
        rpa_manifest,
        verify_application,
        verify_company_protections,
    )

    head = require_session(c)
    raw = c.connection.driver_connection
    if rpa_manifest(raw) is None:
        raise ValueError("STORAGE_RPA_REQUIRED")
    verify_company_protections(raw)
    verify_application(raw, head, has_rpa=True)
    return digest(application_signature(raw, has_rpa=True))


def inspect(c):
    """只读元数据，不COPY事实或创建备份；未知退出时间仍保留。"""
    from app.modules.database_retention import snapshot

    head = require_session(c)
    plan = snapshot(c, with_content=False)
    value = read_policy()
    return {
        "schema_head": head,
        "enabled_tables": value["tables"] if value else [],
        "current_datasets": len(plan["heads"]),
        "protected_versions": len(plan["protected"]),
        "candidate_versions": len(plan["candidates"]),
        "production_delete_enabled": False,
    }


def _write_policy(value):
    """仅本项目固定文件；写失败保留旧策略，成功以同目录rename原子切换。"""
    read_policy()
    destination = _safe(policy_path())
    if value is None:
        destination.unlink(missing_ok=True)
        return
    fd, temporary = tempfile.mkstemp(prefix=".storage-policy-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(destination)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(engine, action="preflight"):
    """默认只读。启停只写固定策略文件，持所有发布锁且拒绝正在运行的任务。"""
    from app.modules.sales.datasets import SPECS

    if action not in {"preflight", "enable", "disable"}:
        raise ValueError("STORAGE_ACTION")
    require_endpoint(engine)
    require_context()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        if action == "preflight":
            c.exec_driver_sql("SET TRANSACTION READ ONLY")
        c.exec_driver_sql("SET LOCAL statement_timeout='30s'")
        c.exec_driver_sql("SET LOCAL transaction_timeout='30s'")
        if action != "preflight":
            for key in (
                "project-source-sync",
                "m1-product-board",
                "m5-source-report",
                "nmc-weather-publication",
                *sorted({"m3-" + code for code in ("shop_daily", "sales_targets", *SPECS)}),
                "pdd-database-maintenance",
            ):
                if not c.scalar(
                    sa.text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": key}
                ):
                    raise ValueError("STORAGE_BUSY")
            c.exec_driver_sql("LOCK TABLE public.task_runs IN SHARE MODE NOWAIT")
        result = inspect(c)
        if action != "preflight":
            _write_policy(policy_value() if action == "enable" else None)
            result["enabled_tables"] = sorted(TABLES) if action == "enable" else []
        return result
