"""按真实请求核验规则；随机 PostgreSQL 分别执行查询角色和同步角色的日常路径。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Event
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from app.connectors.forecast_sop import ForecastSourceError
from app.jobs import forecast_sop
from app.modules.knowledge.forecast_store import read_forecast_sop, sync_forecast_sop
from tests.test_forecast_sop_store import MODEL, Source, compile_rule

pytest_plugins = ["tests.sales_pg_support"]
SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def roles(sales_pg):
    # 同一随机库两个独立连接池；权限由真实 PG 执行，API 的事务另设只读。
    writer = sa.create_engine(
        sales_pg.url, hide_parameters=True, connect_args={"options": "-c role=pdd_ingest"}
    )
    api = sa.create_engine(
        sales_pg.url,
        hide_parameters=True,
        connect_args={"options": "-c role=pdd_app -c default_transaction_read_only=on"},
    )
    try:
        with writer.connect() as c:
            assert c.exec_driver_sql("SELECT current_user").scalar_one() == "pdd_ingest"
        with api.connect() as c:
            assert c.exec_driver_sql("SELECT current_user").scalar_one() == "pdd_app"
            assert c.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        yield writer, api
    finally:
        writer.dispose()
        api.dispose()


class CountingSource(Source):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.probes = 0

    def revision(self):
        self.probes += 1
        return super().revision()


def ask(api, writer, source, **kwargs):
    from app.modules.knowledge.forecast_access import ensure_forecast_current

    return ensure_forecast_current(
        api,
        refresh=lambda: forecast_sop.run_check(
            writer, source, compiler=kwargs.pop("compiler", compile_rule), **kwargs
        ),
    )


def test_busy_before_writer_withdraws_old_version_probes_and_withholds_old_formula(roles):
    writer, _ = roles
    source = CountingSource()
    sync_forecast_sop(writer, source, compiler=compile_rule)
    source.current, source.body = 5, "新稿 | 近 2 日 |"
    probes = source.probes
    with writer.begin() as competing:
        competing.exec_driver_sql("SELECT pg_advisory_xact_lock(726411,20261127)")
        state = sync_forecast_sop(writer, source, compiler=compile_rule)
        assert (state["state"], state["revision"], state["rule"], state["content"]) == (
            "pending",
            5,
            None,
            None,
        )
        assert source.probes == probes + 1 and source.downloads == [4]
        source.current = 3
        older = sync_forecast_sop(writer, source, compiler=compile_rule)
        assert older["revision"] == 5 and older["rule"] is None


def test_every_question_probes_and_only_a_new_stable_draft_needs_interpretation(roles):
    writer, api = roles
    source = CountingSource()
    compiled = []

    def compiler(body):
        compiled.append(body)
        return compile_rule(body)

    first = ask(api, writer, source, compiler=compiler)
    assert first["state"] == "active" and first["revision"] == 4
    unchanged = ask(api, writer, source, compiler=compiler)
    assert unchanged["rule"] == first["rule"] and unchanged["synced_at"] == first["synced_at"]
    assert source.probes == 3 and source.downloads == [4]
    source.current, source.body = 5, "新稿 | 近 2 日 |"
    with api.connect().execution_options(isolation_level="REPEATABLE READ") as old:
        assert read_forecast_sop(old)["revision"] == 4
        updated = ask(api, writer, source, compiler=compiler)
        assert read_forecast_sop(old)["revision"] == 4
    assert (updated["revision"], updated["rule"]["formula"]) == (5, "近 2 日")
    assert source.probes == 5 and source.downloads == [4, 5] and len(compiled) == 2


def test_same_new_draft_is_interpreted_once_when_questions_overlap(roles):
    writer, api = roles
    source = CountingSource()
    ask(api, writer, source)
    source.current, source.body = 5, "新稿 | 近 2 日 |"
    started, release = Event(), Event()
    compiled = []

    def slow(content):
        compiled.append(content)
        started.set()
        assert release.wait(10)
        return compile_rule(content)

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(ask, api, writer, source, compiler=slow)
        try:
            assert started.wait(10)
            second = ask(api, writer, source, compiler=slow)
            assert (second["revision"], second["state"], second["rule"]) == (5, "pending", None)
        finally:
            release.set()
        assert first.result(timeout=10)["rule"]["formula"] == "近 2 日"
    assert compiled == [source.body] and source.downloads == [4, 5] and source.probes == 5


def test_failed_probe_model_or_concurrent_edit_never_leaks_old_rule_or_citations(roles):
    writer, api = roles
    source = CountingSource()
    assert ask(api, writer, source)["state"] == "active"
    for reason in ("temporary", "forbidden", "deleted"):
        source.current = ForecastSourceError(reason)
        record = ask(api, writer, source)
        assert record["state"] in ("pending", "unavailable")
        assert (record["rule"], record["content"], record["issues"]) == (None, None, [])
    source.current, source.body = 6, "改稿 | 权重 |"

    def timeout(_):
        raise TimeoutError("不会暴露的供应商错误正文")

    record = ask(api, writer, source, compiler=timeout)
    assert (record["state"], record["revision"], record["rule"]) == ("pending", 6, None)

    def edit_during_interpretation(content):
        source.current = 7
        return compile_rule(content)

    record = ask(api, writer, source, compiler=edit_during_interpretation)
    assert (record["state"], record["revision"], record["rule"]) == ("pending", 7, None)
    assert ask(api, writer, source)["revision"] == 7
    with writer.connect() as c:
        assert list(
            c.exec_driver_sql("SELECT revision FROM forecast_sop_versions ORDER BY 1").scalars()
        ) == [4, 7]


def test_receipt_does_not_authorize_a_different_version_or_a_revoked_source(roles):
    from app.modules.knowledge.forecast_access import ensure_forecast_current

    writer, api = roles
    source = CountingSource()
    receipt = forecast_sop.run_check(writer, source, compiler=compile_rule)
    source.current, source.body = 5, "新稿 | 权重 |"
    assert ask(api, writer, source)["revision"] == 5
    masked = ensure_forecast_current(api, refresh=lambda: receipt)
    assert masked["rule"] is None and masked["content"] is None and masked["state"] == "pending"
    valid = forecast_sop.run_check(writer, source, compiler=compile_rule)
    source.current = ForecastSourceError("forbidden")
    ask(api, writer, source)
    assert ensure_forecast_current(api, refresh=lambda: valid)["content"] is None

    def failed_refresh():
        raise OSError("不能展示给前端的私密错误")

    failed = ensure_forecast_current(api, refresh=failed_refresh)
    assert failed["rule"] is None and failed["content"] is None and "私密" not in str(failed)
    malformed = ensure_forecast_current(api, refresh=lambda: dict(receipt, review_usage=None))
    assert malformed["rule"] is None and malformed["review_usage"]["requests"] is None


def test_idle_snapshot_only_uses_today_question_receipt_and_committed_local_rule(
    roles, tmp_path, monkeypatch
):
    from app.connectors.sales_feishu import FeishuRefundReader
    from app.modules.knowledge.forecast_rules import compile_forecast_rule
    from app.modules.sales.contracts import ReadWindow
    from app.modules.sales.datasets import REFUND_BASE, REFUND_TABLE, SPECS
    from app.modules.sales.source_policy import SHOPS
    from app.modules.sales.storage import ShopDailyStore
    from app.modules.sales.targets import SalesTargetStore
    from tests.test_forecast_inputs import source_days
    from tests.test_knowledge_forecast_rules import SOP
    from tests.test_sales_targets import candidate as target_candidate
    from tests.test_sales_targets import rows as target_rows

    writer, _ = roles
    marker = tmp_path / "requested.json"
    source = CountingSource(content=SOP)
    compiler = lambda value: {**compile_forecast_rule(value), "interpretation": MODEL}  # noqa: E731
    receipt = forecast_sop.run_check(writer, source, compiler=compiler)
    now = datetime.fromisoformat(receipt["checked_at"]).astimezone(SHANGHAI)
    after = datetime.combine(now.date(), time(23, 59, 59), SHANGHAI)
    prior = (after.date().replace(day=1) - timedelta(days=1)).replace(day=1)
    window = ReadWindow(prior, prior + timedelta(days=6), tuple(SHOPS))
    rows = source_days(7)
    for row in rows:
        row["日期"] = prior.strftime("%Y-%m") + row["日期"][7:]

    def page(*_):
        return rows, {
            "base_token": REFUND_BASE,
            "table_id": REFUND_TABLE,
            "rev": 123,
            "timezone": "Asia/Shanghai",
            "records_count": len(rows),
            "has_more": False,
        }

    f01 = FeishuRefundReader.for_test(page, include_gmv=True).read_stable(window)
    ShopDailyStore(
        writer, archive_root=tmp_path / "data", approved=window, spec=SPECS["shop_refunds"]
    ).publish_candidate(f01)
    targets = SalesTargetStore(writer, archive_root=tmp_path / "targets")
    targets.run(lambda _: target_candidate())
    run_daily = forecast_sop.save_requested_snapshots

    # 运维曾手工核对过，仍未出现任何提问；旧的 enable 标记也不能代表当天提问。
    marker.touch()
    assert run_daily(writer, marker, now=after)["saved"] == 0
    requested = forecast_sop.run_check(writer, source, compiler=compiler, request_marker=marker)
    for later in (after.replace(hour=8), after + timedelta(days=1)):
        assert run_daily(writer, marker, now=later)["saved"] == 0
    assert run_daily(writer, marker, now=after)["saved"] == 1
    with writer.connect() as c:
        stored = c.exec_driver_sql("SELECT payload FROM forecast_monthly_snapshots").scalar_one()
        assert stored["rule_revision"] == 4
        assert datetime.fromisoformat(stored["sop_verified_at"]) == datetime.fromisoformat(
            requested["checked_at"]
        )

    def no_calculation(*_):
        pytest.fail("相同日、修订和输入不能重复计算")

    with monkeypatch.context() as same:
        same.setattr(forecast_sop, "record_month", no_calculation)
        assert run_daily(writer, marker, now=after)["saved"] == 0
    # 目标冲突会改变有效目标，即使发布指针仍指向原版，也需重新核实输入。
    from app.modules.sales.contracts import SalesError

    rows = target_rows()
    with pytest.raises(SalesError, match="TARGET_MONTH_CONFLICT"):
        targets.run(lambda _: target_candidate([*rows, dict(rows[1], record_id="duplicate")]))
    assert run_daily(writer, marker, now=after)["status"] == "checked"
    assert source.probes == 3 and source.downloads == [4]


def test_revoked_or_superseded_question_cannot_enable_background_snapshot(roles, tmp_path):
    writer, _ = roles
    marker = tmp_path / "requested.json"
    source = CountingSource()
    accepted = forecast_sop.run_check(writer, source, compiler=compile_rule, request_marker=marker)
    today = datetime.fromisoformat(accepted["checked_at"]).astimezone(SHANGHAI).date()
    after = datetime.combine(today, time(23, 59, 59), SHANGHAI)
    source.current = ForecastSourceError("forbidden")
    forecast_sop.run_check(writer, source, compiler=compile_rule)
    assert forecast_sop.save_requested_snapshots(writer, marker, now=after)["saved"] == 0
    source.current = 7
    forecast_sop.run_check(writer, source, compiler=compile_rule)
    assert forecast_sop.save_requested_snapshots(writer, marker, now=after)["saved"] == 0
    with writer.connect() as c:
        assert (
            c.exec_driver_sql("SELECT count(*) FROM forecast_monthly_snapshots").scalar_one() == 0
        )


@pytest.mark.parametrize("with_request_file", [False, True])
def test_project_idle_tick_ignores_legacy_switch_and_unapproved_database(
    tmp_path, monkeypatch, capsys, with_request_file
):
    from app.modules import archive_compaction, region_retention_runtime
    from app.modules.aftersales import statistics
    from tests.test_project_sync_runtime import cli_main

    (tmp_path / "config").mkdir()
    (tmp_path / "config/sync.toml").write_text('times=["10:00"]\n')
    (tmp_path / "var/config").mkdir(parents=True)
    (tmp_path / "var/config/forecast-sop.enabled").touch()
    if with_request_file:
        marker = tmp_path / "var/project-sync/forecast-sop-request.json"
        marker.parent.mkdir()
        marker.write_text('{"revision":1}')
        monkeypatch.setattr(
            forecast_sop,
            "save_requested_snapshots",
            lambda *_: pytest.fail("本机快照凭据不能驱动其他数据库"),
        )
    scheduled = cli_main(monkeypatch, tmp_path, ["--scheduled"])
    business = []
    engine = sa.create_engine("postgresql+psycopg://unused")
    monkeypatch.setitem(scheduled.__globals__, "runtime_engine", lambda: engine)
    monkeypatch.setitem(scheduled.__globals__, "due_slot", lambda *_: "example-slot")

    def execute(engine, slot, **kwargs):
        business.append(slot)
        return {"status": "succeeded"}

    monkeypatch.setitem(scheduled.__globals__, "execute_project", execute)
    monkeypatch.setattr(statistics, "maintain_statistics", lambda *a, **k: {"status": "disabled"})
    monkeypatch.setattr(archive_compaction, "compact_archives", lambda *a, **k: {})
    monkeypatch.setattr(
        region_retention_runtime, "maintain_regions", lambda *a, **k: {"status": "disabled"}
    )
    from app.connectors.forecast_sop import FeishuForecastSopReader

    monkeypatch.setattr(
        FeishuForecastSopReader, "revision", lambda *_: pytest.fail("空闲时不能检查飞书预测文档")
    )
    assert scheduled() == 0
    assert business == ["example-slot"]
    assert '"stage": "forecast_sop"' not in capsys.readouterr().out


@pytest.mark.parametrize("competing_observation", [6, "forbidden"])
def test_overlapping_newer_revision_or_revocation_prevents_an_older_inflight_publication(
    roles, competing_observation
):
    writer, api = roles
    ask(api, writer, CountingSource())
    started, release = Event(), Event()
    draft = CountingSource(revision=5, content="新稿 | 权重 |")

    def slow(content):
        started.set()
        assert release.wait(10)
        return compile_rule(content)

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(ask, api, writer, draft, compiler=slow)
        try:
            assert started.wait(10)
            other = CountingSource()
            other.current = (
                ForecastSourceError("forbidden") if competing_observation == "forbidden" else 6
            )
            assert ask(api, writer, other)["content"] is None
        finally:
            release.set()
        withheld = first.result(timeout=10)
    assert withheld["state"] in ("unavailable", "pending") and withheld["rule"] is None
    with writer.connect() as c:
        assert list(
            c.exec_driver_sql("SELECT revision FROM forecast_sop_versions ORDER BY 1").scalars()
        ) == [4]


@pytest.mark.parametrize(
    "url,env",
    [
        ("postgresql+psycopg://pdd_app@remote:55432/pdd_operations", "development"),
        ("postgresql+psycopg://pdd_app@127.0.0.1:55432/another", "development"),
        ("postgresql+psycopg://pdd_admin@127.0.0.1:55432/pdd_operations", "development"),
        ("postgresql+psycopg://pdd_app@127.0.0.1:55432/pdd_operations?host=remote", "development"),
        ("postgresql+psycopg://pdd_app@127.0.0.1:55432/pdd_operations", "production"),
    ],
)
def test_default_access_never_launches_a_local_writer_for_a_different_database(
    monkeypatch, url, env
):
    from app.modules.knowledge import forecast_access

    engine = sa.create_engine(url)
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setattr(
        forecast_access, "_request_refresh", lambda: pytest.fail("非本机只读 API 不得驱动同步账号")
    )
    try:
        source = forecast_access.ensure_forecast_current(engine)
        assert source["state"] == "unavailable" and source["review_usage"]["requests"] == 0
    finally:
        engine.dispose()


def test_the_first_question_still_compiles_if_another_probe_sees_the_same_new_draft_first(roles):
    writer, api = roles
    ask(api, writer, CountingSource())
    started, release = Event(), Event()

    class SlowMetadata(CountingSource):
        def revision(self):
            if not self.probes:
                started.set()
                assert release.wait(10)
            return super().revision()

    question = SlowMetadata(revision=5, content="新稿 | 本月 |")
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(ask, api, writer, question)
        try:
            assert started.wait(10)
            waiting = ask(api, writer, CountingSource(revision=5))
            assert (waiting["state"], waiting["rule"]) == ("pending", None)
        finally:
            release.set()
        result = worker.result(timeout=10)
    assert result["state"] == "active" and result["rule"]["formula"] == "本月"
    assert question.downloads == [5]


def test_stale_knowledge_cannot_create_a_formal_snapshot(roles):
    from datetime import date

    from app.modules.knowledge.forecast_store import save_forecast_snapshot

    writer, api = roles
    source = CountingSource()
    ask(api, writer, source)
    source.current = ForecastSourceError("temporary")
    assert ask(api, writer, source)["rule"] is None
    with writer.begin() as c, pytest.raises(ValueError, match="FORECAST_SOP_RULE_CHANGED"):
        save_forecast_snapshot(
            c,
            date(2026, 11, 1),
            "outdated",
            {
                "cutoff": "2026-11-02",
                "rule_revision": 4,
                "center": "10.00",
            },
        )


def test_unwritable_snapshot_marker_does_not_hide_a_verified_answer(roles, tmp_path):
    writer, api = roles
    parent_is_file = tmp_path / "unwritable"
    parent_is_file.touch()
    record = ask(api, writer, CountingSource(), request_marker=parent_is_file / "request.json")
    assert record["state"] == "active" and record["rule"]["formula"] == "近 3 日"


def test_manual_command_exposes_only_fixed_failure_reason(monkeypatch, capsys):
    import sys

    def failed_configuration():
        raise ValueError("private-configuration-value")

    monkeypatch.setattr(sys, "argv", ["forecast_sop"])
    monkeypatch.setattr(forecast_sop, "runtime_engine", failed_configuration)
    assert forecast_sop.main() == 1
    captured = capsys.readouterr()
    assert (
        "failed" in captured.out
        and "private-configuration-value" not in captured.out + captured.err
    )
