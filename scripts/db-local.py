"""管理本项目Docker PostgreSQL；不访问数仓，不自动删除库或持久卷。"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path("var/local-db/docker.env")


def private_write(path: Path, content: str) -> None:
    """独占创建权限600的配置；已存在的文件由调用方保留，不打印内容。"""
    with open(
        path, "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)
    ) as stream:
        stream.write(content)


def read_config(root: Path) -> dict[str, str]:
    """仅读取本入口管理的本地配置，返回Docker所需键值，不输出凭据。"""
    return dict(
        line.split("=", 1)
        for line in (root / PRIVATE).read_text().splitlines()
        if line and not line.startswith("#")
    )


def initialize(root: Path) -> None:
    """生成本地随机凭据和应用只读连接；重复执行不替换既有配置。"""
    private = root / PRIVATE
    private.parent.mkdir(parents=True, exist_ok=True)
    if not private.exists():
        private_write(
            private,
            f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
            f"APP_PASSWORD={secrets.token_hex(32)}\nPOSTGRES_PORT=55432\n",
        )
    sync_private = root / "var/local-db/sync.env"
    if not sync_private.exists():
        private_write(sync_private, f"SYNC_PASSWORD={secrets.token_hex(32)}\n")
    config = read_config(root)
    api_env = root / "apps/api/.env"
    if not api_env.exists():
        private_write(
            api_env,
            "# 本地Docker应用只读账号；由db-local.py生成，不提交。\n"
            f"DATABASE_URL=postgresql+psycopg://pdd_app:{config['APP_PASSWORD']}"
            f"@127.0.0.1:{config['POSTGRES_PORT']}/pdd_operations\n"
            "APP_ENV=development\nDB_POOL_SIZE=4\nDB_MAX_OVERFLOW=0\n",
        )


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    """执行固定项目的Compose命令；配置只经文件传入，命令行不含密码。"""
    return subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "pdd-operations-platform",
            "--env-file",
            str(ROOT / PRIVATE),
            "-f",
            str(ROOT / "compose.yaml"),
            *args,
        ],
        cwd=ROOT,
        env={**os.environ, **read_config(ROOT)},
        check=True,
        text=True,
        capture_output=capture,
    )


def sql(statement: str, database: str = "postgres") -> str:
    """通过容器stdin执行本地初始化SQL；错误只报告阶段，不回显含密码语句。"""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "pdd-operations-platform",
            "--env-file",
            str(ROOT / PRIVATE),
            "-f",
            str(ROOT / "compose.yaml"),
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-U",
            "pdd_admin",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
        ],
        input=statement,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env={**os.environ, **read_config(ROOT)},
        check=False,
    )
    if result.returncode:
        raise RuntimeError("本地数据库初始化SQL失败；未输出SQL及私密参数")
    return result.stdout.strip()


def provision() -> None:
    """仅创建项目库/独立测试库及受限应用角色；既有库和卷不删除。"""
    config = read_config(ROOT)
    # 随机值为十六进制；仍按SQL字符串规则转义，避免配置编辑造成注入。
    password = config["APP_PASSWORD"].replace("'", "''")
    sql(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='pdd_app') "
        "THEN CREATE ROLE pdd_app LOGIN; END IF; END $$;\n"
        f"ALTER ROLE pdd_app WITH PASSWORD '{password}' "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;"
    )
    for database in ("pdd_operations", "pdd_operations_test"):
        exists = sql(
            f"SELECT EXISTS(SELECT FROM pg_database WHERE datname='{database}');"
        )
        if exists != "t":
            sql(f"CREATE DATABASE {database} OWNER pdd_admin;")
        sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC;", database)
    sql("GRANT CONNECT ON DATABASE pdd_operations TO pdd_app;", "pdd_operations")
    sync_password = (
        (ROOT / "var/local-db/sync.env").read_text().strip().split("=", 1)[1]
    )
    sync_password = sync_password.replace("'", "''")
    sql(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='pdd_ingest') "
        "THEN CREATE ROLE pdd_ingest LOGIN; END IF; END $$; "
        f"ALTER ROLE pdd_ingest WITH PASSWORD '{sync_password}' "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;"
    )
    sql(
        "GRANT CONNECT ON DATABASE pdd_operations TO pdd_ingest; "
        "GRANT USAGE ON SCHEMA public TO pdd_ingest;",
        "pdd_operations",
    )


def main() -> None:
    """提供本地容器和测试入口；不提供隐式down -v或删除数据操作。"""
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in {"up", "test", "status"}:
        raise SystemExit("用法：db-local.py up|test|status；迁移请使用v2受控入口")
    initialize(ROOT)
    if action == "up":
        compose("up", "-d", "--wait", "--wait-timeout", "90")
        provision()
        print(
            "项目库pdd_operations和独立测试库已就绪；本机端口55432，未执行迁移/同步。"
        )
    elif action == "test":
        # 现行PG fixture自行建立一次性v2测试库，不向业务库或旧测试库注入连接。
        subprocess.run(["npm", "test"], cwd=ROOT, check=True)
    else:
        compose("ps")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(
            f"本地数据库操作失败：{type(exc).__name__}；凭据未输出。", file=sys.stderr
        )
        raise SystemExit(1) from None
