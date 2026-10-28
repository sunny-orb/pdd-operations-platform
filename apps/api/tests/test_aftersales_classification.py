"""固定售后词典的真实枚举与否定/重叠反例；不从名称或规格推断问题。"""

from decimal import Decimal

import pytest


@pytest.mark.parametrize(
    "reason,size,quality",
    [
        ("尺码没选对", "unspecified", []),
        ("质量问题", None, ["quality"]),
        ("商品破损或污渍", None, ["quality", "damage"]),
        ("做工粗糙/有瑕疵", None, ["workmanship"]),
        ("颜色不合适", None, []),
        ("不是质量问题", None, []),
        ("不是质量问题但商品破损", None, ["damage"]),
        ("大小尺寸与商品描述不符", None, []),
        ("尺码偏大也偏小", "conflict", []),
    ],
)
def test_source_reason_dictionary_uses_literal_entries_and_preserves_primary(reason, size, quality):
    from app.modules.aftersales.classification import classify, rule_parameters

    result = classify({"source_return_reason": reason}, rule_parameters())
    assert result["primary_reason"] == reason
    assert result["size"] == size
    assert result["quality"] == quality


def test_missing_reason_never_uses_product_names_specs_or_unapproved_analysis_fields():
    from app.modules.aftersales.classification import classify, rule_parameters

    result = classify(
        dict(
            source_return_reason=None,
            source_product_name="质量问题尺码偏大",
            source_color_specification="破损",
            source_analysis_fields={"remark": "做工瑕疵"},
        ),
        rule_parameters(),
    )
    assert result["primary_reason"] == "未填写原因"
    assert result["quality"] == [] and result["size"] is None
    assert result["has_text"] is False


def test_quality_counts_each_original_quantity_once_while_labels_overlap():
    from app.modules.aftersales.classification import observations, rule_parameters

    rows = [
        dict(source_return_reason="质量问题，商品破损或污渍", actual_returned_qty=Decimal("2.5")),
        dict(source_return_reason="不是质量问题", actual_returned_qty=Decimal("1")),
        dict(source_return_reason=None, actual_returned_qty=Decimal("3")),
        dict(source_return_reason="尺码偏大也偏小", actual_returned_qty=Decimal("4")),
    ]
    result = observations(rows, rule_parameters())
    assert result["quality_qty"] == Decimal("2.5")
    assert result["size_qty"] == Decimal("4")
    assert {r["name"]: r["qty"] for r in result["quality_rows"]} == {
        "质量问题/污渍等": Decimal("2.5"),
        "破损/破洞/开裂": Decimal("2.5"),
    }
    assert result["size_rows"][2]["qty"] == Decimal("4")
    assert "未填写" in result["note"] and "观察量" in result["note"]
    assert "标签" in result["note"]


def test_no_text_or_old_empty_dictionaries_remain_unavailable():
    from app.modules.aftersales.classification import observations, rule_parameters

    rows = [dict(source_return_reason=None, actual_returned_qty=Decimal("2"))]
    assert observations(rows, rule_parameters())["quality_qty"] is None
    old = rule_parameters()
    old["dictionaries"] = []
    assert observations(rows, old)["size_qty"] is None
    assert observations(rows, old)["quality_qty"] is None


def test_frozen_entry_changes_are_used_instead_of_module_defaults():
    from app.modules.aftersales.classification import classify, rule_parameters

    parameters = rule_parameters()
    quality = next(d for d in parameters["dictionaries"] if d["dictionary_code"] == "quality_issue")
    quality["entries"] = [
        dict(entry_code="only", order=1, term="限定词", output_code="damage", negative_terms=[])
    ]
    assert classify({"source_return_reason": "质量问题"}, parameters)["quality"] == []
    assert classify({"source_return_reason": "限定词"}, parameters)["quality"] == ["damage"]


def test_query_token_changes_when_classification_or_target_code_changes(monkeypatch):
    from pathlib import Path

    from app.modules.aftersales.repository import revision

    original = Path.read_bytes
    before = revision()
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: original(path) + (b"changed" if path.name == "classification.py" else b""),
    )
    assert revision() != before
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: original(path) + (b"changed" if path.name == "targets.py" else b""),
    )
    assert revision() != before


def test_quality_total_requires_its_confirmed_counting_branch():
    from app.modules.aftersales.classification import observations, rule_parameters

    parameters = rule_parameters()
    next(b for b in parameters["branches"] if b["code"] == "quality_quantity")["enabled"] = False
    rows = [dict(source_return_reason="质量问题", actual_returned_qty=Decimal("2"))]
    assert observations(rows, parameters)["quality_qty"] is None


def test_unknown_dictionary_output_fails_closed_instead_of_crashing_or_guessing():
    from app.modules.aftersales.classification import observations, rule_parameters

    parameters = rule_parameters()
    quality = next(d for d in parameters["dictionaries"] if d["dictionary_code"] == "quality_issue")
    quality["entries"][0]["output_code"] = "unsupported"
    rows = [dict(source_return_reason="质量问题", actual_returned_qty=Decimal("2"))]
    assert observations(rows, parameters)["quality_qty"] is None


def test_issue_comparison_requires_the_same_frozen_dictionary():
    from app.modules.aftersales.classification import compatible, rule_parameters

    now, old = rule_parameters(), rule_parameters()
    assert compatible(now, old, "quality")
    next(d for d in old["dictionaries"] if d["dictionary_code"] == "quality_issue")["entries"][0][
        "term"
    ] = "限定词"
    assert not compatible(now, old, "quality")
    assert compatible(now, old, "size")
