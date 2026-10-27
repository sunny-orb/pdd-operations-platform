"""本轮确认的v2本地执行边界；不导入应用配置，不初始化/改变凭据。"""

import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, Connection

ROOT = Path(__file__).resolve().parents[3]


def local_url(database: str) -> URL:
    """仅生成授权库的loopback连接；错误不回显配置、密码或完整URL。"""
    if database not in ("postgres", "pdd_operations") and not re.fullmatch(
        r"pdd_operations_v2_test_[0-9a-f]{12}", database
    ):
        raise ValueError("数据库不在本轮授权范围")
    config = dict(
        line.split("=", 1)
        for line in (ROOT / "var/local-db/docker.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    if config["POSTGRES_PORT"] != "55432":
        raise RuntimeError("本次授权仅限本机55432")
    return URL.create(
        "postgresql+psycopg",
        username="pdd_admin",
        password=config["POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=55432,
        database=database,
    )


def migrate(connection: Connection, target: str = "head") -> None:
    """只允许独立基线的升级/空库回退；外部事务控制提交和失败回滚。"""
    if target not in ("head", "base"):
        raise ValueError("未批准的迁移目标")
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    config.attributes["connection"] = connection
    config.attributes["v2_expected_database"] = connection.engine.url.database
    (command.downgrade if target == "base" else command.upgrade)(config, target)
