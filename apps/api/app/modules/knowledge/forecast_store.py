"""单篇预测 SOP 的受控修订发布、公开视图与同月历史运行。"""

import hashlib
import json
from datetime import date
from html import unescape

from sqlalchemy import text

from app.connectors.forecast_sop import (
    ACCESS_ERRORS,
    DOC_URL,
    DOCUMENT_ID,
    TITLE,
    ForecastSourceError,
)
from app.modules.knowledge.forecast_rules import ForecastRuleError


def read_forecast_sop(connection) -> dict:
    """读取一次当前视图，原文与规则绑定同一修订；未建表/未接收时可区分。"""
    installed = connection.scalar(text("SELECT to_regclass('public.read_forecast_sop')"))
    if installed:
        row = connection.execute(text("SELECT * FROM public.read_forecast_sop")).mappings().first()
        if row:
            title = TITLE
            header = (row["content"] or "").partition("\n")[0].strip()
            if header.startswith("<title>") and header.endswith("</title>"):
                title = unescape(header.removeprefix("<title>").removesuffix("</title>")) or TITLE
            return {**row, "title": title, "issues": row.get("issues") or []}
    return withheld_forecast_sop(reason="not_synced" if installed else "schema_pending")


def withheld_forecast_sop(*, doc=DOCUMENT_ID, revision=None, state="pending", reason):
    """本次未核对到可引用的版本，返回无正文、无公式的公开状态。"""
    return {
        "document_id": doc,
        "source_url": DOC_URL + doc,
        "title": TITLE,
        "revision": revision,
        "content": None,
        "rule": None,
        "issues": [],
        "state": state,
        "reason": reason,
        "checked_at": None,
        "verified_at": None,
        "synced_at": None,
    }


def _mark(c, document_id, *, revision, version, state, reason=None, verified=False):
    return c.scalar(
        text("""UPDATE public.forecast_sop_source SET revision=:rev,version_revision=:version,
        state=:state,reason=:reason,checked_at=statement_timestamp(),
        verified_at=CASE WHEN :verified THEN statement_timestamp() ELSE verified_at END
      WHERE document_id=:doc RETURNING checked_at"""),
        {
            "doc": document_id,
            "rev": revision,
            "version": version,
            "state": state,
            "reason": reason,
            "verified": verified,
        },
    )


def _still_observed(c, doc, checked_at, *, observed_revision=None):
    """把本次观察与写入放在同一短事务；已观察到更高修订或撤权时停止旧发布。"""
    return c.scalar(
        text("""SELECT true FROM public.forecast_sop_source WHERE document_id=:doc AND (
            checked_at=:checked_at OR (state='pending' AND reason='updating'
                AND revision<=CAST(:observed AS bigint))) FOR UPDATE"""),
        {"doc": doc, "checked_at": checked_at, "observed": observed_revision},
    )


def _failed_probe(c, doc, source, reason):
    revision, version = source["revision"], source["version_revision"]
    if reason in ACCESS_ERRORS:
        state, version = "unavailable", None
    elif source["state"] == "unavailable":
        state, reason = "unavailable", source["reason"]
    else:
        state = "stale" if version is not None else "pending"
    _mark(c, doc, revision=revision, version=version, state=state, reason=reason)


def _publish(c, doc, revision, content, rule, stored_digest):
    """保存稳定原文及校验结果，再切换当前入口，任一步失败均回滚。"""
    digest = hashlib.sha256(content.encode()).hexdigest()
    if stored_digest is None:
        params = {
            "doc": doc,
            "rev": revision,
            "content": content,
            "digest": digest,
            "rule": json.dumps(rule, ensure_ascii=False, allow_nan=False),
        }
        inserted = c.execute(
            text("""INSERT INTO public.forecast_sop_versions
            (document_id,revision,content,content_sha256,rule)
            VALUES (:doc,:rev,:content,:digest,CAST(:rule AS jsonb))
            ON CONFLICT(document_id,revision) DO NOTHING"""),
            params,
        )
        if not inserted.rowcount:
            stored_digest = _stored_digest(c, doc, revision)
    if stored_digest is not None and stored_digest != digest:
        _mark(c, doc, revision=revision, version=None, state="pending", reason="revision_changed")
        return
    _mark(c, doc, revision=revision, version=revision, state="ready", verified=True)


def _stored_digest(c, doc, revision):
    return c.scalar(
        text("""SELECT content_sha256 FROM public.forecast_sop_versions
          WHERE document_id=:doc AND revision=:rev"""),
        {"doc": doc, "rev": revision},
    )


def _checked_rule(content, compiler):
    """只接收正式规则核验器的结果。冲突引文由核验器从当前正文提取。"""
    try:
        rule = compiler(content)
    except ForecastRuleError as exc:
        return {
            "status": "rejected",
            "issues": [
                {
                    "message": item["message"],
                    "citations": [
                        {"section": cite["section"], "quote": cite["quote"]}
                        for cite in item["citations"]
                    ],
                }
                for item in exc.issues
            ],
        }
    review = rule.get("interpretation") if isinstance(rule, dict) else None
    if not (
        isinstance(review, dict)
        and type(review.get("schema")) is int
        and review["schema"] == 1
        and review.get("method") == "deepseek"
        and isinstance(review.get("model"), str)
        and review["model"].strip()
    ):
        raise TypeError("FORECAST_SOP_COMPILER_RESULT")
    json.dumps(rule, allow_nan=False)
    return rule


def _sync(c, reader, doc, compiler):
    with c.begin():
        source = (
            c.execute(
                text("""SELECT revision,version_revision,state,reason,checked_at
                    FROM public.forecast_sop_source WHERE document_id=:doc"""),
                {"doc": doc},
            )
            .mappings()
            .one()
        )
    try:
        revision = reader.revision()
    except ForecastSourceError as exc:
        with c.begin():
            if _still_observed(c, doc, source["checked_at"]):
                _failed_probe(c, doc, source, exc.reason)
        return
    with c.begin():
        # 其他问答可先看到同一新版。此问仍持有解读锁，可接手同版；更高修订与撤权不放行。
        if not _still_observed(c, doc, source["checked_at"], observed_revision=revision):
            return
        if source["revision"] is not None and revision < source["revision"]:
            _failed_probe(c, doc, source, "revision_regressed")
            return
        if revision == source["version_revision"]:
            _mark(c, doc, revision=revision, version=revision, state="ready", verified=True)
            return
        started = _mark(c, doc, revision=revision, version=None, state="pending", reason="updating")
        # 21/22 的视图会把非空 JSON 当作生效规则。先撤旧，升级后才能接收新解读。
        if not c.scalar(
            text("""SELECT EXISTS (SELECT FROM pg_attribute
              WHERE attrelid='public.read_forecast_sop'::regclass AND attname='issues'
                AND NOT attisdropped)""")
        ):
            return
        stored_digest = _stored_digest(c, doc, revision)

    # 此刻其他事务已读不到旧公式。CLI/编译期间不持有数据库事务。
    try:
        content = reader.markdown(revision)
        try:
            rule = _checked_rule(content, compiler) if stored_digest is None else None
        except Exception:
            with c.begin():
                if _still_observed(c, doc, started):
                    _mark(
                        c,
                        doc,
                        revision=revision,
                        version=None,
                        state="pending",
                        reason="compiler_failed",
                    )
            return
        after = reader.revision()
    except ForecastSourceError as exc:
        with c.begin():
            if _still_observed(c, doc, started):
                _mark(
                    c,
                    doc,
                    revision=revision,
                    version=None,
                    state="unavailable" if exc.reason in ACCESS_ERRORS else "pending",
                    reason=exc.reason,
                )
        return
    with c.begin():
        if not _still_observed(c, doc, started):
            return
        if after != revision:
            _mark(
                c,
                doc,
                revision=max(revision, after),
                version=None,
                state="pending",
                reason="revision_changed",
            )
        else:
            _publish(c, doc, revision, content, rule, stored_digest)


def _competing_check(c, reader, doc):
    """另一问只读飞书元数据。更高修订或撤权会打断持锁者尚未完成的发布。"""
    try:
        revision = reader.revision()
    except ForecastSourceError as exc:
        state = "unavailable" if exc.reason in ACCESS_ERRORS else "pending"
        if state == "unavailable":
            with c.begin():
                c.execute(
                    text("""UPDATE public.forecast_sop_source SET state='unavailable',
                    version_revision=NULL,reason=:reason,checked_at=statement_timestamp()
                    WHERE document_id=:doc"""),
                    {"doc": doc, "reason": exc.reason},
                )
        return withheld_forecast_sop(doc=doc, state=state, reason=exc.reason)
    with c.begin():
        c.execute(
            text("""UPDATE public.forecast_sop_source SET state='pending',revision=:revision,
            version_revision=NULL,reason='updating',checked_at=statement_timestamp()
            WHERE document_id=:doc AND (revision IS NULL OR revision<:revision)"""),
            {"doc": doc, "revision": revision},
        )
        highest = c.scalar(
            text("SELECT revision FROM public.forecast_sop_source WHERE document_id=:doc"),
            {"doc": doc},
        )
    return withheld_forecast_sop(
        doc=doc, revision=highest, reason="revision_regressed" if revision < highest else "updating"
    )


def sync_forecast_sop(engine, reader, *, compiler, on_verified=None):
    """每次主动调用检查修订。只允许一名发布者，竞争读者仅核对元数据并返回待核。"""
    doc = getattr(reader, "document_id", DOCUMENT_ID)
    if doc != DOCUMENT_ID:
        from app.modules.database_retention import require_local_test

        require_local_test(engine, roles=("pdd_admin", "pdd_ingest"))
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as c:
        with c.begin():
            if not c.scalar(text("SELECT to_regclass('public.forecast_sop_source')")):
                return read_forecast_sop(c)
            c.execute(
                text("""INSERT INTO public.forecast_sop_source(document_id,source_url,state)
                VALUES (:doc,:url,'pending') ON CONFLICT DO NOTHING"""),
                {"doc": doc, "url": DOC_URL + doc},
            )
            if c.scalar(text("SELECT document_id FROM public.forecast_sop_source")) != doc:
                raise ValueError("FORECAST_SOP_SOURCE_MISMATCH")
            locked = c.scalar(text("SELECT pg_try_advisory_lock(726411,20261127)"))
        if not locked:
            return _competing_check(c, reader, doc)
        try:
            _sync(c, reader, doc, compiler)
            with c.begin():
                current = read_forecast_sop(c)
            if on_verified and current["state"] == "active":
                on_verified(current)
            return current
        finally:
            try:
                with c.begin():
                    c.execute(text("SELECT pg_advisory_unlock(726411,20261127)"))
            except Exception:
                # 不把可能仍持有会话锁的连接交回连接池。
                c.invalidate()
                raise


def read_previous_forecast(connection, month: date, input_version: str, *, cutoff=None):
    """已落库运行始终取首次绑定的前次；新输入可按本期截止日取同月前次实际运行。"""
    params = {"month": month, "key": input_version, "cutoff": cutoff}
    row = connection.execute(
        text("""WITH current_run AS (
          SELECT previous_input_version FROM public.forecast_monthly_snapshots
          WHERE month=:month AND input_version=:key)
        SELECT input_version,payload FROM public.forecast_monthly_snapshots
        WHERE month=:month AND input_version<>:key AND (
          input_version=(SELECT previous_input_version FROM current_run)
          OR (NOT EXISTS(SELECT FROM current_run)
              AND (CAST(:cutoff AS date) IS NULL OR cutoff<=CAST(:cutoff AS date))))
        ORDER BY created_at DESC,input_version LIMIT 1"""),
        params,
    ).first()
    return {**row.payload, "input_version": row.input_version} if row and row.payload else None


def save_forecast_snapshot(
    connection, month: date, input_version: str, payload: dict, *, reinterpreted=False
) -> bool:
    """在后台写事务中按月绑定前次并冻结结果。同一输入重复保存返回 False。"""
    cutoff = date.fromisoformat(payload["cutoff"])
    params = {
        "month": month,
        "key": input_version,
        "cutoff": cutoff,
        "rev": payload["rule_revision"],
        "reinterpreted": reinterpreted,
        "payload": json.dumps(payload, ensure_ascii=False, allow_nan=False),
    }
    connection.execute(
        text("SELECT pg_advisory_xact_lock(726412,:period)"),
        {"period": month.year * 100 + month.month},
    )
    if connection.scalar(
        text("""SELECT 1 FROM public.forecast_monthly_snapshots
          WHERE month=:month AND input_version=:key"""),
        params,
    ):
        return False
    # 发布新快照时锁定规则所在行。已有 RR 快照若看不到并发成功的运行或修订，
    # PG 返回 40001，调用方重试整笔计算；已存在的运行无需触碰来源行或检查时刻。
    connection.execute(
        text("""UPDATE public.forecast_sop_source SET version_revision=version_revision
          WHERE version_revision=:rev"""),
        params,
    )
    inserted = connection.execute(
        text("""INSERT INTO public.forecast_monthly_snapshots
        (month,input_version,cutoff,document_id,rule_revision,previous_input_version,payload)
      SELECT :month,:key,:cutoff,s.document_id,s.revision,
        (SELECT prior.input_version FROM public.forecast_monthly_snapshots prior
         WHERE prior.month=:month AND prior.cutoff<=:cutoff AND prior.input_version<>:key
         ORDER BY prior.created_at DESC,prior.input_version LIMIT 1),
        CAST(:payload AS jsonb)
      FROM public.read_forecast_sop s WHERE s.revision=:rev AND s.checked_at=s.verified_at
        AND (s.state='active' OR
             (:reinterpreted AND s.state='unsupported' AND s.reason='unsupported_rule'))"""),
        params,
    )
    if inserted.rowcount:
        return True
    raise ValueError("FORECAST_SOP_RULE_CHANGED")
