"""从模型的可见文本中剔除误作正文的调用标记及其内容，不解析或执行参数。"""

from itertools import product

from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPartDelta
from pydantic_ai.messages import TextPart


def stream_options(run, can_show, *, buffered=False):
    """只把可交付的可见文本发给页面，调用参数与推理内容不进入 SSE。"""
    if not run.emit or buffered:
        return {}

    async def stream_text(ctx, events):
        visible = AnswerTextFilter()
        async for event in events:
            if not can_show():
                continue
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                run.emit_text(visible.feed(event.part.content))
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                run.emit_text(visible.feed(event.delta.content_delta))
        run.emit_text(visible.finish())

    return {"event_stream_handler": stream_text}


def _control_tags():
    # V4.1 用带空格的 calls，V4 / V3.2 用 tool_calls / function_calls。
    names = {
        "calls": "calls",
        "tool_calls": "calls",
        "function_calls": "calls",
        "tool_call": "invoke",
        "function_call": "invoke",
        "invoke": "invoke",
        "parameter": "parameter",
    }
    # 旧协议有单独的输出结束符，遇到它时只剔除标记本身。
    tags = {"<|end_of_sentence|": ("eos", True)}
    for closing, slash in ((False, ""), (True, "/")):
        for name, kind in names.items():
            for left, right, space in product(("|", "||"), ("|", "||"), ("", " ")):
                tags[f"<{slash}{left}dsml{right}{space}{name}"] = (kind, closing)
            if name != "calls":
                tags[f"<{slash}{name}"] = (kind, closing)
        end = "end" if closing else "begin"
        for name, kind in (("tool_calls", "calls"), ("tool_call", "invoke")):
            tags[f"<|{name}_{end}|"] = (kind, closing)
    return tags


_TAGS = _control_tags()
_PREFIXES = {tag[:n] for tag in _TAGS for n in range(1, len(tag) + 1)}
_BARS = str.maketrans("｜▁", "|_")


class AnswerTextFilter:
    """每个模型答复使用一个实例。只暂存可能的标记前缀，参数和属性随到随弃。"""

    def __init__(self):
        self._prefix = ""
        self._raw = ""
        self._tag: tuple[str, bool] | None = None
        self._quote = ""
        self._last = ""
        self._stack: list[str] = []
        self._discard = False

    def _tag_character(self, char: str):
        """识别属性边界；引号内的 > 属于属性，不能提前结束标记。"""
        if self._quote:
            if char == self._quote:
                self._quote = ""
        elif char in ('"', "'"):
            self._quote = char
        elif char == ">":
            kind, closing = self._tag
            if closing:
                if self._stack and self._stack[-1] == kind:
                    self._stack.pop()
            elif self._last != "/":
                if len(self._stack) < 8:  # 正常最多为调用组、调用、参数三层。
                    self._stack.append(kind)
                else:
                    self._discard = True
            self._tag = None
        self._last = char

    def feed(self, text: str) -> str:
        """返回本片段已确定的可见文字；普通正文直接交给界面。"""
        visible = []
        pos = 0
        while pos < len(text) and not self._discard:
            if not self._prefix and not self._tag:
                start = text.find("<", pos)
                if not self._stack:
                    visible.append(text[pos:] if start < 0 else text[pos:start])
                if start < 0:
                    break
                self._prefix = self._raw = "<"
                pos = start + 1
                continue
            char = text[pos]
            pos += 1
            if self._tag:
                self._tag_character(char)
                continue
            if (char.isspace() or char in "/>") and self._prefix in _TAGS:
                self._tag = _TAGS[self._prefix]
                self._prefix = self._raw = self._last = ""
                self._tag_character(char)
                continue
            token = " " if char.isspace() else char.translate(_BARS).lower()
            if token == " " and self._prefix.endswith(" "):
                continue
            if self._prefix + token in _PREFIXES:
                self._prefix += token
                self._raw += char
                continue
            if not self._stack:
                visible.append(self._raw)
            self._prefix = self._raw = ""
            pos -= 1  # 当前字符还可能是下一个标记的左尖括号。
        return "".join(visible)

    def finish(self) -> str:
        """保留末尾普通的比较符；未完成的内部调用及专用标记前缀均不发布。"""
        private = self._prefix.startswith(("<|", "</|")) or self._prefix in _TAGS
        tail = "" if self._stack or self._tag or self._discard or private else self._raw
        self._prefix = self._raw = ""
        return tail


def clean_answer_text(text: str) -> str:
    """非流式答复与逐片展示共用同一套边界。"""
    visible = AnswerTextFilter()
    return visible.feed(text) + visible.finish()
