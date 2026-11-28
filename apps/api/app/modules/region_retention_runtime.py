"""商品地区回收的固定身份、五分钟续做和小批资源预算，不接触来源状态。"""

import fcntl
import json
import math
import os
import stat
import time
import tomllib
from pathlib import Path

import sqlalchemy as sa

from app.modules.archive_payload import atomic_bytes, safe
from app.modules.database_retention import REGION_SCHEMA_HEADS
from app.modules.local_storage import project_endpoint, verify_connection

ROOT = Path(__file__).resolve().parents[4]
LIMITS = {
    "interval_seconds": 300,
    "minimum_hours": 24,
    "batch_rows": 1000,
    "max_batches": 10,
    "max_seconds": 10,
    "statement_timeout_ms": 1500,
    "lock_timeout_ms": 100,
    "transaction_timeout_ms": 2000,
}
STATES = {
    "running",
    "idle",
    "busy",
    "verifying",
    "retained",
    "reclaiming",
    "reclaimed",
    "failed",
    "interrupted",
    "deferred",
    "schema_pending",
    "budget",
}


def _policy(root):
    path = safe(root / "config/region-retention.toml")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("REGION_RETENTION_POLICY")
    value = tomllib.loads(path.read_text())
    if (
        set(value) != {"enabled", "max_load_average", "batch_pause_seconds", *LIMITS}
        or type(value["enabled"]) is not bool
        or any(type(value[k]) is not int or value[k] != limit for k, limit in LIMITS.items())
        or type(value["max_load_average"]) not in (int, float)
        or not 0 < value["max_load_average"] <= 2.0
        or type(value["batch_pause_seconds"]) not in (int, float)
        or not 0.25 <= value["batch_pause_seconds"] <= 1
    ):
        raise ValueError("REGION_RETENTION_POLICY")
    return value


def _endpoint(engine, root):
    if os.environ.get("APP_ENV") == "production":
        from app.modules.deployment_storage import require_context, require_endpoint

        require_context()
        require_endpoint(engine)
        if root != Path("/app"):
            raise ValueError("REGION_RETENTION_PATH")
    elif project_endpoint(engine):
        if root != ROOT or engine.url.query:
            raise ValueError("REGION_RETENTION_IDENTITY")
    else:
        from app.modules.database_retention import require_local_test

        require_local_test(engine, roles=("pdd_admin", "pdd_ingest"))
        if engine.url.query:
            raise ValueError("REGION_RETENTION_IDENTITY")


def _session(c):
    if os.environ.get("APP_ENV") == "production":
        from app.modules.deployment_storage import require_session

        require_session(c)
    else:
        verify_connection(c)


def _state(path):
    safe(path)
    if not path.exists():
        return None
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as f:
        info = os.fstat(f.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 4096
        ):
            raise ValueError("REGION_RETENTION_STATE")
        value = json.load(f)
    if (
        not isinstance(value, dict)
        or set(value) != {"revision", "attempted_at", "retry_after", "status"}
        or type(value["revision"]) is not int
        or value["revision"] != 1
        or type(value["attempted_at"]) not in (int, float)
        or not math.isfinite(value["attempted_at"])
        or value["attempted_at"] < 0
        or type(value["retry_after"]) not in (int, float)
        or not math.isfinite(value["retry_after"])
        or value["retry_after"] < value["attempted_at"] + LIMITS["interval_seconds"]
        or value["status"] not in STATES
    ):
        raise ValueError("REGION_RETENTION_STATE")
    return value


def _save(path, at, status, *, elapsed=0):
    atomic_bytes(
        path,
        json.dumps(
            {
                "revision": 1,
                "attempted_at": at,
                "status": status,
                "retry_after": at + max(0, elapsed) + LIMITS["interval_seconds"],
            },
            sort_keys=True,
        ).encode(),
        replace=True,
    )


def _busy_host(policy):
    load = os.getloadavg()[0]
    return not math.isfinite(load) or load > policy["max_load_average"]


def _error(exc):
    if isinstance(exc, ValueError) and str(exc) == "ARCHIVE_PATH":
        return "REGION_RETENTION_PATH"
    known = {"REGION_RETENTION_" + key for key in ("PATH", "POLICY", "STATE", "IDENTITY")}
    if isinstance(exc, ValueError) and str(exc) in known:
        return str(exc)
    if isinstance(exc, ValueError) and str(exc) == "RETENTION_SCHEMA_SIGNATURE":
        return "REGION_RETENTION_SCHEMA"
    if isinstance(exc, sa.exc.SQLAlchemyError):
        if getattr(getattr(exc, "orig", None), "sqlstate", None) in {"55P03", "57014", "25P04"}:
            return "REGION_RETENTION_TIMEOUT"
    return "REGION_RETENTION_FAILED"


def _session_budget(c, policy):
    # 目录预检与删除使用同样的短事务限制，不能让多条目录查询累加成长事务。
    for name, value in (
        ("statement_timeout", f"{policy['statement_timeout_ms']}ms"),
        ("lock_timeout", f"{policy['lock_timeout_ms']}ms"),
        ("transaction_timeout", f"{policy['transaction_timeout_ms']}ms"),
        ("work_mem", "4MB"),
        ("max_parallel_workers_per_gather", "0"),
    ):
        c.execute(sa.text("SELECT set_config(:name,:value,true)"), {"name": name, "value": value})


def _run(engine, path, policy, at):
    state = _state(path)
    if state and at < state["retry_after"]:
        return {"status": "not_due"}
    from app.modules import region_retention
    from app.modules.database_retention import validate_schema

    started = time.monotonic()
    _save(path, at, "running")
    result = {"status": "idle", "checked_rows": 0, "deleted_rows": 0, "batches": 0}
    try:
        # 目录校验只读，先确认20；18/19兼容切换期不申请回收写锁。
        with engine.connect() as c, c.begin():
            c.exec_driver_sql("SET TRANSACTION READ ONLY")
            _session_budget(c, policy)
            if time.monotonic() - started > policy["max_seconds"] - 2:
                result["status"] = "budget"
                _save(path, at, result["status"], elapsed=time.monotonic() - started)
                return result
            _session(c)
            revision = c.scalar(sa.text("SELECT version_num FROM public.alembic_version_v2"))
            if revision not in REGION_SCHEMA_HEADS:
                result["status"] = "schema_pending"
                _save(path, at, result["status"], elapsed=time.monotonic() - started)
                return result
            validate_schema(c)
        step_policy = region_retention.Policy(
            batch_rows=policy["batch_rows"],
            statement_timeout_ms=policy["statement_timeout_ms"],
            lock_timeout_ms=policy["lock_timeout_ms"],
            max_seconds=policy["max_seconds"],
        )
        for _ in range(policy["max_batches"]):
            remaining = policy["max_seconds"] - (time.monotonic() - started)
            if remaining < policy["transaction_timeout_ms"] / 1000:
                result["status"] = "budget"
                break
            if _busy_host(policy):
                result.update(status="deferred", code="HOST_LOAD")
                break
            with engine.connect() as c, c.begin():
                _session_budget(c, policy)
                if time.monotonic() - started > policy["max_seconds"] - 2:
                    result["status"] = "budget"
                    break
                _session(c)
                batch = region_retention.step(c, step_policy)
                if batch["status"] not in STATES:
                    raise ValueError("REGION_RETENTION_STATE")
            # 只有事务成功提交后才累加结果；不把回滚删除记录成回收成功。
            result.update(status=batch["status"], version_id=batch.get("version_id"))
            result["batches"] += 1
            for key in ("checked_rows", "deleted_rows"):
                result[key] += batch.get(key, 0)
            if batch["status"] not in ("verifying", "reclaiming"):
                break
            time.sleep(policy["batch_pause_seconds"])
        _save(path, at, result["status"], elapsed=time.monotonic() - started)
        return result
    except BaseException as exc:
        status = "failed" if isinstance(exc, Exception) else "interrupted"
        _save(path, at, status, elapsed=time.monotonic() - started)
        if not isinstance(exc, Exception):
            raise
        return dict(result, status="failed", code=_error(exc))


def maintain_regions(engine, root, *, sync_complete, now=None):
    """正常同步后有界维护；维护错误不修改来源结果，失败/预算退出五分钟后续做。"""
    try:
        if not sync_complete:
            return {"status": "deferred", "code": "SYNC_INCOMPLETE"}
        root = safe(root)
        policy = _policy(root)
        if not policy or not policy["enabled"]:
            return {"status": "disabled"}
        if _busy_host(policy):
            return {"status": "deferred", "code": "HOST_LOAD"}
        try:
            _endpoint(engine, root)
        except ValueError as exc:
            if str(exc) == "REGION_RETENTION_PATH":
                raise
            raise ValueError("REGION_RETENTION_IDENTITY") from None
        directory = safe(root / "var/maintenance")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("REGION_RETENTION_PATH")
        path = safe(directory / "region-retention.json")
        lock = safe(directory / ".region-retention.lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("REGION_RETENTION_PATH")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            at = time.time() if now is None else now
            if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                raise ValueError("REGION_RETENTION_STATE")
            return _run(engine, path, policy, at)
        finally:
            os.close(fd)
    except Exception as exc:
        return {"status": "failed", "code": _error(exc)}
