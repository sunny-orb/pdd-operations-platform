"""本机项目加密归档/恢复演练；独立随机库验收，无生产恢复入口。"""

import hashlib
import json
import os
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import sql

from app.modules.archive_store import _safe
from app.modules.database_retention import (
    CONTENT,
    HEAD,
    MIGRATION,
    RECONCILIATIONS,
    TARGETS,
    code_revision,
    content,
    copy_rows,
    digest,
    require_local_test,
)
from app.modules.local_storage import require_local_maintenance, verify_connection
from migrations_v2.baseline_20261028.schema import build_metadata
from migrations_v2.local import ROOT, local_url

MAGIC = b"PDD-PG-ARCHIVE-1\n"
CHUNK = 1024 * 1024


def encrypt(chunks, path, key):
    """独占600文件，分块AEAD+必需结束帧；没有完整结束帧即视为损坏。"""
    path = _safe(path)
    prefix = os.urandom(8)
    aes = AESGCM(key)
    with open(path, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as f:
        f.write(MAGIC + prefix)
        index = 0
        for chunk in chunks:
            for offset in range(0, len(chunk), CHUNK):
                data = chunk[offset : offset + CHUNK]
                nonce = prefix + index.to_bytes(4, "big")
                sealed = aes.encrypt(nonce, data, MAGIC)
                f.write(struct.pack("!I", len(sealed)) + sealed)
                index += 1
        end = aes.encrypt(prefix + index.to_bytes(4, "big"), b"", MAGIC)
        f.write(struct.pack("!I", len(end)) + end)
        f.flush()
        os.fsync(f.fileno())


def decrypt(path, key):
    """验证每块及结束标记；调用者先完整verify，再允许数据库事务写入。"""
    try:
        with _safe(path).open("rb") as f:
            if f.read(len(MAGIC)) != MAGIC:
                raise ValueError("ARCHIVE_FORMAT")
            prefix = f.read(8)
            index = 0
            while True:
                header = f.read(4)
                if len(header) != 4:
                    raise ValueError("ARCHIVE_TRUNCATED")
                length = struct.unpack("!I", header)[0]
                if not 16 <= length <= CHUNK + 16:
                    raise ValueError("ARCHIVE_FORMAT")
                sealed = f.read(length)
                if len(sealed) != length:
                    raise ValueError("ARCHIVE_TRUNCATED")
                data = AESGCM(key).decrypt(prefix + index.to_bytes(4, "big"), sealed, MAGIC)
                index += 1
                if not data:
                    if f.read(1):
                        raise ValueError("ARCHIVE_TRAILING_DATA")
                    return
                yield data
    except (InvalidTag, OverflowError) as exc:
        raise ValueError("ARCHIVE_INTEGRITY") from exc


def file_hash(path):
    with _safe(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def pg_dump(c, section):
    """仅本机固定Compose服务内PG17工具，沿当前RR导出快照，不在容器写文件。"""
    require_local_maintenance(c.engine)
    verify_connection(c)
    snapshot = c.scalar(sa.text("SELECT pg_export_snapshot()"))
    command = [
        "docker",
        "compose",
        "--project-name",
        "pdd-operations-platform",
        "--env-file",
        str(ROOT / "var/local-db/docker.env"),
        "-f",
        str(ROOT / "compose.yaml"),
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "pdd_admin",
        "-d",
        c.engine.url.database,
        "--schema=public",
        "--no-owner",
        "--no-acl",
        "--no-tablespaces",
        "--section=" + section,
        "--snapshot=" + snapshot,
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
        while block := p.stdout.read(CHUNK):
            yield block
        if p.wait() != 0:
            raise ValueError("ARCHIVE_SCHEMA_EXPORT")


def export(engine, selected, destination, key, *, version_id=None):
    """固定一个候选版本；同时归档依赖元数据/维度/规则，不复制其他大事实表。"""
    require_local_maintenance(engine)
    candidates = selected["candidates"]
    if version_id is None:
        if len(candidates) != 1:
            raise ValueError("ARCHIVE_SELECT_ONE_VERSION")
        version_id = candidates[0]["version_id"]
    matches = [item for item in candidates if item["version_id"] == version_id]
    if len(matches) != 1:
        raise ValueError("ARCHIVE_SELECT_ONE_VERSION")
    destination = _safe(destination)
    destination.mkdir(mode=0o700)
    record = matches[0]
    manifest = {
        "format": 1,
        "database": engine.url.database,
        "schema_head": HEAD,
        "code_revision": code_revision(),
        "plan": selected,
        "candidate": record,
        "files": {},
    }
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            c.execute(sa.text("SET LOCAL TIME ZONE 'UTC'"))
            from app.modules.database_retention import snapshot

            if snapshot(c)["plan_sha256"] != selected["plan_sha256"]:
                raise ValueError("ARCHIVE_PLAN_CHANGED")
            for section in ("pre-data", "post-data"):
                filename = section + ".enc"
                encrypt(pg_dump(c, section), destination / filename, key)
                manifest["files"][filename] = {
                    "kind": "schema",
                    "sha256": file_hash(destination / filename),
                }
            # 精确保存候选行；其余小表用于依赖恢复演练。无谓复制五个大事实表被排除。
            omitted = {*TARGETS.values(), "return_records", "product_sales_records"}
            tables = sorted(
                {t.name for t in build_metadata().tables.values() if t.name not in omitted}
                | set(MIGRATION.TABLES)
                | {n for n in CONTENT.TABLES if not n.startswith("shared_")}
                | {
                    "weather_publications",
                    "source_checkpoints",
                    RECONCILIATIONS,
                    "alembic_version_v2",
                }
            )
            for table in [*tables, record["table_name"]]:
                filename = table + ".enc"
                candidate = table == record["table_name"]
                encrypt(
                    copy_rows(c, table, record["version_id"] if candidate else None),
                    destination / filename,
                    key,
                )
                manifest["files"][filename] = {
                    "kind": "rows",
                    "table": table,
                    "sha256": file_hash(destination / filename),
                    "candidate": candidate,
                }
            manifest["versions"] = [
                dict(r)
                for r in c.execute(
                    sa.text("SELECT * FROM dataset_versions WHERE id=:id"),
                    {"id": record["version_id"]},
                ).mappings()
            ]
            manifest["schema_sha256"] = selected["schema_sha256"]
            # 必需来源资料必须另行可读；synthetic仅用于隔离合成测试。
            manifest["evidence"] = []
            manifest["documentation_locators"] = []
            for v in manifest["versions"]:
                files, locators = evidence_files(v)
                manifest["documentation_locators"].extend(locators)
                for source in files:
                    filename = "evidence-" + str(len(manifest["evidence"])) + ".enc"
                    with source.open("rb") as f:
                        encrypt(iter(lambda: f.read(CHUNK), b""), destination / filename, key)
                    manifest["files"][filename] = {
                        "kind": "evidence",
                        "sha256": file_hash(destination / filename),
                    }
                    manifest["evidence"].append({"ref": str(source), "file": filename})

        encrypt(
            [json.dumps(manifest, sort_keys=True, default=str).encode()],
            destination / "manifest.enc",
            key,
        )
        return destination
    except Exception:
        # 不删除可能有唯一证据的半包；没有manifest的目录永远不能verify/apply。
        raise


def evidence_files(version):
    """沿现有受校验清单收集原始输入与producer；不扫描目录或把当前文档冒充旧规则。"""
    from app.modules.archive_store import require_archive

    files, locators = set(), []
    for field in ("retained_input_ref", "interpretation_ref", "validation_evidence_ref"):
        ref = version.get(field)
        if not ref or ref.startswith("synthetic:"):
            continue
        if ref.startswith("docs/"):
            if ".." in Path(ref.split("#", 1)[0]).parts:
                raise ValueError("ARCHIVE_PATH")
            locators.append(ref)
            continue
        source = _safe(require_archive(ref))
        if source.is_dir():
            document = source / "manifest.json"
            members = json.loads(document.read_text())["files"]
            files.add(document)
            files.update(_safe(source / name) for name in members)
        else:
            files.add(source)
        if version["dataset_code"] == "product_regions" and field == "retained_input_ref":
            files.add(_safe(require_archive(source.parent / "producer-source.json")))
    return sorted(files), locators


def verify(bundle, key):
    bundle = _safe(bundle)
    manifest = json.loads(b"".join(decrypt(bundle / "manifest.enc", key)))
    if manifest.get("format") != 1 or manifest.get("schema_head") != HEAD:
        raise ValueError("ARCHIVE_SCHEMA_ADAPTER_REQUIRED")
    candidate = manifest["candidate"]
    filename = candidate["table_name"] + ".enc"
    if (
        candidate["table_name"] not in TARGETS.values()
        or not {"pre-data.enc", "post-data.enc", filename} <= manifest["files"].keys()
        or manifest["files"][filename].get("candidate") is not True
        or manifest["files"][filename].get("table") != candidate["table_name"]
        or sum(bool(e.get("candidate")) for e in manifest["files"].values()) != 1
    ):
        raise ValueError("ARCHIVE_CANDIDATE_MISSING")
    known = (
        {t.name for t in build_metadata().tables.values()}
        | set(MIGRATION.TABLES)
        | {n for n in CONTENT.TABLES if not n.startswith("shared_")}
        | {"weather_publications", "source_checkpoints", RECONCILIATIONS, "alembic_version_v2"}
    )
    for name, entry in manifest["files"].items():
        if Path(name).name != name or name in (".", "..") or not name.endswith(".enc"):
            raise ValueError("ARCHIVE_PATH")
        if entry["kind"] == "rows" and entry.get("table") not in known:
            raise ValueError("ARCHIVE_TABLE")
        h = hashlib.sha256()
        for block in decrypt(bundle / name, key):
            h.update(block)
        if file_hash(bundle / name) != entry["sha256"]:
            raise ValueError("ARCHIVE_INTEGRITY")
        if entry.get("candidate") and h.hexdigest() != manifest["candidate"]["content_sha256"]:
            raise ValueError("ARCHIVE_CONTENT")
    if set(p.name for p in bundle.iterdir()) - {"receipt.json"} != {
        "manifest.enc",
        *manifest["files"],
    }:
        raise ValueError("ARCHIVE_FILE_SET")
    return manifest


def _sql_text(bundle, name, key):
    # PG17 pg_dump可能含psql的随机restrict标记；它不是SQL，不传给驱动。
    text = b"".join(decrypt(bundle / name, key)).decode()
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("\\") and line != "CREATE SCHEMA public;"
    )


def restore_copy(c, table, chunks):
    with c.connection.driver_connection.cursor() as cursor:
        with cursor.copy(
            sql.SQL("COPY public.{} FROM STDIN WITH(FORMAT text)").format(sql.Identifier(table))
        ) as cp:
            for block in chunks:
                cp.write(block)


def drill(bundle, key):
    """只新建随机PG空库：先结构/原行、后约束触发器；完成后销毁该测试库。"""
    manifest = verify(bundle, key)
    name = "pdd_operations_v2_test_" + uuid4().hex[:12]
    admin = sa.create_engine(
        local_url("postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    engine = None
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER pdd_admin')
    try:
        engine = sa.create_engine(local_url(name), hide_parameters=True)
        require_local_test(engine)
        with engine.begin() as c:
            c.connection.driver_connection.execute(
                _sql_text(bundle, "pre-data.enc", key), prepare=False
            )
            c.execute(sa.text("SET LOCAL TIME ZONE 'UTC'"))
            for filename, entry in manifest["files"].items():
                if entry["kind"] == "rows":
                    restore_copy(c, entry["table"], decrypt(Path(bundle) / filename, key))
            c.connection.driver_connection.execute(
                _sql_text(bundle, "post-data.enc", key), prepare=False
            )
            item = manifest["candidate"]
            actual = content(c, item["table_name"], item["version_id"])
            if any(actual[k] != item[k] for k in actual):
                raise ValueError("ARCHIVE_RESTORE_MISMATCH")
        receipt = {
            "restored": True,
            "production_approved": False,
            "manifest_sha256": digest(manifest),
            "cipher_sha256": file_hash(Path(bundle) / "manifest.enc"),
            "restored_at": datetime.now(UTC).isoformat(),
            "schema_head": HEAD,
            "database": manifest["database"],
            "archive_id": uuid4().hex,
        }
        # 回执绑定密钥；不能用一份手填JSON声称已完成恢复。
        import hmac

        receipt["signature"] = hmac.new(
            key, json.dumps(receipt, sort_keys=True).encode(), "sha256"
        ).hexdigest()
        p = _safe(Path(bundle) / "receipt.json")
        with open(p, "x", opener=lambda p, f: os.open(p, f, 0o600)) as f:
            json.dump(receipt, f, sort_keys=True)
        return receipt
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()
