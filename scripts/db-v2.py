"""v2受控本地入口：verify隔离验证；create通过后创建本地空结构。

2026-10-28用户明确授权；不导入业务数据、不启动同步、不运行旧M1迁移。
只读取现有本地受管配置；私密备份/测试证据在Git忽略的var内，权限600。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.baseline_20261028.schema import build_metadata
from migrations_v2.local import local_url, migrate

PRIVATE = ROOT / "var/database-v2"
EVIDENCE = PRIVATE / "verified.json"


def fingerprint() -> str:
    """绑定结构、运行入口和测试内容，任一变化都须重跑隔离验证。"""
    files = [
        *sorted((ROOT / "apps/api/migrations_v2").rglob("*.py")),
        ROOT / "apps/api/alembic-v2.ini",
        ROOT / "docs/database-schema/schema.sql",
        ROOT / "scripts/render-db-schema.py",
        ROOT / "scripts/upgrade-targets-local.py",
        ROOT / "docs/database-schema/target-ranges.sql",
        Path(__file__).resolve(),
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def private_write(path: Path, data: str | bytes) -> None:
    """独占创建证据或备份，保护权限；不覆盖既有备份。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        stream.write(data.encode() if isinstance(data, str) else data)


def snapshot(connection) -> dict:
    """只记录本地结构与行数，不读取业务行/私密配置。"""
    database, role, version, cluster = connection.execute(
        sa.text(
            "SELECT current_database(), current_user, current_setting('server_version_num')::integer, "
            "(SELECT system_identifier::text FROM pg_control_system())"
        )
    ).one()
    if database != "pdd_operations" or role != "pdd_admin" or version // 10000 != 17:
        raise RuntimeError("实际项目库身份/PG版本不符")
    objects = connection.execute(
        sa.text(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f') ORDER BY c.relname"
        )
    ).all()
    functions = connection.execute(
        sa.text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public'"
        )
    ).scalar_one()
    return {
        "database": database,
        "role": role,
        "server_version_num": version,
        "cluster": cluster,
        "objects": [list(row) for row in objects],
        "functions": functions,
    }


def verify() -> None:
    """项目库只读快照不变；测试专用临时库由测试类新建/销毁。"""
    tested_fingerprint = fingerprint()
    engine = sa.create_engine(local_url("pdd_operations"), hide_parameters=True)
    try:
        with engine.connect() as connection:
            before = snapshot(connection)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "apps/api/migrations_v2/tests",
                "-v",
            ],
            cwd=ROOT,
            env={**os.environ, "PDD_V2_INTEGRATION": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        log = PRIVATE / (stamp + "-tests.log")
        private_write(log, result.stdout + result.stderr)
        print("\n".join((result.stdout + result.stderr).splitlines()[-5:]))
        if result.returncode:
            raise RuntimeError("隔离验证失败，禁止创建项目结构；脱敏测试输出已保留")
        subprocess.run(
            [sys.executable, "scripts/render-db-schema.py", "--check"],
            cwd=ROOT,
            check=True,
        )
        with engine.connect() as connection:
            after = snapshot(connection)
        if before != after:
            raise RuntimeError("隔离测试期间项目结构变化，禁止继续")
        if tested_fingerprint != fingerprint():
            raise RuntimeError("验证期间文件发生变化，拒绝签发通过证据")
        evidence = {
            "verified_at": stamp,
            "fingerprint": tested_fingerprint,
            "project_before": before,
            "test_log": str(log.relative_to(ROOT)),
            "result": "passed",
        }
        # 最新验证指针可以原子替换；每份日志和结构备份保持独占不可覆盖。
        temporary = PRIVATE / (stamp + "-verified.json")
        private_write(temporary, json.dumps(evidence, ensure_ascii=False, indent=2))
        temporary.replace(EVIDENCE)
        print("隔离真实PG验证通过，项目库结构快照未变化；验证阶段未写项目库")
    finally:
        engine.dispose()


def schema_backup(expected_cluster: str) -> bytes:
    """固定本机Docker socket，并与TCP迁移目标核对实际数据库/集群身份。"""
    prefix = [
        "docker",
        "--host",
        "unix://" + str(Path.home() / ".docker/run/docker.sock"),
        "exec",
        "pdd-operations-platform-postgres-1",
    ]
    identity = (
        subprocess.run(
            [
                *prefix,
                "psql",
                "-X",
                "-U",
                "pdd_admin",
                "-d",
                "pdd_operations",
                "-At",
                "-c",
                "SELECT current_database() || '|' || system_identifier::text FROM pg_control_system()",
            ],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    if identity != "pdd_operations|" + expected_cluster:
        raise RuntimeError("备份Docker集群与已核验的TCP迁移目标不一致")
    return subprocess.run(
        [
            *prefix,
            "pg_dump",
            "-U",
            "pdd_admin",
            "-d",
            "pdd_operations",
            "--schema-only",
        ],
        capture_output=True,
        check=True,
    ).stdout


def create() -> None:
    """拒绝未验证/变化的文件或非空项目库；事务建表并独立核验所有业务表0行。"""
    evidence = json.loads(EVIDENCE.read_text())
    if evidence["result"] != "passed" or evidence["fingerprint"] != fingerprint():
        raise RuntimeError("缺少与当前文件一致的隔离验证证据")
    engine = sa.create_engine(
        local_url("pdd_operations"),
        hide_parameters=True,
        connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=10000"},
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_advisory_xact_lock(hashtextextended('pdd-v2-schema', 0))"
                )
            )
            before = snapshot(connection)
            if (
                before != evidence["project_before"]
                or before["objects"]
                or before["functions"]
            ):
                raise RuntimeError("项目结构不是已验证的空结构，拒绝覆盖/清空")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            # 已核对空public；备份现存schema/权限/数据库设置，不读取经营数据。
            backup = schema_backup(before["cluster"])
            private_write(PRIVATE / (stamp + "-before-schema.sql"), backup)
            private_write(
                PRIVATE / (stamp + "-before.json"), json.dumps(before, indent=2)
            )
            if evidence["fingerprint"] != fingerprint():
                raise RuntimeError("创建前文件发生变化，拒绝继续")
            migrate(connection)
            inspect_empty(connection)
            if evidence["fingerprint"] != fingerprint():
                raise RuntimeError("创建期间文件发生变化，事务回滚")
        # 新连接：不能把未提交事务里的可见结果当成已落库证据。
        with engine.connect() as connection:
            result = inspect_empty(connection)
            result["server"] = snapshot(connection)
        result.update(
            {
                "created_at": stamp,
                "fingerprint": fingerprint(),
                "verification_log": evidence["test_log"],
            }
        )
        private_write(
            PRIVATE / (stamp + "-created.json"),
            json.dumps(result, ensure_ascii=False, indent=2),
        )
        print(
            "本地空结构已提交并由独立连接核对：30业务表/494字段/50FK，30表均0行；"
            "另有1张Alembic技术版本表；未导入数据或启动同步"
        )
    finally:
        engine.dispose()


def inspect_empty(connection) -> dict:
    """结构精确匹配+全表COUNT，禁止漏表、多表或误写经营内容。"""
    metadata = build_metadata()
    inspector = sa.inspect(connection)
    names = set(inspector.get_table_names(schema="public"))
    expected = {table.name for table in metadata.tables.values()}
    if names != expected | {"alembic_version_v2"}:
        raise RuntimeError("项目表清单不符合30表基线")
    row_counts = {}
    columns = 0
    keys = 0
    for table in metadata.tables.values():
        actual = inspector.get_columns(table.name, schema="public")
        if [c["name"] for c in actual] != list(table.c.keys()):
            raise RuntimeError("字段清单不一致")
        for item, column in zip(actual, table.c, strict=True):
            if (
                item["nullable"] != column.nullable
                or item["comment"] != column.comment
                or str(item["type"].compile(dialect=connection.dialect))
                != str(column.type.compile(dialect=connection.dialect))
            ):
                raise RuntimeError("字段定义与字典不一致")
        columns += len(actual)
        keys += len(inspector.get_foreign_keys(table.name, schema="public"))
        row_counts[table.name] = connection.execute(
            sa.select(sa.func.count()).select_from(table)
        ).scalar_one()
    version = connection.execute(
        sa.text("SELECT version_num FROM public.alembic_version_v2")
    ).scalar_one()
    if (
        columns != 494
        or keys != 50
        or any(row_counts.values())
        or version != "20261028_01"
    ):
        raise RuntimeError("字段/FK/迁移版本/空表验证失败")
    return {
        "business_tables": 30,
        "columns": columns,
        "foreign_keys": keys,
        "revision": version,
        "row_counts": row_counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "create"))
    args = parser.parse_args()
    try:
        (verify if args.action == "verify" else create)()
    except (
        SQLAlchemyError,
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.CalledProcessError,
    ) as error:
        # 不输出连接异常的URL、密码、SQL参数；详细测试日志只含合成样例。
        print(
            f"v2本地操作失败：{type(error).__name__}；未继续后续阶段", file=sys.stderr
        )
        raise SystemExit(1) from None
