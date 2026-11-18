"""数据库整版保留：只读计划；写操作限定本机项目管理员或随机测试库。"""

import hashlib
import json
import re
import tomllib
from contextlib import contextmanager
from datetime import timedelta
from importlib import import_module
from pathlib import Path

import sqlalchemy as sa
from psycopg import sql

from app.modules.local_storage import require_local_maintenance, verify_connection
from migrations_v2.baseline_20261028.schema import build_metadata

# 归档数据结构格式；19仅增加COMMENT，不能令现有18归档失效或扩大公司升级范围。
HEAD = "20261114_18"
RECONCILIATIONS = "source_reconciliations"
RECONCILIATION_COLUMNS = (
    "source_id",
    "cycle_at",
    "status",
    "last_attempt_at",
    "last_finished_at",
    "last_success_at",
    "legacy_task_id",
    "error_code",
)
POLICY = "db-retention-v1"
TARGETS = {
    "product_daily": "product_daily_facts",
    "product_promotion": "product_promotion_records",
    "product_regions": "product_region_records",
}
ROOT = Path(__file__).resolve().parents[4]
MIGRATION = import_module("migrations_v2.versions.20261110_13_database_retention")
CONTENT = import_module("migrations_v2.versions.20261110_14_content_reuse")


def policy():
    """配置只允许更保守的本地保留参数，拒绝把生产开关当成授权。"""
    with (ROOT / "config/database-retention.toml").open("rb") as f:
        value = tomllib.load(f)
    if (
        value.get("production_apply_enabled") is not False
        or value.get("revision") != POLICY
        or value.get("minimum_hours", 0) < 24
        or value.get("max_versions_per_apply") != 1
        or not 1 <= value.get("lock_timeout_ms", 0) <= 1000
        or not 1 <= value.get("statement_timeout_ms", 0) <= 30000
    ):
        raise ValueError("RETENTION_POLICY")
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def code_revision():
    """绑定维护实现和迁移文件实际内容，未提交本地改动也使旧计划失效。"""
    paths = [
        ROOT / "config/database-retention.toml",
        ROOT / "scripts/database-maintenance.py",
        *sorted((ROOT / "apps/api/app").rglob("*.py")),
        *sorted((ROOT / "apps/api/migrations_v2/versions").glob("*.py")),
    ]
    return digest(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    )


def require_local_test(engine):
    """第一阶段不提供生产apply旁路，环境变量/CLI参数不能解除这个边界。"""
    u = engine.url
    if (
        u.host != "127.0.0.1"
        or u.port != 55432
        or u.username != "pdd_admin"
        or not re.fullmatch(r"pdd_operations_v2_test_[0-9a-f]{12}", u.database or "")
    ):
        raise ValueError("RETENTION_PRODUCTION_DISABLED")


def compatible_local_schema_head(actual):
    """本地仅显式认可18的说明兼容版19；未知修订和其他历史格式仍拒绝。"""
    return actual == HEAD or (HEAD == "20261114_18" and actual == "20261116_19")


def validate_schema(c):
    if not compatible_local_schema_head(
        c.scalar(sa.text("SELECT version_num FROM public.alembic_version_v2"))
    ):
        raise ValueError("RETENTION_SCHEMA_HEAD")
    from app.modules import deployment_storage

    if c.engine.url.database == deployment_storage.DATABASE:
        return deployment_storage.verify_catalog(c)
    expected = {t.name for t in build_metadata().tables.values()} | {
        "weather_publications",
        "source_checkpoints",
        RECONCILIATIONS,
        "alembic_version_v2",
        *MIGRATION.TABLES,
        *CONTENT.TABLES,
        *CONTENT.VIEWS,
    }
    actual = set(
        c.execute(
            sa.text("""SELECT relname FROM pg_class t JOIN pg_namespace n
      ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m','f')
      AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
        AND d.objid=t.oid AND d.deptype='e')""")
        ).scalars()
    )
    if actual != expected:
        raise ValueError("RETENTION_SCHEMA_OBJECTS")
    inspector = sa.inspect(c)
    signature = {}
    baseline = build_metadata().tables
    for table in sorted(expected):
        columns = inspector.get_columns(table, schema="public")
        signature[table] = [(v["name"], str(v["type"]), v["nullable"]) for v in columns]
        if table in TARGETS.values():
            if [v["name"] for v in columns] != list(baseline["public." + table].c.keys()):
                raise ValueError("RETENTION_SCHEMA_COLUMNS")
        if table == RECONCILIATIONS and tuple(v["name"] for v in columns) != RECONCILIATION_COLUMNS:
            raise ValueError("RETENTION_SCHEMA_COLUMNS")
        # 所有业务指向版本的关系已有固定清单；未知关系不能随schema头未变绕过检查。
        signature[table + ":fks"] = inspector.get_foreign_keys(table, schema="public")
    column_count = sum(
        len(value)
        for key, value in signature.items()
        if not key.endswith(":fks") and key not in CONTENT.VIEWS
    )
    expected_fk_count = len(
        re.findall(
            r"REFERENCES public\.[a-z_]+\s*\(",
            (ROOT / "docs/database-schema/schema.sql").read_text(),
        )
    )
    if (
        column_count != 720
        or sum(len(v) for k, v in signature.items() if k.endswith(":fks")) != expected_fk_count
    ):
        raise ValueError("RETENTION_SCHEMA_RELATIONSHIPS")
    if c.scalar(
        sa.text("""SELECT count(*) FROM pg_trigger t
      JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
      WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname LIKE 'pdd_%'
        AND t.tgenabled<>'O'""")
    ):
        raise ValueError("RETENTION_SCHEMA_GUARDS")
    from migrations_v2.baseline_20261028.guards import _trigger_specs
    from migrations_v2.baseline_20261028.helpers import name

    required = {
        (
            table,
            name("pdd_v2_trg", table, suffix),
            "pdd_retention_content" if table in TARGETS.values() and suffix == "content" else func,
        )
        for table, suffix, func, _, _ in _trigger_specs()
    }
    required |= {
        (table, "pdd_retention_publish", "pdd_retention_publish")
        for table in ("dataset_versions", "version_inputs", "current_datasets")
    }
    required |= {
        ("dataset_versions", "pdd_retention_register", "pdd_retention_register"),
        ("current_datasets", "pdd_retention_publication", "pdd_retention_publication"),
        ("dataset_storage", "pdd_retention_storage_guard", "pdd_retention_storage_guard"),
        ("retention_operations", "pdd_retention_complete", "pdd_retention_complete"),
        ("retention_operations", "pdd_retention_operation_guard", "pdd_retention_operation_guard"),
        ("retention_operation_items", "pdd_retention_item_guard", "pdd_retention_item_guard"),
    }
    required |= {(table, "pdd_content_guard", "pdd_content_guard") for table in CONTENT.TABLES}
    required |= {
        (table, "pdd_content_no_truncate", "pdd_v2_forbid_truncate") for table in CONTENT.TABLES
    }
    required |= {(table, "pdd_content_format", "pdd_content_publish") for table in CONTENT.FACTS}
    required |= {
        ("dataset_versions", "pdd_content_publish", "pdd_content_publish"),
        ("fact_content_blocks", "pdd_content_complete", "pdd_content_complete"),
    }
    signature["views"] = {
        name: inspector.get_view_definition(name, schema="public") for name in CONTENT.VIEWS
    }
    guards = set(
        c.execute(
            sa.text("""SELECT r.relname,t.tgname,p.proname FROM pg_trigger t
      JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
      JOIN pg_proc p ON p.oid=t.tgfoid
      WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled='O'""")
        ).all()
    )
    if not required <= guards:
        raise ValueError("RETENTION_SCHEMA_GUARDS: " + repr(sorted(required - guards)))
    signature["guards"] = sorted(guards)
    return digest(signature)


def copy_rows(c, table, version_id=None):
    """UTC、原列顺序的PG COPY文本流，保留NUMERIC/NULL/JSONB/转义与原ID。"""
    allowed = (
        {t.name for t in build_metadata().tables.values()}
        | set(MIGRATION.TABLES)
        | set(CONTENT.TABLES)
        | {"weather_publications", "source_checkpoints", RECONCILIATIONS, "alembic_version_v2"}
    )
    if table not in allowed:
        raise ValueError("RETENTION_TABLE")
    query = sql.SQL("SELECT * FROM public.{}{}").format(
        sql.Identifier(table),
        sql.SQL(" WHERE dataset_version_id={}").format(sql.Literal(version_id))
        if version_id is not None
        else sql.SQL(""),
    )
    keys = sa.inspect(c).get_pk_constraint(table, schema="public")["constrained_columns"]
    query += sql.SQL(" ORDER BY {} ").format(sql.SQL(",").join(map(sql.Identifier, keys)))
    with c.connection.driver_connection.cursor() as cursor:
        with cursor.copy(sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT text)").format(query)) as cp:
            for data in cp:
                yield bytes(data)


def content(c, table, version_id):
    h, size = hashlib.sha256(), 0
    for chunk in copy_rows(c, table, version_id):
        h.update(chunk)
        size += len(chunk)
    count = c.scalar(
        sa.text(f"SELECT count(*) FROM public.{table} WHERE dataset_version_id=:v"),
        {"v": version_id},
    )
    return {"row_count": count, "content_sha256": h.hexdigest(), "copy_bytes": size}


def snapshot(c, *, now=None, with_content=True):
    """单个只读快照内取保护集合，未知时间保留，只有三表完整版本可能成为候选。"""
    schema_hash = validate_schema(c)
    if c.scalar(sa.text("SELECT count(*) FROM public.task_runs WHERE status='running'")):
        raise ValueError("RETENTION_BUSY")
    protected = set(c.execute(sa.text(CONTENT.PROTECTED_SQL)).scalars())
    versions = list(
        c.execute(
            sa.text("""SELECT v.id,v.dataset_code,v.state,s.state AS storage_state,
      v.content_fingerprint,v.period_start,v.period_end,
      (SELECT max(occurred_at) FROM public.dataset_publications
        WHERE previous_version_id=v.id) exited_at
      FROM public.dataset_versions v LEFT JOIN public.dataset_storage s ON s.version_id=v.id
      ORDER BY v.id""")
        ).mappings()
    )
    cutoff = (now or c.scalar(sa.text("SELECT clock_timestamp()"))) - timedelta(
        hours=policy()["minimum_hours"]
    )
    kept, candidates = [], []
    for v in versions:
        reason = (
            "protected"
            if v["id"] in protected
            else "outside_scope"
            if v["dataset_code"] not in TARGETS
            else "not_published"
            if v["state"] != "published"
            else "not_online"
            if v["storage_state"] != "online"
            else "exit_unknown"
            if not v["exited_at"]
            else "grace_period"
            if v["exited_at"] > cutoff
            else None
        )
        if reason:
            kept.append({"version_id": v["id"], "reason": reason})
            continue
        table = TARGETS[v["dataset_code"]]
        candidates.append(
            {
                "version_id": v["id"],
                "dataset_code": v["dataset_code"],
                "table_name": table,
                "fingerprint": v["content_fingerprint"],
                "exited_at": v["exited_at"].isoformat(),
                "period_start": str(v["period_start"]),
                "period_end": str(v["period_end"]),
                **(content(c, table, v["id"]) if with_content else {}),
            }
        )
    result = {
        "database": c.scalar(sa.text("SELECT current_database()")),
        "schema_head": HEAD,
        "schema_sha256": schema_hash,
        "code_revision": code_revision(),
        "policy": POLICY,
        "heads": dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        ),
        "protected": sorted(protected),
        "candidates": candidates,
        "kept": kept,
    }
    result["plan_sha256"] = digest(result)
    return result


def plan(engine, *, now=None):
    """默认只读。now仅测试时用于边界推演，apply始终用真实DB时钟重新算。"""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        c.execute(sa.text("SET TRANSACTION READ ONLY"))
        c.execute(sa.text("SET LOCAL TIME ZONE 'UTC'"))
        c.execute(sa.text("SET LOCAL statement_timeout='30s'"))
        return snapshot(c, now=now)


@contextmanager
def maintenance(engine):
    """固定顺序抢锁，整版30秒预算，任何失败整版回滚且不杀任务。"""
    from app.modules.sales.datasets import SPECS

    require_local_maintenance(engine)
    limits = policy()
    keys = [
        "project-source-sync",
        "m1-product-board",
        "m5-source-report",
        "nmc-weather-publication",
        *sorted({"m3-" + c for c in ("shop_daily", "sales_targets", *SPECS)}),
        "pdd-database-maintenance",
    ]
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as c, c.begin():
        verify_connection(c)
        c.execute(
            sa.text(
                "SELECT set_config('lock_timeout',:lock,true), "
                "set_config('statement_timeout',:budget,true), "
                "set_config('transaction_timeout',:budget,true)"
            ),
            {
                "lock": str(limits["lock_timeout_ms"]) + "ms",
                "budget": str(limits["statement_timeout_ms"]) + "ms",
            },
        )
        c.execute(sa.text("SET LOCAL TIME ZONE 'UTC'"))
        for key in keys:
            if not c.scalar(
                sa.text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": key}
            ):
                raise ValueError("RETENTION_BUSY")
        if c.scalar(sa.text("SELECT count(*) FROM task_runs WHERE status='running'")):
            raise ValueError("RETENTION_BUSY")
        yield c


def _receipt(bundle, key):
    """完整解密校验在抢锁前完成；签名回执只能来自成功的隔离恢复。"""
    import hmac

    from app.modules.archive_store import _safe
    from app.modules.database_archive import file_hash, verify

    manifest = verify(bundle, key)
    receipt = json.loads(_safe(Path(bundle) / "receipt.json").read_text())
    signature = receipt.pop("signature", "")
    expected = hmac.new(key, json.dumps(receipt, sort_keys=True).encode(), "sha256").hexdigest()
    if (
        not hmac.compare_digest(signature, expected)
        or not receipt.get("restored")
        or receipt.get("production_approved") is not False
        or receipt["manifest_sha256"] != digest(manifest)
        or receipt["cipher_sha256"] != file_hash(Path(bundle) / "manifest.enc")
    ):
        raise ValueError("RETENTION_RECEIPT_REQUIRED")
    return manifest, receipt


def _operation(c, manifest, receipt, kind):
    """将归档与准确的单版/单表清单绑定到当前事务；DB触发器独立校验保护与范围。"""
    from uuid import uuid4

    archive = receipt["archive_id"]
    exists = c.scalar(sa.text("SELECT id FROM retention_archives WHERE id=:id"), {"id": archive})
    if not exists:
        c.execute(
            sa.text("""INSERT INTO retention_archives
          (id,database_name,schema_head,format_revision,cipher_sha256,manifest_sha256,
           locations,verified_at,restored_at)
          VALUES(:id,:db,:head,1,:cipher,:manifest,CAST(:locations AS jsonb),
                 clock_timestamp(),:at)"""),
            {
                "id": archive,
                "db": manifest["database"],
                "head": HEAD,
                "cipher": receipt["cipher_sha256"],
                "manifest": receipt["manifest_sha256"],
                # 明确是测试证据，绝不声称本机两目录为生产独立双副本。
                "locations": json.dumps(
                    [
                        {
                            "kind": "local-project-bundle"
                            if manifest["database"] == "pdd_operations"
                            else "local-test-bundle"
                        },
                        {"kind": "isolated-pg-drill"},
                    ]
                ),
                "at": receipt["restored_at"],
            },
        )
    ident = uuid4().hex
    c.execute(
        sa.text("""INSERT INTO retention_operations
      (id,archive_id,kind,plan_sha256,policy_revision,code_revision,transaction_id,status)
      VALUES(:id,:archive,:kind,:plan,:policy,:code,txid_current(),'running')"""),
        {
            "id": ident,
            "archive": archive,
            "kind": kind,
            "plan": manifest["plan"]["plan_sha256"],
            "policy": POLICY,
            "code": code_revision(),
        },
    )
    item = manifest["candidate"]
    c.execute(
        sa.text("""INSERT INTO retention_operation_items
      (operation_id,version_id,table_name,row_count,content_sha256)
      VALUES(:op,:version_id,:table_name,:row_count,:content_sha256)"""),
        {"op": ident, **item},
    )
    c.execute(sa.text("SELECT set_config('pdd.retention_operation',:id,true)"), {"id": ident})
    return ident


def _finish(c, ident):
    c.execute(
        sa.text(
            "UPDATE retention_operations SET status='succeeded',finished_at=clock_timestamp() "
            "WHERE id=:id"
        ),
        {"id": ident},
    )


def apply(engine, selected, bundle, key):
    """仅授权本机库，一个已恢复验证完整版本；未知/过期/新增引用拒绝整次操作。"""
    require_local_maintenance(engine)
    manifest, receipt = _receipt(bundle, key)
    if (
        manifest["database"] != engine.url.database
        or manifest["plan"] != selected
        or manifest["code_revision"] != code_revision()
        or manifest["candidate"] not in selected["candidates"]
    ):
        raise ValueError("RETENTION_PLAN_CHANGED")
    item = manifest["candidate"]
    with maintenance(engine) as c:
        fresh = snapshot(c, with_content=False)
        expected = json.loads(json.dumps(selected))
        expected.pop("plan_sha256")
        for candidate in expected["candidates"]:
            for field in ("row_count", "content_sha256", "copy_bytes"):
                candidate.pop(field)
        fresh.pop("plan_sha256")
        if fresh != expected:
            raise ValueError("RETENTION_PLAN_CHANGED")
        c.execute(
            sa.text("SELECT version_id FROM dataset_storage WHERE version_id=:v FOR UPDATE"),
            {"v": item["version_id"]},
        )
        # 封版原行和schema都未变：无需持写锁再次全量导出。
        operation = _operation(c, manifest, receipt, "offline")
        result = c.execute(
            sa.text(f"DELETE FROM public.{item['table_name']} WHERE dataset_version_id=:v"),
            {"v": item["version_id"]},
        )
        if result.rowcount != item["row_count"]:
            raise ValueError("RETENTION_CONTENT_CHANGED")
        c.execute(
            sa.text(
                "UPDATE dataset_storage SET state='offline',archive_id=:archive WHERE version_id=:v"
            ),
            {"v": item["version_id"], "archive": receipt["archive_id"]},
        )
        _finish(c, operation)
    return {"state": "offline", "version_id": item["version_id"], "operation_id": operation}


def restore(engine, bundle, key):
    """原ID/原值回填指定离线版本；不移动current，不回放包内DDL或覆盖已有业务行。"""
    from app.modules.database_archive import decrypt, restore_copy

    require_local_maintenance(engine)
    manifest, receipt = _receipt(bundle, key)
    if manifest["database"] != engine.url.database or manifest["schema_head"] != HEAD:
        raise ValueError("ARCHIVE_SCHEMA_ADAPTER_REQUIRED")
    item = manifest["candidate"]
    with maintenance(engine) as c:
        if validate_schema(c) != manifest["schema_sha256"]:
            raise ValueError("ARCHIVE_SCHEMA_ADAPTER_REQUIRED")
        current = (
            c.execute(
                sa.text("SELECT * FROM dataset_storage WHERE version_id=:v FOR UPDATE"),
                {"v": item["version_id"]},
            )
            .mappings()
            .one()
        )
        actual = content(c, item["table_name"], item["version_id"])
        if current["state"] == "online":
            if any(actual[k] != item[k] for k in actual):
                raise ValueError("ARCHIVE_EXISTING_CONFLICT")
            return {"state": "online", "version_id": item["version_id"]}
        if actual["row_count"]:
            raise ValueError("ARCHIVE_EXISTING_CONFLICT")
        operation = _operation(c, manifest, receipt, "restore")
        restore_copy(
            c, item["table_name"], decrypt(Path(bundle) / (item["table_name"] + ".enc"), key)
        )
        if content(c, item["table_name"], item["version_id"]) != {
            k: item[k] for k in ("row_count", "content_sha256", "copy_bytes")
        }:
            raise ValueError("ARCHIVE_RESTORE_MISMATCH")
        c.execute(
            sa.text("UPDATE dataset_storage SET state='online' WHERE version_id=:v"),
            {"v": item["version_id"]},
        )
        _finish(c, operation)
    return {"state": "online", "version_id": item["version_id"], "operation_id": operation}
