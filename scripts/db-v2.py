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
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.baseline_20261028.schema import build_metadata
from migrations_v2.local import local_url, migrate

PRIVATE = ROOT / "var/database-v2"
EVIDENCE = PRIVATE / "verified.json"
CURRENT_HEAD = "20261031_09"
WRITER_UPDATE_COLUMNS = {
    "dataset_versions": {
        "state",
        "content_fingerprint",
        "retained_input_ref",
        "received_at",
        "validation_evidence_ref",
    },
    "task_runs": {
        "status",
        "started_at",
        "finished_at",
        "worker_ref",
        "lease_expires_at",
        "result_version_id",
        "source_updated_text",
        "source_update_observed_at",
        "error_code",
        "error_summary",
        "evidence_ref",
    },
    "current_datasets": {"version_id"},
    "score_standards": {"id"},
    "product_scores": {"id"},
    "sales_targets": {"id"},
}


def current_metadata():
    """在新实例中表达08验收目录；不改01冻结快照或执行任何DDL。"""
    metadata = build_metadata()
    target = metadata.tables["public.refund_targets"]
    target.c.target_rate.nullable = True
    for name, type_, nullable, comment in (
        ("metric_code", sa.Text(), False, "退款率指标代码"),
        ("lower_rate", sa.Numeric(), True, "退款率目标下限"),
        ("upper_rate", sa.Numeric(), True, "退款率目标上限"),
        ("application_policy_code", sa.Text(), True, "滚动30天与自然月适用策略"),
    ):
        target.append_column(sa.Column(name, type_, nullable=nullable, comment=comment))
    sa.Table(
        "weather_publications",
        metadata,
        sa.Column(
            "id",
            sa.Text(),
            sa.ForeignKey("public.dataset_versions.id"),
            primary_key=True,
        ),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("revision_no", sa.BigInteger(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("parser_revision", sa.Text(), nullable=False),
        sa.Column("document", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        schema="public",
    )
    return metadata


def inspect_roles(connection) -> None:
    """只读拒绝危险或缺失的受管运行身份；不改变任何集群级角色/口令。"""
    roles = connection.execute(
        sa.text(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname IN ('pdd_app','pdd_ingest') ORDER BY rolname"
        )
    ).all()
    if len(roles) != 2 or any(not row[1] or any(row[2:]) for row in roles):
        raise RuntimeError("运行角色缺失或具有未批准的管理权限")
    if connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
            "WHERE r.rolname IN ('pdd_app','pdd_ingest'))"
        )
    ).scalar_one():
        raise RuntimeError("运行角色具有未批准的角色成员关系")


def fingerprint() -> str:
    """绑定结构、运行入口和测试内容，任一变化都须重跑隔离验证。"""
    files = [
        *sorted((ROOT / "apps/api/migrations_v2").rglob("*.py")),
        ROOT / "apps/api/alembic-v2.ini",
        ROOT / "docs/database-schema/schema.sql",
        ROOT / "scripts/render-db-schema.py",
        ROOT / "scripts/upgrade-targets-local.py",
        ROOT / "docs/database-schema/target-ranges.sql",
        ROOT / "scripts/upgrade-query-indexes-local.py",
        ROOT / "docs/database-schema/query-performance.sql",
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


def snapshot(connection, *, database: str = "pdd_operations") -> dict:
    """只记录本地目标身份和结构目录，不读取经营行或私密配置。"""
    actual_database, role, version, cluster, owner = connection.execute(
        sa.text(
            "SELECT current_database(), current_user, current_setting('server_version_num')::integer, "
            "(SELECT system_identifier::text FROM pg_control_system()), "
            "(SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())"
        )
    ).one()
    if (
        actual_database != database
        or role != "pdd_admin"
        or version // 10000 != 17
        or owner != role
    ):
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
    extra_objects = connection.execute(
        sa.text(
            "SELECT n.nspname,c.relname,c.relkind::text FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname <> 'public' "
            "AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' "
            "UNION ALL SELECT n.nspname,p.proname,'function' FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname <> 'public' "
            "AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' "
            "UNION ALL SELECT n.nspname,t.typname,'type' FROM pg_type t "
            "JOIN pg_namespace n ON n.oid=t.typnamespace WHERE t.typrelid=0 "
            "AND t.typelem=0 AND n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' "
            "ORDER BY 1,2,3"
        )
    ).all()
    return {
        "database": database,
        "role": role,
        "server_version_num": version,
        "cluster": cluster,
        "objects": [list(row) for row in objects],
        "functions": functions,
        "owner": owner,
        "extra_objects": [list(row) for row in extra_objects],
        "database_acl": connection.execute(
            sa.text(
                "SELECT datacl::text FROM pg_database WHERE datname=current_database()"
            )
        ).scalar_one(),
        "public_acl": connection.execute(
            sa.text("SELECT nspacl::text FROM pg_namespace WHERE nspname='public'")
        ).scalar_one(),
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


def schema_backup(expected_cluster: str, *, database: str = "pdd_operations") -> bytes:
    """固定本机Docker socket，并与TCP迁移目标核对实际数据库/集群身份。"""
    if database == "postgres":
        raise ValueError("管理库不是备份/初始化目标")
    local_url(database)
    prefix = [
        "docker",
        "--host",
        "unix://" + str(Path.home() / ".docker/run/docker.sock"),
        "exec",
        "pdd-operations-platform-postgres-1",
    ]
    try:
        identity = (
            subprocess.run(
                [
                    *prefix,
                    "psql",
                    "-X",
                    "-U",
                    "pdd_admin",
                    "-d",
                    database,
                    "-At",
                    "-c",
                    "SELECT current_database() || '|' || system_identifier::text FROM pg_control_system()",
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            .stdout.decode()
            .strip()
        )
        if identity != database + "|" + expected_cluster:
            raise RuntimeError("备份Docker集群与已核验的TCP迁移目标不一致")
        return subprocess.run(
            [
                *prefix,
                "pg_dump",
                "-U",
                "pdd_admin",
                "-d",
                database,
                "--schema-only",
                "--lock-wait-timeout=10s",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError("本机结构备份进程超时，已中止后续迁移") from None


def create(*, database: str = "pdd_operations") -> None:
    """拒绝未验证/变化的文件或非空项目库；事务建表并独立核验所有业务表0行。"""
    evidence = json.loads(EVIDENCE.read_text())
    if evidence["result"] != "passed" or evidence["fingerprint"] != fingerprint():
        raise RuntimeError("缺少与当前文件一致的隔离验证证据")
    if ScriptDirectory.from_config(
        Config(str(ROOT / "apps/api/alembic-v2.ini"))
    ).get_heads() != [CURRENT_HEAD]:
        raise RuntimeError("初始化仅验证至09，须先审查新的迁移head")
    if database == "postgres":
        raise ValueError("管理库不是初始化目标")
    engine = sa.create_engine(
        local_url(database),
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
            before = snapshot(connection, database=database)
            if (
                before != evidence["project_before"]
                or before["objects"]
                or before["functions"]
                or before["extra_objects"]
            ):
                raise RuntimeError("项目结构不是已验证的空结构，拒绝覆盖/清空")
            inspect_roles(connection)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            # 已核对空public；备份现存schema/权限/数据库设置，不读取经营数据。
            backup = schema_backup(before["cluster"], database=database)
            private_write(PRIVATE / (stamp + "-before-schema.sql"), backup)
            private_write(
                PRIVATE / (stamp + "-before.json"), json.dumps(before, indent=2)
            )
            if evidence["fingerprint"] != fingerprint():
                raise RuntimeError("创建前文件发生变化，拒绝继续")
            migrate(connection)
            # 不依赖其他库历史默认授权；只授本次明确的业务表，不授技术版本表。
            quoted_database = connection.dialect.identifier_preparer.quote(database)
            connection.exec_driver_sql(
                f"REVOKE ALL ON DATABASE {quoted_database} FROM PUBLIC"
            )
            connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {quoted_database} TO pdd_app,pdd_ingest"
            )
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql(
                "GRANT USAGE ON SCHEMA public TO pdd_app,pdd_ingest"
            )
            for table in current_metadata().tables.values():
                connection.exec_driver_sql(
                    f"GRANT SELECT ON public.{table.name} TO pdd_app"
                )
            inspect_empty(connection)
            inspect_permissions(connection)
            if evidence["fingerprint"] != fingerprint():
                raise RuntimeError("创建期间文件发生变化，事务回滚")
        # 新连接：不能把未提交事务里的可见结果当成已落库证据。
        with engine.connect() as connection:
            result = inspect_empty(connection)
            inspect_permissions(connection)
            result["server"] = snapshot(connection, database=database)
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
            "本地09空结构已提交并由独立连接核对：31业务表/506字段/51FK，31表均0行；"
            "另有1张Alembic技术版本表；未导入数据或启动同步"
        )
    finally:
        engine.dispose()


def inspect_empty(connection) -> dict:
    """结构精确匹配+全表COUNT，禁止漏表、多表或误写经营内容。"""
    metadata = current_metadata()
    inspector = sa.inspect(connection)
    names = set(inspector.get_table_names(schema="public"))
    expected = {table.name for table in metadata.tables.values()}
    if names != expected | {"alembic_version_v2"}:
        raise RuntimeError("项目表清单不符合当前31表结构")
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
        columns != 506
        or keys != 51
        or any(row_counts.values())
        or version != CURRENT_HEAD
    ):
        raise RuntimeError("字段/FK/迁移版本/空表验证失败")
    return {
        "business_tables": 31,
        "columns": columns,
        "foreign_keys": keys,
        "revision": version,
        "row_counts": row_counts,
    }


def inspect_permissions(connection) -> None:
    """逐表/逐列检查09有效权限；允许列表仅取既有revision，不扩大写范围。"""
    inspect_roles(connection)
    metadata = current_metadata()
    for role in ("pdd_app", "pdd_ingest"):
        db_rights = connection.execute(
            sa.text(
                "SELECT has_database_privilege(:role,current_database(),'CONNECT'), "
                "has_database_privilege(:role,current_database(),'CREATE'), "
                "has_database_privilege(:role,current_database(),'TEMP'), "
                "has_schema_privilege(:role,'public','USAGE'), "
                "has_schema_privilege(:role,'public','CREATE')"
            ),
            {"role": role},
        ).one()
        if tuple(db_rights) != (True, False, False, True, False):
            raise RuntimeError("运行身份数据库/结构权限不符合最小授权")
        for name in [*(t.name for t in metadata.tables.values()), "alembic_version_v2"]:
            writer = name not in {
                "refund_target_categories",
                "refund_target_styles",
                "alembic_version_v2",
            }
            permissions = connection.execute(
                sa.text(
                    "SELECT p,has_table_privilege(:role,:table,p) FROM unnest(ARRAY["
                    "'SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p"
                ),
                {"role": role, "table": "public." + name},
            ).all()
            for permission, actual in permissions:
                expected = permission == "SELECT" and name != "alembic_version_v2"
                if role == "pdd_ingest":
                    expected = writer and permission in {"SELECT", "INSERT"}
                if actual != expected:
                    raise RuntimeError("运行身份表权限不符合最小授权")
            allowed_updates = (
                WRITER_UPDATE_COLUMNS.get(name, set())
                if role == "pdd_ingest"
                else set()
            )
            column_permissions = connection.execute(
                sa.text(
                    "SELECT a.attname,p,has_column_privilege(:role,c.oid,a.attnum,p) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid=c.oid "
                    "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) p "
                    "WHERE n.nspname='public' AND c.relname=:table "
                    "AND a.attnum>0 AND NOT a.attisdropped"
                ),
                {"role": role, "table": name},
            ).all()
            for column, permission, actual in column_permissions:
                expected = (
                    permission == "SELECT" and name != "alembic_version_v2"
                    if role == "pdd_app"
                    else (writer and permission in {"SELECT", "INSERT"})
                    or (permission == "UPDATE" and column in allowed_updates)
                )
                if (
                    role == "pdd_ingest"
                    and name == "alembic_version_v2"
                    and column == "version_num"
                    and permission == "SELECT"
                ):
                    expected = True
                if actual != expected:
                    raise RuntimeError("运行身份列权限不符合最小授权")
    owners = (
        connection.execute(
            sa.text(
                "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"
            )
        )
        .scalars()
        .all()
    )
    if owners != ["pdd_admin"]:
        raise RuntimeError("结构对象所有者不是受管管理员")


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
