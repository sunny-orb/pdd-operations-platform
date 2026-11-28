"""飞书 SOP 的只读元数据和指定修订正文。预测任务沿用它自己的固定单篇入口。"""

import json
import re
import subprocess
from functools import cached_property

from app.core.config import lark_cli_executable

DOCUMENT_ID = "H3svdLyEYoxsK3xgeBOceOxfnMc"
TITLE = "拼多多月度 GMV 滚动预测 SOP"
DOC_URL = "https://kocotree.feishu.cn/docx/"
ACCESS_ERRORS = frozenset({"not_found", "deleted", "forbidden"})


class ForecastSourceError(Exception):
    def __init__(self, reason: str):
        self.reason = reason if reason in ACCESS_ERRORS else "temporary"
        super().__init__(self.reason)


def _error_reason(error: dict) -> str:
    codes = {1770002: "not_found", 1770003: "deleted", 1770032: "forbidden"}
    if error.get("subtype") in {"missing_scope", "permission_denied", "forbidden"}:
        return "forbidden"
    return codes.get(error.get("code"), "temporary")


def validated_document_id(document_id: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9]{20,64}", document_id):
        raise ValueError("FORECAST_SOP_DOCUMENT_ID")
    return document_id


class FeishuSopReader:
    def __init__(self, document_id: str, *, identity="bot"):
        if identity not in {"user", "bot"}:
            raise ValueError("FORECAST_SOP_IDENTITY")
        self.identity = identity
        self.document_id = validated_document_id(document_id)

    @cached_property
    def executable(self) -> str:
        return lark_cli_executable()

    def _document(self, *arguments):
        # CLI 错误可携身份或授权链接；只向上层提供固定原因码。
        try:
            result = subprocess.run(
                [self.executable, *arguments, "--as", self.identity],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            envelope = json.loads(result.stderr if result.returncode else result.stdout)
            if result.returncode or envelope.get("ok") is not True:
                raise ForecastSourceError(_error_reason(envelope.get("error", {})))
            doc = envelope["data"]["document"]
            revision = doc["revision_id"]
            if (
                envelope.get("identity") != self.identity
                or doc["document_id"] != self.document_id
                or type(revision) is not int
                or not 0 < revision <= 2**63 - 1
            ):
                raise ForecastSourceError("temporary")
            return doc
        except (
            OSError,
            subprocess.TimeoutExpired,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ):
            raise ForecastSourceError("temporary") from None

    def revision(self) -> int:
        """原生元数据只返回标题和最新修订，无正文及评论。"""
        return self._document("api", "GET", f"/open-apis/docx/v1/documents/{self.document_id}")[
            "revision_id"
        ]

    def markdown(self, revision: int) -> str:
        """读取钉住的完整正文和原生表格，仅提取当前文档 content。"""
        doc = self._document(
            "docs",
            "+fetch",
            "--doc",
            self.document_id,
            "--doc-format",
            "markdown",
            "--revision-id",
            str(revision),
        )
        if doc["revision_id"] != revision or not isinstance(doc.get("content"), str):
            raise ForecastSourceError("temporary")
        return doc["content"]


class FeishuForecastSopReader(FeishuSopReader):
    def __init__(self, *, identity="bot"):
        super().__init__(DOCUMENT_ID, identity=identity)

    @classmethod
    def for_isolated_copy(cls, document_id: str, *, identity="user"):
        """用于获准的预测验收副本；同步入口另行核对随机本机隔离库。"""
        reader = cls(identity=identity)
        reader.document_id = validated_document_id(document_id)
        return reader
