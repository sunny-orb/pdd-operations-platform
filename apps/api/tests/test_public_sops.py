"""公共 SOP 仅按登记键查当次修订，章节与引文可在该版原文定位。"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.connectors.forecast_sop import ForecastSourceError
from app.modules.knowledge import public_sops
from app.modules.knowledge.public_sops import PublicSopEntry, PublicSopLibrary

RECEIPT = PublicSopEntry(
    "receipt", "ReceiptProcedureDocx12345", "到货交接 SOP", "交接时查验铅封、签收异常的操作方法。"
)
RETURN = PublicSopEntry(
    "returns", "ReturnsProcedureDocx12345", "退货交接 SOP", "退货交接时复核批号和包装。"
)


class Document:
    def __init__(self, content):
        self.content = content
        self.version = 1
        self.checks = 0
        self.downloads = []
        self.check_error = self.body_error = None

    def revision(self):
        self.checks += 1
        if self.check_error:
            raise ForecastSourceError(self.check_error)
        return self.version

    def markdown(self, revision):
        self.downloads.append(revision)
        if self.body_error:
            raise ForecastSourceError(self.body_error)
        assert revision == self.version
        return self.content


def test_default_catalog_exposes_only_registered_short_descriptions(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")

    def unexpected(*args, **kwargs):
        raise AssertionError("读取短目录不需要请求飞书")

    monkeypatch.setattr("subprocess.run", unexpected)
    (entry,) = public_sops.catalog_entries()
    assert set(entry) == {"key", "title", "description"}
    assert entry["key"] == "monthly_gmv"
    assert entry["title"] == "拼多多月度 GMV 滚动预测 SOP"
    assert "投放" in entry["description"] and len(entry["description"]) < 150


def test_only_the_chosen_registered_source_is_read_even_with_similar_titles():
    documents = {
        RECEIPT: Document("## 铅封交接\n铅封断裂先拍照留存。"),
        RETURN: Document("## 铅封交接\n退货铅封由接收员当面复核。"),
    }
    library = PublicSopLibrary(documents, documents.__getitem__)
    assert [row["key"] for row in library.catalog_entries()] == ["receipt", "returns"]
    for invalid in ("unknown", RECEIPT.document_id, "https://other.example/docx/another"):
        result = library.read_public_sop(invalid, "铅封交接")
        assert result["state"] == "not_registered" and result["passages"] == []
        assert result["source_url"] is None
    result = library.read_public_sop("receipt", "铅封交接应该怎么做？")
    assert result["revision"] == 1 and result["title"] == "到货交接 SOP"
    assert result["source_url"] == "https://kocotree.feishu.cn/docx/ReceiptProcedureDocx12345"
    assert result["passages"] == [{"section": "铅封交接", "quote": "铅封断裂先拍照留存。"}]
    assert documents[RECEIPT].checks == 1 and documents[RETURN].checks == 0


def test_revision_probe_precedes_body_reuse_and_changed_revision_replaces_the_quotes():
    document = Document("铅封红色时联系运输员。")
    library = PublicSopLibrary([RECEIPT], lambda _: document)
    first = library.read_public_sop("receipt", "铅封红色")
    assert first["passages"][0]["quote"] == document.content
    assert library.read_public_sop("receipt", "联系运输员")["revision"] == 1
    assert document.checks == 2 and document.downloads == [1]

    document.version = 2
    document.content = "铅封红色时拍照提交仓管。"
    result = library.read_public_sop("receipt", "铅封红色")
    assert result["revision"] == 2 and result["passages"][0]["quote"] == document.content
    assert document.checks == 3 and document.downloads == [1, 2]


@pytest.mark.parametrize("reason", ["forbidden", "deleted", "not_found", "temporary"])
def test_failed_access_invalidates_saved_body_even_if_same_revision_reappears(reason):
    document = Document("铅封须完整。")
    library = PublicSopLibrary([RECEIPT], lambda _: document)
    assert library.read_public_sop("receipt", "铅封")["passages"]
    document.check_error = reason
    result = library.read_public_sop("receipt", "铅封")
    assert result["state"] == "unavailable" and result["reason"] == reason
    assert result["revision"] is None and result["passages"] == []

    document.check_error = None
    document.body_error = "forbidden"
    result = library.read_public_sop("receipt", "铅封")
    assert result["state"] == "unavailable" and result["passages"] == []
    assert document.downloads == [1, 1]


def test_new_revision_whose_body_cannot_be_read_never_falls_back_to_old_text():
    document = Document("铅封须完整。")
    library = PublicSopLibrary([RECEIPT], lambda _: document)
    assert library.read_public_sop("receipt", "铅封")["passages"]
    document.version = 2
    document.body_error = "temporary"
    result = library.read_public_sop("receipt", "铅封")
    assert result["state"] == "unavailable" and result["passages"] == []
    document.body_error = None
    document.content = "铅封编号由司机签字。"
    assert library.read_public_sop("receipt", "铅封")["passages"][0]["quote"] == document.content
    assert document.downloads == [1, 2, 2]


def test_concurrent_reads_check_access_for_each_request_and_download_body_only_once():
    started = Event()
    release = Event()

    class SlowDocument(Document):
        def markdown(self, revision):
            started.set()
            assert release.wait(2)
            return super().markdown(revision)

    document = SlowDocument("铅封须完整。")
    library = PublicSopLibrary([RECEIPT], lambda _: document)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(library.read_public_sop, "receipt", "铅封")
        assert started.wait(2)
        second = executor.submit(library.read_public_sop, "receipt", "铅封")
        release.set()
        assert first.result()["passages"] == second.result()["passages"]
    assert document.checks == 2 and document.downloads == [1]


def test_default_source_uses_fixed_user_and_document_and_never_runs_in_production(monkeypatch):
    calls = []
    doc_id = "H3svdLyEYoxsK3xgeBOceOxfnMc"

    def run(args, **kwargs):
        calls.append(args)
        doc = {"document_id": doc_id, "revision_id": 37}
        if args[1] == "docs":
            doc["content"] = "## 滚动预测\n比较同一期间的投放效率。"
        response = {"ok": True, "identity": "user", "data": {"document": doc}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(response), stderr="")

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setenv("APP_ENV", "production")
    assert public_sops.catalog_entries() == []
    assert public_sops.read_public_sop("monthly_gmv", "投放效率")["state"] == "unavailable"
    assert not calls

    monkeypatch.setenv("APP_ENV", "development")
    result = public_sops.read_public_sop("monthly_gmv", "投放效率")
    assert result["revision"] == 37 and result["passages"][0]["quote"] == "比较同一期间的投放效率。"
    assert calls[0][1:4] == ["api", "GET", f"/open-apis/docx/v1/documents/{doc_id}"]
    assert calls[1][calls[1].index("--doc") + 1] == doc_id
    assert calls[1][calls[1].index("--revision-id") + 1] == "37"
    assert all(args[-2:] == ["--as", "user"] for args in calls)


def test_similar_sections_are_ranked_by_the_requested_detail_and_keep_verbatim_quotes():
    content = (
        "# 到货交接 SOP\n"
        "## 正常签收\n核对单号，完成签收。\n"
        "## 异常签收\n核对单号，登记签收时间。\n\n"
        "遇到铅封断裂，应先拍箱体照片、封存运单，再请承运人签字。\n"
        "## 算例\n铅封断裂时这个历史案例选择了直接入库。"
    )
    library = PublicSopLibrary([RECEIPT], lambda _: Document(content))
    result = library.read_public_sop("receipt", "签收时遇到铅封断裂怎么做？")
    assert result["state"] == "matched"
    assert result["passages"] == [
        {"section": "异常签收", "quote": "遇到铅封断裂，应先拍箱体照片、封存运单，再请承运人签字。"}
    ]
    assert all(p["quote"] in content for p in result["passages"])


@pytest.mark.parametrize(
    "question",
    ["《到货交接 SOP》里的广告预算该怎么审批？", "退款平均处理时间怎么计算？"],
)
def test_title_and_generic_shared_words_do_not_make_unrelated_short_text_a_match(question):
    content = "## 交接处理\n需要核对单号，登记处理时间。"
    library = PublicSopLibrary([RECEIPT], lambda _: Document(content))
    result = library.read_public_sop("receipt", question)
    assert result["state"] == "no_match" and result["passages"] == []


def test_short_article_can_be_read_in_context_without_guessing_a_matching_clause():
    entry = PublicSopEntry(
        "moisture", "WetPackagesExampleDoc12345", "外包装验收", "收件时纸箱渗水或破口的处理"
    )
    content = "## 受潮\n先拍照，请承运人签字。\n\n## 开裂\n拒收并记录运单。"
    library = PublicSopLibrary([entry], lambda _: Document(content))
    for question in ("这篇的操作步骤是什么？", "收件时箱子渗水该找谁？"):
        result = library.read_public_sop("moisture", question)
        assert result["state"] == "context"
        assert [p["quote"] for p in result["passages"]] == [
            "先拍照，请承运人签字。",
            "拒收并记录运单。",
        ]


def test_long_paragraph_finds_a_late_subsection_with_bounded_verbatim_output():
    clause = "货架蓝标脱落时把批号写在交接单上。"
    content = "## 货架维护\n" + ("按顺序点验，不得遗漏。" * 1800) + clause
    library = PublicSopLibrary([RECEIPT], lambda _: Document(content))
    result = library.read_public_sop("receipt", "货架蓝标脱落该如何处理？")
    assert result["state"] == "matched"
    assert any(clause in passage["quote"] for passage in result["passages"])
    assert all(p["quote"] in content for p in result["passages"])
    assert sum(len(p["quote"]) for p in result["passages"]) <= 2400


def test_old_bodies_are_evicted_across_registered_documents():
    entries = [
        PublicSopEntry(f"item_{i}", f"RegisteredProcedureDocx{i:04d}", "签收", "收件检查")
        for i in range(16)
    ]
    documents = {entry: Document("铅封须完整。") for entry in entries}
    library = PublicSopLibrary(entries, documents.__getitem__)
    for entry in entries:
        assert library.read_public_sop(entry.key, "铅封")["passages"]
    assert library.read_public_sop("item_0", "铅封")["passages"]
    assert documents[entries[0]].downloads == [1, 1]


@pytest.mark.parametrize(
    ("key", "doc_id"),
    [
        ("receipt", RETURN.document_id),
        ("https://another.example", RETURN.document_id),
        ("other", "https://another.example/docx/foreign"),
    ],
)
def test_registration_refuses_key_collisions_and_external_addresses(key, doc_id):
    entry = PublicSopEntry(key, doc_id, "登记", "签收操作")
    with pytest.raises(ValueError):
        PublicSopLibrary([RECEIPT, entry], lambda _: Document("原文"))


def test_explicitly_naming_another_registered_article_cannot_attribute_its_work_to_this_one():
    document = Document("## 交接\n铅封断裂先拍照。")
    library = PublicSopLibrary([RECEIPT, RETURN], lambda _: document)
    result = library.read_public_sop("receipt", "《退货交接 SOP》要求铅封断裂怎么办？")
    assert result["state"] == "no_match" and result["passages"] == []
    assert document.checks == 0


def test_short_context_includes_later_steps_that_fit_and_bounds_overlong_heading_labels():
    content = "\n".join(f"## 环节{i}\n保留第{i}次现场签字。" for i in range(1, 8))
    library = PublicSopLibrary([RECEIPT], lambda _: Document(content))
    preview = library.read_public_sop("receipt", "这篇的步骤是什么？")
    assert preview["state"] == "context"
    assert preview["passages"][-1] == {"section": "环节7", "quote": "保留第7次现场签字。"}

    heading = "一段被误写成标题的正文" * 800
    library = PublicSopLibrary([RECEIPT], lambda _: Document(f"# {heading}\n先拍铅封。"))
    answer = library.read_public_sop("receipt", "铅封")
    (passage,) = answer["passages"]
    assert len(passage["section"]) <= 200 and passage["section"] in heading
    assert passage["quote"] == "先拍铅封。"


def test_question_session_fixes_one_revision_but_selects_again_for_each_keyword(monkeypatch):
    document = Document("## 签收\n签收时两人到场。\n## 铅封\n铅封须完整。")
    returns = Document("## 批号\n批号须与签收单一致。")
    sources = {RECEIPT: document, RETURN: returns}
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(public_sops, "_library", PublicSopLibrary(sources, sources.__getitem__))
    session = public_sops.PublicSopSession()
    first = public_sops.read_public_sop("receipt", "签收", session=session)
    document.version = 2
    document.content = "## 铅封\n铅封编号另录入仓储系统。"
    second = public_sops.read_public_sop("receipt", "铅封", session=session)
    other = public_sops.read_public_sop("returns", "批号", session=session)
    assert first["revision"] == second["revision"] == other["revision"] == 1
    assert first["passages"] == [{"section": "签收", "quote": "签收时两人到场。"}]
    assert second["passages"] == [{"section": "铅封", "quote": "铅封须完整。"}]
    assert other["passages"] == [{"section": "批号", "quote": "批号须与签收单一致。"}]
    assert document.checks == 1 and returns.checks == 1

    next_question = public_sops.PublicSopSession()
    new_turn = public_sops.read_public_sop("receipt", "铅封", session=next_question)
    assert new_turn["revision"] == 2
    assert new_turn["passages"][0]["quote"] == "铅封编号另录入仓储系统。"
    assert document.checks == 2 and document.downloads == [1, 2]


def test_failed_question_session_keeps_the_same_failure_until_the_next_question():
    document = Document("## 铅封\n铅封须完整。")
    document.check_error = "forbidden"
    library = PublicSopLibrary([RECEIPT], lambda _: document)
    session = public_sops.PublicSopSession()
    assert library.read_public_sop("receipt", "铅封", session=session)["state"] == "unavailable"
    document.check_error = None
    assert library.read_public_sop("receipt", "签收", session=session)["passages"] == []
    assert document.checks == 1 and not document.downloads
    assert library.read_public_sop("receipt", "铅封")["state"] == "matched"
    assert document.checks == 2


def test_concurrent_reads_in_one_question_share_the_first_access_check():
    started = Event()
    release = Event()

    class SlowProbe(Document):
        def revision(self):
            started.set()
            assert release.wait(2)
            return super().revision()

    document = SlowProbe("## 铅封\n铅封须完整。\n## 签收\n签收要留底。")
    library = PublicSopLibrary([RECEIPT], lambda _: document)
    session = public_sops.PublicSopSession()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(library.read_public_sop, "receipt", "铅封", session=session)
        assert started.wait(2)
        second = executor.submit(library.read_public_sop, "receipt", "签收", session=session)
        release.set()
        assert first.result()["passages"][0]["quote"] == "铅封须完整。"
        assert second.result()["passages"][0]["quote"] == "签收要留底。"
    assert document.checks == 1 and document.downloads == [1]
