"""真实的助手选文档入口。文档正文与调用轨迹只在选中该来源后出现。"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.usage import RunUsage

from app.modules.assistant import catalog, dashboard
from app.modules.assistant.chat import ChatScope, QueryRun, answer_question, get_dashboard_data


def test_catalog_question_lists_titles_without_a_business_or_feishu_query(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("只列目录就读了经营库或飞书")

    monkeypatch.setattr(catalog, "read", unexpected)
    q = "目前可以看哪些 SOP？"
    result = asyncio.run(answer_question(None, q, "商品数据", [], QueryRun(None, ChatScope(), q)))
    assert result["status"] == "answered"
    assert "月度 GMV 滚动预测" in result["answer"]
    assert result["reports"] == []


def test_plain_observed_gmv_does_not_open_even_a_registered_sop(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("只问已发生的销售额，不应打开文档")

    monkeypatch.setattr(catalog, "read", unexpected)
    q = "这个月已经卖了多少？"
    run = QueryRun(None, ChatScope(), q)
    result = asyncio.run(
        get_dashboard_data(
            SimpleNamespace(deps=run),
            dashboard.DashboardRequest(
                capability="business_knowledge",
                document_key="monthly_gmv",
            ),
        )
    )
    assert result["status"] == "not_applicable"
    assert run.reports == []


def test_agent_can_select_a_different_document_from_the_same_catalog(monkeypatch):
    from app.modules.knowledge import public_sops

    docs = [
        dict(
            key="returns_quality", title="品质售后排查", description="退款增加时核对签收与品质原因"
        ),
        dict(key="returns_approval", title="售后审批", description="退款申请的审批流程"),
    ]
    source_reads = []
    monkeypatch.setattr(public_sops, "catalog_entries", lambda: docs)

    def read(key, query, *, session):
        source_reads.append(key)
        assert key == "returns_approval"
        return dict(
            key=key,
            title="售后审批",
            source_url="https://example.test/sop",
            revision=8,
            state="matched",
            passages=[dict(section="主管复核", quote="异常申请先核实凭证。")],
        )

    monkeypatch.setattr(public_sops, "read_public_sop", read)
    q = "按公司的 SOP，异常退款申请该怎么审批？请附原文链接。"

    class Model:
        async def run(self, prompt, *, deps, **kwargs):
            assert (
                "returns_approval" in prompt
                and "returns_quality" in prompt
                and "先核实凭证" not in prompt
            )
            request = dashboard.DashboardRequest(
                capability="business_knowledge",
                document_key="returns_approval",
                keyword="异常退款申请审批",
            )
            evidence = await get_dashboard_data(SimpleNamespace(deps=deps), request)
            assert (
                "异常申请先核实凭证" in evidence["answer"]
                and "六店" not in evidence["source_scope"]
            )
            return SimpleNamespace(output="先核实异常退款申请的凭证。", usage=RunUsage(requests=1))

    run = QueryRun(None, ChatScope(), q)
    result = asyncio.run(answer_question(Model(), q, "推广分析", [], run))
    assert source_reads == ["returns_approval"] and result["status"] == "answered"
    assert result["reports"][0]["scope"]["document_key"] == "returns_approval"
    assert result["reports"][0]["data_version"] == "8"
    assert "https://example.test/sop" in result["answer"]


def test_unregistered_sop_returns_a_specific_gap_instead_of_an_invented_rule():
    q = "仓库夜班盘点 SOP 的签字人是谁？"

    class Model:
        async def run(self, prompt, *, deps, **kwargs):
            request = dashboard.DashboardRequest(
                capability="business_knowledge", document_key="unknown"
            )
            result = await get_dashboard_data(SimpleNamespace(deps=deps), request)
            assert result["status"] == "not_registered"
            return SimpleNamespace(output="由仓库经理签字。", usage=RunUsage(requests=1))

    result = asyncio.run(
        answer_question(Model(), q, "售后分析", [], QueryRun(None, ChatScope(), q))
    )
    assert result["status"] == "no_evidence" and "签字" not in result["answer"]
    assert "SOP" in result["answer"] and not result["reports"]


@pytest.mark.parametrize("source", ["已登记的公共 SOP", "运营手册"])
def test_explicit_document_question_cannot_be_answered_with_a_data_coverage_template(source):
    from app.modules.assistant.chat import ObservationExplanation

    class Model:
        async def run(self, prompt, **kwargs):
            return SimpleNamespace(
                output=ObservationExplanation(topic="incomplete_coverage"),
                usage=RunUsage(requests=1),
            )

    q = f"请按{source}回答直播间补光灯如何选色温，没有对应文档请说没有依据。"
    result = asyncio.run(
        answer_question(Model(), q, "运营看板中心", [], QueryRun(None, ChatScope(), q))
    )
    assert result["status"] == "no_evidence" and "SOP" in result["answer"]
    assert "有记录的日期" not in result["answer"] and not result["reports"]


def test_missing_review_usage_does_not_report_zero_tokens_or_requests():
    run = QueryRun(None, ChatScope(), "按六店预测 SOP 计算月末 GMV")
    run.record_review_usage({"requests": None, "input_tokens": None, "output_tokens": None})
    usage = run.response("本次未能完成核验。", "no_evidence")["usage"]
    assert usage["sop_review_usage_complete"] is False and usage["sop_review_requests"] is None
    assert all(key not in usage for key in ("requests", "input_tokens", "output_tokens"))


def test_first_forecast_reports_on_demand_interpretation_tokens(monkeypatch):
    from app.modules.assistant.business import report

    def read(engine, capability, params):
        data = report("sales", capability, "六店预测", "日报", "4", scope=params)
        return dict(
            data,
            answer="按当前规则估算。",
            forecast_status="forecast",
            review_usage={"requests": 1, "input_tokens": 83, "output_tokens": 21},
        )

    monkeypatch.setattr(catalog, "read", read)
    q = "预测本月六店 GMV"
    result = asyncio.run(answer_question(None, q, "商品数据", [], QueryRun(None, ChatScope(), q)))
    assert result["usage"]["requests"] == 1 and result["usage"]["input_tokens"] == 83
    assert result["usage"]["sop_review_requests"] == 1 and result["usage"]["output_tokens"] == 21
    assert "review_usage" not in result["reports"][0]


def test_asking_about_a_source_revision_does_not_compile_a_new_formula(monkeypatch):
    from app.modules.knowledge import public_sops

    def fetch(key, query, **kwargs):
        return dict(
            key=key,
            title="月度预测 SOP",
            source_url="https://example.test/revision",
            state="no_match",
            revision=17,
            passages=[],
        )

    monkeypatch.setattr(public_sops, "read_public_sop", fetch)
    q = "预测 SOP 用的是哪一版？"
    answer = asyncio.run(answer_question(None, q, "推广分析", [], QueryRun(None, ChatScope(), q)))
    assert "17" in answer["answer"] and answer["status"] == "answered"
    assert answer["reports"][0]["scope"]["document_key"] == "monthly_gmv"
