"""预测知识在真实隔离 PG 的修订、当前权限和同月运行记录。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import sqlalchemy as sa
from sqlalchemy import text

pytest_plugins = ["tests.sales_pg_support"]
MODEL = {"schema": 1, "method": "deepseek", "model": "测试模型"}


class Source:
    def __init__(self, revision=4, content="第一版 | 近 3 日 |"):
        self.current, self.body = revision, content
        self.downloads = []

    def revision(self):
        if isinstance(self.current, Exception):
            raise self.current
        return self.current

    def markdown(self, revision):
        self.downloads.append(revision)
        return self.body


def compile_rule(content):
    from app.modules.knowledge.forecast_rules import ForecastRuleError

    if "不能执行" in content:
        raise ForecastRuleError("输入包含了无法使用的公式")
    return {"formula": content.split("|")[1].strip(), "interpretation": MODEL}


def api_read(engine):
    from app.modules.knowledge.forecast_store import read_forecast_sop

    with engine.begin() as c:
        c.exec_driver_sql("SET LOCAL ROLE pdd_app")
        return read_forecast_sop(c)


def sync(engine, source):
    from app.modules.knowledge.forecast_store import sync_forecast_sop

    return sync_forecast_sop(engine, source, compiler=compile_rule)


def effective_read(engine):
    from app.modules.knowledge.interpretation import effective_forecast_sop

    with engine.begin() as c:
        c.exec_driver_sql("SET TRANSACTION READ ONLY")
        c.exec_driver_sql("SET LOCAL ROLE pdd_app")
        return effective_forecast_sop(c)


def install_legacy_revision(engine, source):
    """复原 21/22 已接收原文、当时未能提取规则的一行，用于读取和迁移兼容。"""
    from app.connectors.forecast_sop import DOC_URL, DOCUMENT_ID

    params = {
        "doc": DOCUMENT_ID,
        "url": DOC_URL + DOCUMENT_ID,
        "rev": source.current,
        "body": source.body,
    }
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO forecast_sop_source(document_id,source_url,state)
            VALUES (:doc,:url,'pending')"""),
            params,
        )
        c.execute(
            text("""INSERT INTO forecast_sop_versions
            (document_id,revision,content,content_sha256) VALUES (
                :doc,:rev,:body,encode(sha256(convert_to(:body,'UTF8')),'hex'))"""),
            params,
        )
        c.execute(
            text("""UPDATE forecast_sop_source SET revision=:rev,version_revision=:rev,
            state='ready',verified_at=checked_at WHERE document_id=:doc"""),
            params,
        )
    return api_read(engine)


@pytest.mark.parametrize(
    "result",
    [
        {"terms": []},
        {"interpretation": {"schema": 2, "method": "deepseek", "model": "test"}},
    ],
)
def test_unverified_compiler_payload_never_becomes_a_legacy_rule(sales_pg, result):
    from app.modules.knowledge.forecast_store import sync_forecast_sop
    from tests.test_knowledge_forecast_rules import SOP

    source = Source(content=SOP)
    record = sync_forecast_sop(sales_pg, source, compiler=lambda _: result)
    assert (record["state"], record["reason"], record["rule"]) == (
        "pending",
        "compiler_failed",
        None,
    )
    assert effective_read(sales_pg)["rule"] is None


def test_checked_interpretation_is_frozen_for_questions_and_same_revision_recovery(sales_pg):
    from app.connectors.forecast_sop import ForecastSourceError
    from app.modules.knowledge.forecast_rules import compile_forecast_rule
    from app.modules.knowledge.forecast_store import sync_forecast_sop
    from tests.test_knowledge_forecast_rules import SOP

    source = Source(content=SOP.replace("4. 月末预测", "4. 经营测算办法"))
    checked = {
        **compile_forecast_rule(SOP),
        "interpretation": {"schema": 1, "method": "deepseek", "model": "测试模型"},
    }
    calls = []

    def compiler(content):
        calls.append(content)
        return checked

    def run():
        return sync_forecast_sop(sales_pg, source, compiler=compiler)

    first = run()
    for _ in range(2):
        observed = effective_read(sales_pg)
        assert (observed["state"], observed["rule"], observed["issues"]) == ("active", checked, [])
    assert run()["synced_at"] == first["synced_at"]
    source.current = ForecastSourceError("forbidden")
    assert run()["content"] is None
    source.current = 4
    original = source.body
    source.body += "\n同一修订号下出现不同内容。"
    changed = run()
    assert (changed["state"], changed["reason"], changed["rule"]) == (
        "pending",
        "revision_changed",
        None,
    )
    source.body = original
    restored = run()
    assert (restored["synced_at"], restored["rule"]) == (first["synced_at"], checked)
    assert calls == [original]
    assert source.downloads == [4, 4, 4]


def test_rejected_revision_preserves_its_citations_and_blocks_legacy_snapshot_override(sales_pg):
    from datetime import date

    from app.connectors.forecast_sop import ForecastSourceError
    from app.modules.knowledge.forecast_rules import ForecastRuleError
    from app.modules.knowledge.forecast_store import save_forecast_snapshot, sync_forecast_sop
    from tests.sales_pg_support import downgrade_to
    from tests.test_knowledge_forecast_rules import FORMULA, SOP

    source = Source()
    sync(sales_pg, source)
    source.current, source.body = 5, SOP
    issue = {
        "message": "中心估计与情景区间的依据需核对。",
        "citations": [
            {"section": "4. 月末预测", "quote": FORMULA},
            {"section": "4. 月末预测", "quote": "基准取近2日与近4日日均之间"},
        ],
    }
    calls = []

    def reject(content):
        calls.append(content)
        raise ForecastRuleError("FORECAST_RULE_REJECTED", issues=[dict(issue, trace="内部响应")])

    def run():
        return sync_forecast_sop(sales_pg, source, compiler=reject)

    first = run()
    record = effective_read(sales_pg)
    assert (record["revision"], record["state"], record["rule"]) == (5, "unsupported", None)
    assert (record["reason"], record["issues"]) == ("interpretation_rejected", [issue])
    with (
        pytest.raises(sa.exc.DBAPIError, match="FORECAST_INTERPRETATION_PRESENT"),
        sales_pg.begin() as c,
    ):
        downgrade_to(c, "20261127_22")
    with sales_pg.begin() as c:
        c.exec_driver_sql("SET LOCAL ROLE pdd_app")
        assert c.execute(text("SELECT state,rule,issues FROM public.read_forecast_sop")).one() == (
            "unsupported",
            None,
            [issue],
        )
    with sales_pg.begin() as c, pytest.raises(ValueError, match="FORECAST_SOP_RULE_CHANGED"):
        save_forecast_snapshot(
            c,
            date(2026, 11, 1),
            "rejected",
            {"cutoff": "2026-11-10", "rule_revision": 5},
            reinterpreted=True,
        )
    source.current = ForecastSourceError("temporary")
    assert run()["issues"] == [issue]
    # 来源失联恰好发生在问答核验与经营快照之间，旧引文也要撤回。
    from app.modules.assistant.forecast_reports import _read_verified
    from app.modules.product_board.repository import BoardError

    with pytest.raises(BoardError) as unavailable:
        _read_verified(sales_pg, "business_knowledge", {"keyword": "预测权重怎么算"}, record)
    assert unavailable.value.code == "DATA_VERSION_CHANGED"
    with sales_pg.begin() as c:
        c.exec_driver_sql("UPDATE forecast_sop_source SET verified_at=now()-interval '25 hours'")
    for state in ("expired", "unavailable"):
        hidden = effective_read(sales_pg)
        assert (hidden["state"], hidden["content"], hidden["rule"], hidden["issues"]) == (
            state,
            None,
            None,
            [],
        )
        source.current = ForecastSourceError("deleted")
        run()
    source.current = 5
    restored = run()
    assert (restored["synced_at"], restored["issues"]) == (first["synced_at"], [issue])
    assert calls == [SOP]


@pytest.mark.parametrize("failure", [RuntimeError, ValueError])
def test_model_timeout_retries_same_revision_without_publishing_older_or_racing_work(
    sales_pg, failure
):
    from app.modules.knowledge.forecast_store import sync_forecast_sop

    started, release = Event(), Event()
    source = Source()
    sync(sales_pg, source)
    source.current, source.body = 5, "新版计算方法"

    def temporary(_):
        raise failure("仅供测试的私密响应")

    failed = sync_forecast_sop(sales_pg, source, compiler=temporary)
    assert (failed["state"], failed["reason"], failed["revision"]) == (
        "pending",
        "compiler_failed",
        5,
    )
    assert "私密响应" not in str(failed)
    rule = {
        "terms": [{"days": 2, "weight": "1"}],
        "interpretation": {
            "schema": 1,
            "method": "deepseek",
            "model": "测试模型",
        },
    }

    def slow(_):
        started.set()
        assert release.wait(10)
        return rule

    with ThreadPoolExecutor(max_workers=1) as pool:
        retry = pool.submit(sync_forecast_sop, sales_pg, source, compiler=slow)
        try:
            assert started.wait(10)
            observed = effective_read(sales_pg)
            assert (observed["revision"], observed["rule"], observed["content"]) == (5, None, None)
            assert sync(sales_pg, Source(5))["state"] == "pending"
            with sales_pg.connect() as c:
                assert c.scalar(text("SELECT count(*) FROM forecast_sop_versions")) == 1
        finally:
            release.set()
        assert retry.result(timeout=10)["rule"] == rule
    assert effective_read(sales_pg)["rule"] == rule
    assert source.downloads == [4, 5, 5]


def test_published_rule_and_source_are_one_revision_and_revocation_removes_api_access(sales_pg):
    assert api_read(sales_pg)["state"] == "pending"
    source = Source()
    first = sync(sales_pg, source)
    assert (first["state"], first["revision"], first["content"], first["rule"]) == (
        "active",
        4,
        "第一版 | 近 3 日 |",
        {"formula": "近 3 日", "interpretation": MODEL},
    )
    assert api_read(sales_pg) == first
    assert sync(sales_pg, source)["synced_at"] == first["synced_at"]
    assert source.downloads == [4]
    source.current, source.body = 5, "修订 | 近 7 日 |"
    assert sync(sales_pg, source)["rule"] == {"formula": "近 7 日", "interpretation": MODEL}
    from app.connectors.forecast_sop import ForecastSourceError

    source.current = ForecastSourceError("forbidden")
    revoked = sync(sales_pg, source)
    assert (revoked["state"], revoked["reason"], revoked["rule"], revoked["content"]) == (
        "unavailable",
        "forbidden",
        None,
        None,
    )
    source.current = ForecastSourceError("temporary")
    assert sync(sales_pg, source)["state"] == "unavailable"
    with sales_pg.begin() as c:
        assert c.scalar(text("SELECT count(*) FROM forecast_sop_versions")) == 2
        assert not c.scalar(
            text("SELECT has_table_privilege('pdd_app','forecast_sop_versions','SELECT')")
        )
        assert not c.scalar(
            text("SELECT has_table_privilege('pdd_ingest','forecast_sop_versions','UPDATE')")
        )
    source.current = 5
    assert sync(sales_pg, source)["state"] == "active"
    source.current = ForecastSourceError("deleted")
    assert sync(sales_pg, source)["reason"] == "deleted"


def test_isolated_copy_preserves_actual_document_title_and_cannot_replace_real_registration(
    sales_pg,
):
    from app.connectors.forecast_sop import DOC_URL, FeishuForecastSopReader

    source = Source(content="<title>验收 &amp; 预测</title>\n\n正文 | 窗口 |")
    copy_id = "TmpCopyForForecastSopVerification"
    source.document_id = FeishuForecastSopReader.for_isolated_copy(copy_id).document_id
    record = sync(sales_pg, source)
    assert (record["title"], record["document_id"], record["source_url"]) == (
        "验收 & 预测",
        copy_id,
        DOC_URL + copy_id,
    )
    with pytest.raises(ValueError, match="FORECAST_SOP_SOURCE_MISMATCH"):
        sync(sales_pg, Source())
    outside = sa.create_engine("postgresql+psycopg://pdd_ingest@127.0.0.1:1/unapproved")
    try:
        with pytest.raises(ValueError, match="RETENTION_PRODUCTION_DISABLED"):
            sync(outside, source)
    finally:
        outside.dispose()


def test_invalid_new_rule_keeps_its_original_and_never_runs_old_formula(sales_pg):
    source = Source()
    sync(sales_pg, source)
    source.current, source.body = 6, "不能执行。最新正文与表格 | 变量 |"
    record = sync(sales_pg, source)
    assert (record["revision"], record["state"], record["content"], record["rule"]) == (
        6,
        "unsupported",
        source.body,
        None,
    )
    source.current = 4
    assert sync(sales_pg, source)["revision"] == 6
    assert api_read(sales_pg)["rule"] is None
    source.current, source.body = 7, "已修正 | 近 10 日 |"
    assert sync(sales_pg, source)["rule"] == {"formula": "近 10 日", "interpretation": MODEL}


def test_assistant_cites_current_sections_and_withholds_a_rewritten_forecast(sales_pg, monkeypatch):
    from app.modules.assistant.forecast_reports import read
    from app.modules.knowledge import forecast_access
    from app.modules.knowledge.forecast_rules import compile_forecast_rule
    from app.modules.knowledge.forecast_store import sync_forecast_sop
    from tests.test_knowledge_forecast_rules import FORMULA, SOP

    heading = "4. 推演剩余销售，再对照目标"
    document = SOP.replace("4. 月末预测", heading)
    definition = "加权日均 = 近 2 日销售日均 × 60% + 近 4 日销售日均 × 40%"
    amended = document.replace(
        FORMULA, "月末中心估计 = 累计 GMV + 剩余天数 × 加权日均。" + definition
    )
    source = Source(content=amended)

    def publish():
        return sync_forecast_sop(
            sales_pg,
            source,
            compiler=lambda body: {
                **compile_forecast_rule(body),
                "interpretation": MODEL,
            },
        )

    monkeypatch.setattr(forecast_access, "ensure_forecast_current", lambda _: publish())
    assert publish()["state"] == "active"

    def knowledge(question):
        return read(sales_pg, "business_knowledge", {"keyword": question})

    method = knowledge("月末预测的权重是多少？")
    assert definition in method["answer"] and "[查看预测 SOP]" in method["answer"]
    assert method["versions"]["forecast_sop"] == "4" and "第 4 版" not in method["answer"]
    assert method["notes"] == [f"原文章节 · {heading}"]
    assert knowledge("月末预测的情景区间如何划分？")["notes"] == [f"原文章节 · {heading}"]
    catalogue = knowledge("有哪些SOP？")
    assert catalogue["forecast_status"] == "method" and not catalogue["notes"]
    assert "修订号是 4" in knowledge("预测 SOP 用的是哪一版？")["answer"]

    source.current, source.body = 5, amended.replace(definition, definition + " × 0.95")
    assert publish()["state"] == "unsupported"
    paused = read(sales_pg, "sales_forecast", {"month": "2026-11"})
    assert paused["versions"]["forecast_sop"] == "5"
    assert paused["forecast_status"] == "unavailable" and not paused["facts"]
    assert "[查看预测 SOP]" in paused["answer"]
    original = knowledge("月末预测的计算方法是什么？")
    assert "0.95" in original["answer"] and "暂停" in original["answer"]

    source.current, source.body = 6, "# 渠道销量新模型\n新增库存修正系数。"
    assert publish()["state"] == "unsupported"
    unknown = knowledge("月末预测的计算方法是什么？")
    assert unknown["forecast_status"] == "unavailable" and not unknown["notes"]
    assert "[查看预测 SOP]" in unknown["answer"]

    source.current, source.body = 7, document
    assert publish()["state"] == "active"
    restored = knowledge("预测权重是多少？")
    assert restored["versions"]["forecast_sop"] == "7" and FORMULA in restored["answer"]


def test_probe_failure_uses_only_recently_verified_rule_even_after_worker_stops(sales_pg):
    from app.connectors.forecast_sop import ForecastSourceError

    source = Source()
    active = sync(sales_pg, source)
    source.current = ForecastSourceError("temporary")
    record = sync(sales_pg, source)
    assert record["state"] == "stale" and record["verified_at"] == active["verified_at"]
    assert record["rule"] == active["rule"]
    with sales_pg.begin() as c:
        c.exec_driver_sql("UPDATE forecast_sop_source SET verified_at=now()-interval '25 hours'")
    expired = api_read(sales_pg)
    assert (expired["state"], expired["rule"], expired["content"]) == ("expired", None, None)
    source.current = 4
    assert sync(sales_pg, source)["state"] == "active"
    assert source.downloads == [4]


def test_published_rule_withdrawn_before_download_and_racing_source_revision_cannot_publish(
    sales_pg,
):
    started, release = Event(), Event()
    sync(sales_pg, Source())

    class Editing(Source):
        def markdown(self, revision):
            started.set()
            assert release.wait(10)
            self.current = 8
            return "下载途中修改 | 近 5 日 |"

    source = Editing(7)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(sync, sales_pg, source)
        try:
            assert started.wait(10)
            observed = api_read(sales_pg)
            assert (observed["state"], observed["revision"], observed["rule"]) == (
                "pending",
                7,
                None,
            )
            concurrent = Source(9)
            assert sync(sales_pg, concurrent)["state"] == "pending"
            assert not concurrent.downloads
        finally:
            release.set()
        worker.result(timeout=10)
    record = api_read(sales_pg)
    assert (record["revision"], record["state"], record["rule"]) == (9, "pending", None)
    with sales_pg.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM forecast_sop_versions")) == 1


def test_monthly_comparison_binds_prior_run_and_rejects_future_cutoff(sales_pg):
    from datetime import date

    from app.modules.knowledge.forecast_store import read_previous_forecast, save_forecast_snapshot

    month = date(2026, 11, 1)
    sync(sales_pg, Source())
    previous = {"cutoff": "2026-11-12", "rule_revision": 4, "center": "50.00"}
    current = {"cutoff": "2026-11-13", "rule_revision": 4, "center": "47.00"}
    with sales_pg.begin() as c:
        c.exec_driver_sql("SET LOCAL ROLE pdd_ingest")
        assert read_previous_forecast(c, month, "a") is None
        assert save_forecast_snapshot(c, month, "a", previous)
        assert save_forecast_snapshot(c, month, "b", current)
        assert save_forecast_snapshot(c, month, "c", dict(current, cutoff="2026-11-14"))
        assert not save_forecast_snapshot(c, month, "b", current)
        assert save_forecast_snapshot(c, month, "backfill", dict(previous, cutoff="2026-11-10"))
        assert (
            read_previous_forecast(c, month, "next", cutoff=date(2026, 11, 14))["input_version"]
            == "backfill"
        )
        assert save_forecast_snapshot(c, month, "next", dict(current, cutoff="2026-11-14"))
    with sales_pg.begin() as c:
        c.exec_driver_sql("SET LOCAL ROLE pdd_app")
        assert read_previous_forecast(c, month, "a") is None
        assert read_previous_forecast(c, month, "b") == dict(previous, input_version="a")
        assert read_previous_forecast(c, month, "backfill") is None
        assert read_previous_forecast(c, month, "next")["input_version"] == "backfill"
        assert read_previous_forecast(c, date(2026, 12, 1), "new") is None
        assert not c.scalar(
            text("SELECT has_table_privilege('pdd_app','forecast_monthly_snapshots','INSERT')")
        )


def test_head_contains_knowledge_objects_and_rejects_nonempty_downgrade(sales_pg):
    from alembic import command
    from alembic.config import Config

    from migrations_v2.local import ROOT

    with sales_pg.connect() as c:
        assert c.scalar(text("SELECT to_regclass('public.read_forecast_sop')")) is not None
    install_legacy_revision(sales_pg, Source())
    with sales_pg.begin() as c:
        config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
        config.attributes.update(connection=c, v2_expected_database=c.engine.url.database)
        with pytest.raises(sa.exc.DBAPIError, match="FORECAST_SOP_DATA_PRESENT"), c.begin_nested():
            command.downgrade(config, "20261118_20")
    assert api_read(sales_pg)["revision"] == 4


def test_revision_23_preserves_legacy_rows_acl_and_blocks_unsafe_downgrade(sales_pg):
    from alembic import command
    from alembic.config import Config

    from app.modules.knowledge.forecast_store import sync_forecast_sop
    from migrations_v2.local import ROOT

    source = Source()
    old = install_legacy_revision(sales_pg, source)
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    with sales_pg.begin() as c:
        assert c.scalar(text("SELECT version_num FROM public.alembic_version_v2")) == "20261128_23"

        config.attributes.update(connection=c, v2_expected_database=sales_pg.url.database)
        command.downgrade(config, "20261127_22")
        before = c.execute(
            text("SELECT content,rule,synced_at FROM public.read_forecast_sop")
        ).one()
        assert before == (source.body, old["rule"], old["synced_at"])
        command.upgrade(config, "20261128_23")
        assert (
            c.execute(text("SELECT content,rule,synced_at FROM public.read_forecast_sop")).one()
            == before
        )
        assert c.scalar(text("SELECT has_table_privilege('pdd_app','read_forecast_sop','SELECT')"))
        assert not c.scalar(
            text("SELECT has_table_privilege('pdd_app','forecast_sop_versions','SELECT')")
        )
    source.current = 5
    checked = {"interpretation": {"schema": 1, "method": "deepseek", "model": "测试模型"}}
    assert sync_forecast_sop(sales_pg, source, compiler=lambda _: checked)["revision"] == 5
    with sales_pg.begin() as c:
        config.attributes["connection"] = c
        with (
            pytest.raises(sa.exc.DBAPIError, match="FORECAST_INTERPRETATION_PRESENT"),
            c.begin_nested(),
        ):
            command.downgrade(config, "20261127_22")
        assert c.scalar(text("SELECT version_num FROM public.alembic_version_v2")) == "20261128_23"


def test_worker_observing_a_new_revision_on_22_withdraws_old_rule_until_upgrade(sales_pg):
    from alembic import command
    from alembic.config import Config

    from app.modules.knowledge.forecast_store import sync_forecast_sop
    from migrations_v2.local import ROOT

    source = Source()
    install_legacy_revision(sales_pg, source)
    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    with sales_pg.begin() as c:
        config.attributes.update(connection=c, v2_expected_database=sales_pg.url.database)
        command.downgrade(config, "20261127_22")
    source.current, source.body = 5, "新版 | 近 7 日 |"
    held = sync_forecast_sop(sales_pg, source, compiler=compile_rule)
    assert (held["state"], held["revision"], held["content"], held["rule"]) == (
        "pending",
        5,
        None,
        None,
    )
    assert not source.downloads
    with sales_pg.begin() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "20261128_23")
    assert sync(sales_pg, source)["rule"]["formula"] == "近 7 日"


def test_roundtrip_keeps_restricted_view_column_access(sales_pg):
    from alembic import command
    from alembic.config import Config

    from migrations_v2.local import ROOT

    config = Config(str(ROOT / "apps/api/alembic-v2.ini"))
    with sales_pg.begin() as c:
        config.attributes.update(connection=c, v2_expected_database=sales_pg.url.database)
        c.exec_driver_sql("REVOKE SELECT ON public.read_forecast_sop FROM pdd_app")
        c.exec_driver_sql("GRANT SELECT(revision) ON public.read_forecast_sop TO pdd_app")
        for migrate, target in [
            (command.downgrade, "20261127_22"),
            (command.upgrade, "20261128_23"),
        ]:
            migrate(config, target)
            get_access = text(
                "SELECT has_column_privilege('pdd_app','read_forecast_sop',:col,'SELECT')"
            )
            assert c.scalar(get_access, {"col": "revision"})
            assert not c.scalar(get_access, {"col": "content"})


def test_back_to_back_checks_immediately_observe_an_edited_revision(sales_pg):
    source = Source()
    first = sync(sales_pg, source)
    source.current = 5
    assert first["revision"] == 4 and sync(sales_pg, source)["revision"] == 5


def test_compiler_fault_and_known_new_read_failure_leave_affected_formula_disabled(sales_pg):
    from app.connectors.forecast_sop import ForecastSourceError
    from app.modules.knowledge.forecast_store import sync_forecast_sop

    source = Source()
    sync(sales_pg, source)
    source.current = 7

    def broken(content):
        raise RuntimeError("upstream raw text that must not appear in the status")

    record = sync_forecast_sop(sales_pg, source, compiler=broken)
    assert (record["revision"], record["state"], record["reason"], record["rule"]) == (
        7,
        "pending",
        "compiler_failed",
        None,
    )

    class Failed(Source):
        def markdown(self, revision):
            raise ForecastSourceError("temporary")

    record = sync(sales_pg, Failed(8))
    assert (record["revision"], record["state"], record["rule"]) == (8, "pending", None)


def test_local_forecast_migrations_preserve_the_three_archived_facts_and_company_target(sales_pg):
    from alembic import command
    from alembic.config import Config

    from app.modules.database_retention import validate_schema
    from migrations_v2.local import ROOT
    from tests.test_deployment_database import load_script

    initializer = load_script("forecast_company_check", "init-deployment-db.py")
    with sales_pg.begin() as c:
        cfg = Config(str(ROOT / "apps/api/alembic-v2.ini"))
        cfg.attributes.update(connection=c, v2_expected_database=c.engine.url.database)
        command.downgrade(cfg, "20261118_20")
        archive_schema = validate_schema(c)
        for rev in ("20261127_21", "20261127_22", "20261128_23"):
            command.upgrade(cfg, rev)
            assert validate_schema(c) == archive_schema
            with pytest.raises(ValueError, match="DEPLOY_REVISION_MISMATCH"):
                initializer.initialize(
                    c.connection.driver_connection,
                    database=sales_pg.url.database,
                    account="pdd_admin",
                )


def test_writer_with_old_repeatable_read_cannot_lose_intervening_month_snapshot(sales_pg):
    from datetime import date

    from app.modules.knowledge.forecast_store import read_previous_forecast, save_forecast_snapshot

    sync(sales_pg, Source())
    month = date(2026, 11, 1)
    payload = {"cutoff": "2026-11-12", "rule_revision": 4, "center": "50.00"}
    with (
        sales_pg.connect().execution_options(isolation_level="REPEATABLE READ") as old,
        old.begin(),
    ):
        assert read_previous_forecast(old, month, "b") is None
        with sales_pg.begin() as fresh:
            assert save_forecast_snapshot(fresh, month, "a", payload)
        with pytest.raises(sa.exc.OperationalError) as caught, old.begin_nested():
            save_forecast_snapshot(old, month, "b", payload)
        assert caught.value.orig.sqlstate == "40001"
    with sales_pg.begin() as fresh:
        assert save_forecast_snapshot(fresh, month, "b", payload)
        assert read_previous_forecast(fresh, month, "b")["input_version"] == "a"


def test_read_committed_lookup_keeps_the_same_prior_when_runs_commit_during_read(sales_pg):
    from datetime import date

    from app.modules.knowledge.forecast_store import read_previous_forecast, save_forecast_snapshot

    sync(sales_pg, Source())
    month = date(2026, 11, 1)
    payload = {"cutoff": "2026-11-12", "rule_revision": 4, "center": "50.00"}
    with sales_pg.begin() as c:
        assert save_forecast_snapshot(c, month, "a", payload)
    with sales_pg.connect() as reader:

        @sa.event.listens_for(reader, "after_cursor_execute", once=True)
        def two_more_runs(*_):
            with sales_pg.begin() as writer:
                assert save_forecast_snapshot(writer, month, "b", payload)
                assert save_forecast_snapshot(writer, month, "c", payload)

        assert read_previous_forecast(reader, month, "b")["input_version"] == "a"


def test_same_verified_text_can_be_reinterpreted_after_a_program_upgrade(sales_pg):
    from datetime import date

    from app.modules.knowledge.forecast_store import save_forecast_snapshot
    from app.modules.knowledge.interpretation import effective_forecast_sop
    from tests.test_knowledge_forecast_rules import SOP

    source = Source()
    source.body = SOP

    stored = install_legacy_revision(sales_pg, source)
    assert stored["state"] == "unsupported"
    with sales_pg.begin() as c:
        current = effective_forecast_sop(c)
        assert current["state"] == "active" and current["revision"] == stored["revision"]
        assert current["rule"]["terms"] == [
            {"days": 2, "weight": "0.75"},
            {"days": 4, "weight": "0.25"},
        ]
        assert save_forecast_snapshot(
            c,
            date(2026, 11, 1),
            "upgraded-method",
            {"cutoff": "2026-11-10", "rule_revision": current["revision"]},
            reinterpreted=current["reinterpreted"],
        )
    assert api_read(sales_pg)["rule"] is None  # 首次解析与当时的证据仍可审计。
