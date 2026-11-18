"""售后固定输入发布后的受限统计维护；不改变经营记录或来源任务状态。"""

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import time
import tomllib
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_payload import atomic_bytes, safe
from app.modules.local_storage import project_endpoint, verify_connection

from .rpa_ingestion import REVISION

ROOT = Path(__file__).resolve().parents[5]
TABLES = {
    "return_records": ("rpa_returns",),
    "product_sales_records": ("rpa_shipped_sales", "rpa_cancelled_sales"),
}
COLUMNS = "dataset_version_id,row_kind,period_start,period_end"
LOCKS = ("project-source-sync", "m5-source-report", "aftersales-statistics")
SESSION = {
    "lock_timeout": ("lock_timeout_ms", 100, "ms"),
    "statement_timeout": ("statement_timeout_ms", 20000, "ms"),
    "transaction_timeout": ("transaction_timeout_ms", 25000, "ms"),
    "maintenance_work_mem": ("maintenance_work_mem_mb", 16, "MB"),
    "vacuum_buffer_usage_limit": ("vacuum_buffer_usage_limit_mb", 1, "MB"),
    "vacuum_cost_delay": ("vacuum_cost_delay_ms", 5, "ms"),
    "vacuum_cost_limit": ("vacuum_cost_limit", 50, ""),
}
CODES = {
    "STATISTICS_PATH",
    "STATISTICS_POLICY",
    "STATISTICS_STATE",
    "STATISTICS_IDENTITY",
    "STATISTICS_PERMISSION",
    "STATISTICS_TIMEOUT",
    "STATISTICS_FAILED",
    "STATISTICS_INTERRUPTED",
}


def _policy(root):
    path = safe(root / "config/aftersales-statistics.toml")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("STATISTICS_POLICY")
    policy = tomllib.loads(path.read_text())
    if (
        set(policy)
        != {"enabled", "retry_seconds", "max_load_average", *(v[0] for v in SESSION.values())}
        or type(policy["enabled"]) is not bool
        or type(policy["retry_seconds"]) is not int
        or policy["retry_seconds"] != 300
        or type(policy["max_load_average"]) not in (int, float)
        or not 0 < policy["max_load_average"] <= 4
        or any(type(policy[k]) is not int or policy[k] != limit for k, limit, _ in SESSION.values())
    ):
        raise ValueError("STATISTICS_POLICY")
    return policy


def _endpoint(engine, root):
    if os.environ.get("APP_ENV") == "production":
        from app.modules.deployment_storage import require_context, require_endpoint

        require_context()
        require_endpoint(engine)
        if root != Path("/app"):
            raise ValueError("STATISTICS_PATH")
    elif project_endpoint(engine):
        if root != ROOT or engine.url.query:
            raise ValueError("STATISTICS_IDENTITY")
    else:
        from app.modules.database_retention import require_local_test

        require_local_test(engine)
        if engine.url.query:
            raise ValueError("STATISTICS_IDENTITY")


def _session(c):
    try:
        if os.environ.get("APP_ENV") == "production":
            from app.modules.deployment_storage import require_session

            require_session(c)
        else:
            verify_connection(c)
    except ValueError:
        raise ValueError("STATISTICS_IDENTITY") from None


def _state(path):
    safe(path)
    if not path.exists():
        return {"revision": 1, "tables": {}}
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or info.st_uid != os.getuid()
        or info.st_size > 8192
    ):
        raise ValueError("STATISTICS_STATE")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
        value = json.load(stream)
    if (
        not isinstance(value, dict)
        or set(value) != {"revision", "tables"}
        or type(value["revision"]) is not int
        or value["revision"] != 1
        or not isinstance(value["tables"], dict)
        or not set(value["tables"]) <= set(TABLES)
    ):
        raise ValueError("STATISTICS_STATE")
    fields = {
        "attempted_token",
        "attempted_at",
        "succeeded_token",
        "succeeded_at",
        "status",
        "code",
    }
    for entry in value["tables"].values():
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("STATISTICS_STATE")
        for key in ("attempted_token", "succeeded_token"):
            if entry[key] is not None and not (
                isinstance(entry[key], str) and re.fullmatch("[0-9a-f]{64}", entry[key])
            ):
                raise ValueError("STATISTICS_STATE")
        for key in ("attempted_at", "succeeded_at"):
            if entry[key] is not None and (
                type(entry[key]) not in (int, float)
                or not math.isfinite(entry[key])
                or entry[key] < 0
            ):
                raise ValueError("STATISTICS_STATE")
        if entry["status"] not in {"running", "succeeded", "failed", "interrupted"} or entry[
            "code"
        ] not in {*CODES, None}:
            raise ValueError("STATISTICS_STATE")
    return value


def _save(path, state):
    payload = json.dumps(state, separators=(",", ":")).encode()
    if len(payload) > 8192:
        raise ValueError("STATISTICS_STATE")
    atomic_bytes(safe(path), payload, replace=path.exists())


def _error(exc):
    if str(exc) in CODES:
        return str(exc)
    if isinstance(exc, sa.exc.SQLAlchemyError):
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if code in {"55P03", "57014", "25P04"}:
            return "STATISTICS_TIMEOUT"
        if code == "42501":
            return "STATISTICS_PERMISSION"
    if str(exc) == "ARCHIVE_PATH":
        return "STATISTICS_PATH"
    return "STATISTICS_FAILED"


def _tokens(c):
    rows = (
        c.execute(
            sa.text("""SELECT id,dataset_code,content_fingerprint FROM dataset_versions
        WHERE state='published' AND row_schema_revision=:revision AND dataset_code=ANY(:codes)
        ORDER BY id"""),
            {"revision": REVISION, "codes": [code for codes in TABLES.values() for code in codes]},
        )
        .mappings()
        .all()
    )
    result = {}
    for table, codes in TABLES.items():
        inputs = [(r["id"], r["content_fingerprint"]) for r in rows if r["dataset_code"] in codes]
        if inputs:
            result[table] = hashlib.sha256(
                json.dumps(inputs, separators=(",", ":")).encode()
            ).hexdigest()
    return result


def _maintain_locked(engine, path, policy, now):
    state = _state(path)
    selected = None
    try:
        with engine.connect() as c, c.begin():
            # 参数仅来自已验证的固定配置；true使设置随本次事务结束自动恢复。
            for name, (key, _, unit) in SESSION.items():
                c.execute(
                    sa.text("SELECT set_config(:name,:value,true)"),
                    {"name": name, "value": f"{policy[key]}{unit}"},
                )
            _session(c)
            for key in LOCKS:
                if not c.scalar(
                    sa.text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": key}
                ):
                    return {"status": "busy"}
            if c.scalar(
                sa.text("""SELECT 1 FROM task_runs WHERE status='running'
                AND (job_code='sync_project' OR job_code LIKE '%aftersales%'
                OR job_code=ANY(:jobs)) LIMIT 1"""),
                {
                    "jobs": [
                        "sync_jst_returns",
                        "sync_jst_shipped_sales",
                        "sync_jst_cancelled_sales",
                    ]
                },
            ):
                return {"status": "deferred", "code": "SYNC_RUNNING"}
            tokens = _tokens(c)
            if not tokens:
                return {"status": "no_rpa"}
            pending = False
            for table, token in tokens.items():
                prior = state["tables"].get(table, {})
                if prior.get("succeeded_token") == token:
                    continue
                pending = True
                if (
                    prior.get("attempted_at") is not None
                    and now - prior["attempted_at"] < policy["retry_seconds"]
                ):
                    continue
                selected = table
                state["tables"][table] = {
                    "attempted_token": token,
                    "attempted_at": now,
                    "succeeded_token": prior.get("succeeded_token"),
                    "succeeded_at": prior.get("succeeded_at"),
                    "status": "running",
                    "code": None,
                }
                _save(path, state)
                if not c.scalar(
                    sa.text("SELECT has_table_privilege(current_user,:table,'MAINTAIN')"),
                    {"table": "public." + table},
                ):
                    raise ValueError("STATISTICS_PERMISSION")
                # 表名和列名只能取本模块常量，不接受外部SQL或扩大到全库。
                c.execute(sa.text(f"ANALYZE public.{table} ({COLUMNS})"))
                break
            if selected is None:
                return {"status": "not_due" if pending else "unchanged"}
        # 已提交才保存成功；提交后被kill最多重做统计，不会复制业务事实。
        entry = state["tables"][selected]
        entry.update(
            status="succeeded",
            code=None,
            succeeded_token=entry["attempted_token"],
            succeeded_at=now,
        )
        _save(path, state)
        return {"status": "succeeded", "table": selected}
    except BaseException as exc:
        if selected is not None:
            state["tables"][selected].update(
                status="failed" if isinstance(exc, Exception) else "interrupted",
                code=_error(exc) if isinstance(exc, Exception) else "STATISTICS_INTERRUPTED",
            )
            try:
                _save(path, state)
            except Exception:
                if isinstance(exc, Exception):
                    return {"status": "failed", "code": "STATISTICS_STATE"}
        if not isinstance(exc, Exception):
            raise
        return {
            "status": "failed",
            "code": _error(exc),
            **({"table": selected} if selected else {}),
        }


def maintain_statistics(engine, project_root, *, sync_complete, now=None):
    """正常空闲轮最多分析一表；失败只返回维护状态，中断仍交给原进程监督。"""
    try:
        if not sync_complete:
            return {"status": "deferred", "code": "SYNC_INCOMPLETE"}
        root = safe(project_root)
        policy = _policy(root)
        if not policy or not policy["enabled"]:
            return {"status": "disabled"}
        if not math.isfinite(os.getloadavg()[0]) or os.getloadavg()[0] > policy["max_load_average"]:
            return {"status": "deferred", "code": "HOST_LOAD"}
        try:
            _endpoint(engine, root)
        except ValueError as exc:
            if str(exc) == "STATISTICS_PATH":
                raise
            raise ValueError("STATISTICS_IDENTITY") from None
        directory = safe(root / "var/maintenance")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = safe(directory / "aftersales-statistics.json")
        lock = safe(directory / ".aftersales-statistics.lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("STATISTICS_PATH")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            at = time.time() if now is None else now
            if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                raise ValueError("STATISTICS_STATE")
            return _maintain_locked(engine, path, policy, at)
        finally:
            os.close(fd)
    except Exception as exc:
        return {"status": "failed", "code": _error(exc)}
