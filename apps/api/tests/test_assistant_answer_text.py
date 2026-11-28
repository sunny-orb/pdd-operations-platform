"""模型误把内部调用写成正文时，各种 SSE 分片均只发布可见答复。"""

import pytest


def stream(parts):
    from app.modules.assistant.answer_text import AnswerTextFilter

    visible = AnswerTextFilter()
    return "".join(visible.feed(part) for part in parts) + visible.finish()


@pytest.mark.parametrize(
    "block",
    [
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="lookup">'
        '<｜｜DSML｜｜ parameter name="ids">原始参数</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>",
        '<｜DSML｜ calls>\n<｜DSML｜ invoke name="lookup">{"店铺":"内部范围"}'
        "</｜DSML｜ invoke>\n</｜DSML｜ calls>",
        '<｜DSML｜tool_calls><｜DSML｜invoke name="lookup">参数'
        "</｜DSML｜invoke></｜DSML｜tool_calls>",
        '<|DSML|function_calls><|DSML|invoke name="lookup">参数'
        "</|DSML|invoke></|DSML|function_calls>",
        '<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>lookup<｜tool▁sep｜>{"param":"value"}'
        "<｜tool▁call▁end｜><｜tool▁calls▁end｜>",
        '<tool_call>{"name":"lookup","arguments":{"key":"内部参数"}}</tool_call>',
        '<｜DSML｜ invoke name="lookup">内部参数</｜DSML｜ invoke>',
        '<｜DSML｜ parameter name="days">内部参数</｜DSML｜ parameter>',
        '<function_calls><invoke name="lookup"><parameter name="a">值</parameter>'
        "</invoke></function_calls><｜end▁of▁sentence｜>",
        '<invoke name="lookup"><parameter name="a">值</parameter></invoke>',
        '<｜DSML｜ calls><｜DSML｜ invoke name="n > 2"/>'
        '<｜DSML｜ invoke name="b"/>\n</｜DSML｜ calls>',
    ],
)
def test_suppresses_calls_and_keeps_surrounding_text_at_every_split(block):
    from app.modules.assistant.answer_text import clean_answer_text

    body, expected = "前段。\n" + block + "\n后段。", "前段。\n\n后段。"
    assert clean_answer_text(body) == expected
    assert stream(body) == expected  # 每个字符分别到达。
    for offset in range(len(body) + 1):
        assert stream([body[:offset], "", body[offset:]]) == expected


@pytest.mark.parametrize(
    "unfinished",
    [
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="lookup">原始参数</｜｜DSML｜｜ invoke>',
        '<｜DSML｜ parameter name="days">未结束的参数',
        '<｜DSML｜ invoke name="unfinished',
        "<｜DSML｜ cal",
        '<tool_call>{"a":',
        "<｜tool▁calls▁begin｜>这里是参数",
    ],
)
def test_unfinished_internal_call_is_dropped_through_end_of_response(unfinished):
    assert stream("已核实。" + unfinished) == "已核实。"


def test_plain_text_is_immediate_and_keeps_comparisons_and_formatting():
    from app.modules.assistant.answer_text import AnswerTextFilter

    visible = AnswerTextFilter()
    assert visible.feed("先") == "先"
    assert visible.feed("看 ROI ") == "看 ROI "
    assert visible.feed("<") == ""
    assert visible.feed(" 2 和金额 <=100，") == "< 2 和金额 <=100，"
    assert visible.feed("普通 <small>标注</small>，a<<2。") == "普通 <small>标注</small>，a<<2。"
    assert visible.feed("末尾 <") == "末尾 "
    assert visible.finish() == "<"
    assert visible.finish() == ""


def test_many_hidden_chunks_resume_immediately_after_the_real_close():
    from app.modules.assistant.answer_text import AnswerTextFilter

    visible = AnswerTextFilter()
    assert visible.feed('结论<｜DSML｜ calls><｜DSML｜ invoke name="') == "结论"
    for _ in range(64):
        assert visible.feed("x" * 4096) == ""
    assert visible.feed('"><｜DSML｜ parameter name="a">') == ""
    for _ in range(64):
        assert visible.feed("私密参数< 42 " * 512) == ""
    assert visible.feed("</｜DSML｜ parameter></｜DSML｜ invoke></｜DSML｜ calls>。") == "。"
    assert visible.finish() == ""
