"""只读飞书元数据和整篇 Markdown 的真实 CLI 协议边界。"""

import json
import subprocess
import sys
from importlib.util import find_spec
from unittest.mock import patch

import pytest

DOC = "H3svdLyEYoxsK3xgeBOceOxfnMc"


def test_light_probe_and_revision_pinned_complete_markdown():
    assert find_spec("app.connectors.forecast_sop") is not None
    from app.connectors.forecast_sop import FeishuForecastSopReader

    calls = []
    body = "# 原文\n\n| 日期 | 权重 |\n| --- | --- |\n| 近 3 日 | 60% |"

    def run(args, **kwargs):
        calls.append(args)
        doc = {"document_id": DOC, "revision_id": 4}
        if args[1] == "docs":
            doc["content"] = body
        result = {"ok": True, "identity": "user", "data": {"document": doc}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(result), stderr="")

    with patch("subprocess.run", side_effect=run):
        reader = FeishuForecastSopReader(identity="user")
        assert reader.revision() == 4
        assert reader.markdown(4) == body
    assert calls[0][1:4] == ["api", "GET", f"/open-apis/docx/v1/documents/{DOC}"]
    assert "--scope" not in calls[1]
    assert calls[1][calls[1].index("--revision-id") + 1] == "4"
    assert calls[1][calls[1].index("--doc-format") + 1] == "markdown"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ({"code": 1770002}, "not_found"),
        ({"code": 1770003}, "deleted"),
        ({"code": 1770032}, "forbidden"),
        ({"type": "authorization", "subtype": "missing_scope"}, "forbidden"),
        ({"code": 1771005}, "temporary"),
        ({"type": "authentication", "subtype": "token_expired"}, "temporary"),
    ],
)
def test_error_envelope_redacts_provider_details(error, reason):
    from app.connectors.forecast_sop import FeishuForecastSopReader, ForecastSourceError

    error["message"] = "Sensitive detail in upstream response"
    cmd = subprocess.CompletedProcess(
        [], 1, stdout="", stderr=json.dumps({"ok": False, "error": error})
    )
    with (
        patch("subprocess.run", return_value=cmd),
        pytest.raises(ForecastSourceError) as caught,
    ):
        FeishuForecastSopReader(identity="bot").revision()
    assert caught.value.reason == reason
    assert "Sensitive" not in str(caught.value)


def test_foreign_document_wrong_identity_or_unpinned_content_cannot_be_used():
    from app.connectors.forecast_sop import FeishuForecastSopReader, ForecastSourceError

    reader = FeishuForecastSopReader(identity="user")
    for doc, identity, rev in [(DOC, "bot", 4), ("Elsewhere", "user", 4), (DOC, "user", 2)]:
        result = {
            "ok": True,
            "identity": identity,
            "data": {
                "document": {
                    "document_id": doc,
                    "revision_id": rev,
                    "content": "非本次原文",
                }
            },
        }
        cmd = subprocess.CompletedProcess([], 0, stdout=json.dumps(result), stderr="")
        with patch("subprocess.run", return_value=cmd), pytest.raises(ForecastSourceError):
            reader.markdown(4)


@pytest.mark.parametrize("override", [False, True], ids=["api-env", "process-env"])
def test_public_and_forecast_subprocess_find_cli_with_empty_path(monkeypatch, tmp_path, override):
    from app.connectors.forecast_sop import FeishuForecastSopReader, FeishuSopReader
    from app.core import config

    # 用绝对路径 Python 充当可执行程序，实际走子进程，避开调用飞书或依赖系统 shell。
    working = tmp_path / "worker"
    working.mkdir()
    response = {
        "ok": True,
        "identity": "user",
        "data": {"document": {"document_id": DOC, "revision_id": 19}},
    }
    (working / "api").write_text(f"print({json.dumps(response)!r})\n", encoding="utf-8")
    api = tmp_path / "apps" / "api"
    api.mkdir(parents=True)
    api_env = api / ".env"
    candidate = str(tmp_path / "unavailable") if override else sys.executable
    api_env.write_text(f"LARK_CLI_BIN={json.dumps(candidate)}\n", encoding="utf-8")
    monkeypatch.setattr(config, "API_ENV_FILE", api_env, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.chdir(working)
    if override:
        monkeypatch.setenv("LARK_CLI_BIN", sys.executable)
    else:
        monkeypatch.delenv("LARK_CLI_BIN", raising=False)

    for reader in (FeishuSopReader(DOC, identity="user"), FeishuForecastSopReader(identity="user")):
        assert reader.revision() == 19
    assert config.Settings(_env_file=api_env).lark_cli_bin == sys.executable


def test_unconfigured_cli_uses_existing_command_without_loading_database_settings(
    monkeypatch, tmp_path
):
    from app.connectors.forecast_sop import FeishuForecastSopReader
    from app.core import config

    api_env = tmp_path / ".env"
    api_env.write_text("DATABASE_URL=unused-by-cli\nLARK_CLI_BIN=\n", encoding="utf-8")
    monkeypatch.setattr(config, "API_ENV_FILE", api_env, raising=False)
    monkeypatch.delenv("LARK_CLI_BIN", raising=False)
    envelope = {
        "ok": True,
        "identity": "user",
        "data": {"document": {"document_id": DOC, "revision_id": 20}},
    }
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(envelope), stderr=""),
    ) as run:
        assert FeishuForecastSopReader(identity="user").revision() == 20
    assert run.call_args.args[0][0] == "lark-cli"
