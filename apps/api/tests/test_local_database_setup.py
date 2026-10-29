"""本地建库配置必须可重复运行，不能覆盖已有凭据。"""

import importlib.util
from pathlib import Path


def load_setup():
    path = Path(__file__).resolve().parents[3] / "scripts" / "db-local.py"
    assert path.is_file(), "尚未实现本地数据库配置入口"
    spec = importlib.util.spec_from_file_location("db_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_init_preserves_credentials_and_private_permissions(tmp_path):
    setup = load_setup()
    (tmp_path / "apps/api").mkdir(parents=True)
    setup.initialize(tmp_path)
    private = tmp_path / "var/local-db/docker.env"
    sync_private = tmp_path / "var/local-db/sync.env"
    api_env = tmp_path / "apps/api/.env"
    first = private.read_bytes()
    api_first = api_env.read_bytes()
    sync_first = sync_private.read_bytes()
    assert b"POSTGRES_PASSWORD=" in first
    assert b"pdd_app" in api_first
    assert b"pdd_admin" not in api_first
    assert b"DATABASE_URL=postgresql+psycopg://" in api_first
    setup.initialize(tmp_path)
    assert private.read_bytes() == first
    assert sync_private.read_bytes() == sync_first
    assert api_env.read_bytes() == api_first
    assert private.stat().st_mode & 0o777 == 0o600
    assert sync_private.stat().st_mode & 0o777 == 0o600
    assert api_env.stat().st_mode & 0o777 == 0o600


def test_init_keeps_existing_api_environment(tmp_path):
    setup = load_setup()
    (tmp_path / "apps/api").mkdir(parents=True)
    api_env = tmp_path / "apps/api/.env"
    api_env.write_text("DATABASE_URL=existing-private-setting\n")
    setup.initialize(tmp_path)
    assert api_env.read_text() == "DATABASE_URL=existing-private-setting\n"


def test_compose_uses_managed_configuration_even_with_shell_overrides(tmp_path, monkeypatch):
    setup = load_setup()
    (tmp_path / "apps/api").mkdir(parents=True)
    setup.initialize(tmp_path)
    config = setup.read_config(tmp_path)
    binary = tmp_path / "bin/docker"
    binary.parent.mkdir()
    binary.write_text('#!/bin/sh\nprintf "%s|%s" "$POSTGRES_PASSWORD" "$POSTGRES_PORT"\n')
    binary.chmod(0o700)
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setenv("PATH", str(binary.parent))
    monkeypatch.setenv("POSTGRES_PASSWORD", "unrelated-shell-password")
    monkeypatch.setenv("POSTGRES_PORT", "15432")
    expected = config["POSTGRES_PASSWORD"] + "|55432"
    assert setup.compose("version", capture=True).stdout == expected
    assert setup.sql("SELECT 1") == expected


def test_retired_actions_are_rejected_before_local_initialization(monkeypatch):
    """旧链路不可再通过数据库工具重建；拒绝时也不读写本地配置。"""
    import pytest

    setup = load_setup()
    calls = []
    monkeypatch.setattr(setup, "initialize", lambda *_: calls.append("initialize"))
    monkeypatch.setattr(setup, "migrate", lambda: calls.append("migrate"), raising=False)
    for action in ("migrate", "sync:m1", "profiles:m1", "scores:m1"):
        monkeypatch.setattr(setup.sys, "argv", ["db-local.py", action])
        with pytest.raises(SystemExit):
            setup.main()
        assert calls == []
