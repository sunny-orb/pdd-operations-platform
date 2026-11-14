"""本地真实文件系统验证发布边界；Docker外部操作由可控进程边界替代。"""

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_deployment_handoff import FakeRuntime, record

ROOT = Path(__file__).resolve().parents[3]


def load_controller():
    spec = importlib.util.spec_from_file_location(
        "deployment_controller", ROOT / "deploy/manage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(digit="a"):
    return {
        "commit": digit * 40,
        **{
            name: f"ghcr.io/sunny-orb/pdd-operations-platform-{name}@sha256:" + digit * 64
            for name in ("api", "web", "worker")
        },
    }


@pytest.fixture
def project(monkeypatch):
    module = load_controller()
    # 用项目var内临时目录，避免macOS /var符号链接和/tmp共享目录影响边界测试。
    with tempfile.TemporaryDirectory(prefix="release-test-", dir=ROOT / "var") as temporary:
        path = Path(temporary).resolve() / "project"
        path.mkdir(mode=0o750)
        monkeypatch.setattr(module, "ROOT", path)
        monkeypatch.setattr(module.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="songbing"))
        for name in module.UPLOADS:
            shutil.copyfile(ROOT / "deploy" / name, path / name)
        for name in (".env.runtime", ".htpasswd"):
            (path / name).write_text("synthetic")
            (path / name).chmod(0o600)
        (path / ".docker").mkdir(mode=0o700)
        (path / ".docker/config.json").write_text('{"auths":{}}')
        (path / ".docker/config.json").chmod(0o600)
        yield module, path


@pytest.fixture
def docker_calls(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        stdout = '{"status":"already_initialized"}' if "run" in args else ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def seed_handoff(controller, digit="a"):
    """真实受控文件与状态机，只有Docker/代理外部边界用确定性Runtime替代。"""
    compose = base64.b64encode(controller.read("compose.production.yaml")).decode()
    active = {**record(digit), "compose": compose}
    controller.write(".release.json", json.dumps({
        "version": 1, "manifest": active["manifest"], "compose": compose,
    }).encode())
    handoff = controller.handoff()
    runtime = FakeRuntime(digit * 40)
    runtime.abort = lambda state: runtime.events.append("abort_candidate")
    handoff.runtime = runtime
    handoff.save({
        "version": 1, "phase": "idle",
        "gateway": {"id": "f" * 64, "image": active["manifest"]["web"]},
        "active": active, "previous": None, "candidate": None,
        "old_workers": [], "legacy_web": None, "networks": {},
    })
    return handoff


def test_release_refuses_unapproved_directory_before_any_docker_call(tmp_path):
    shutil.copyfile(ROOT / "deploy/release.sh", tmp_path / "release.sh")
    result = subprocess.run(["bash", str(tmp_path / "release.sh")], capture_output=True)
    assert result.returncode != 0
    assert not (tmp_path / ".release.lock").exists()


def test_public_release_does_not_require_legacy_website_password(project):
    """无Basic认证的新发布不依赖旧口令文件；数据库配置仍必需。"""
    module, path = project
    (path / ".htpasswd").unlink()
    with module.Project() as controller:
        environment = controller.docker_environment(manifest())
        assert environment["RUNTIME_ENV_FILE"] == str(path / ".env.runtime")
        (path / ".env.runtime").unlink()
        with pytest.raises(module.BoundaryError, match="MISSING_FILE: .env.runtime"):
            controller.docker_environment(manifest())


def test_account_cannot_be_overridden_by_environment(project, monkeypatch, docker_calls):
    module, path = project
    monkeypatch.setenv("SSH_USER", "songbing")
    monkeypatch.setenv("USER", "songbing")
    monkeypatch.setattr(module.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="someone_else"))
    with pytest.raises(module.BoundaryError, match="WRONG_ACCOUNT"), module.Project():
        pass
    assert not docker_calls
    assert not (path / ".release.lock").exists()


@pytest.mark.parametrize(
    "name",
    [
        ".env.runtime",
        ".htpasswd",
        "compose.production.yaml",
        "manage.py",
        ".release.lock",
        ".release.json",
        ".compose.rollback.yaml",
        ".release.previous.json",
        ".release.recovery.json",
        "handoff.py",
        "assets.py",
        ".handoff.json",
        ".compose.handoff.json",
        ".asset-extraction.json",
    ],
)
@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_files_refused_without_changing_external_target(project, docker_calls, name, kind):
    module, path = project
    target = path.parent / "external"
    target.write_text("unchanged")
    target.chmod(0o644)
    (path / name).unlink(missing_ok=True)
    if kind == "symlink":
        (path / name).symlink_to(target)
    else:
        os.link(target, path / name)
    with pytest.raises(module.BoundaryError), module.Project():
        pass
    assert target.read_text() == "unchanged"
    assert target.stat().st_mode & 0o777 == 0o644
    assert not docker_calls


@pytest.mark.parametrize("which", ["root", "parent", "docker"])
def test_symlink_directories_rejected(project, monkeypatch, docker_calls, which):
    module, path = project
    if which == "root":
        link = path.parent / "alias"
        link.symlink_to(path, target_is_directory=True)
        monkeypatch.setattr(module, "ROOT", link)
    elif which == "parent":
        link = path.parent / "alias"
        link.symlink_to(path.parent, target_is_directory=True)
        monkeypatch.setattr(module, "ROOT", link / "project")
    else:
        (path / ".docker").rename(path.parent / "docker-original")
        (path / ".docker").symlink_to(path.parent / "docker-original", target_is_directory=True)
    with pytest.raises(module.BoundaryError), module.Project():
        pass
    assert not docker_calls


def test_release_ignores_environment_overrides_and_uses_project_credentials(
    project, monkeypatch, docker_calls
):
    module, path = project
    for key in (
        "DOCKER_CONFIG",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "RUNTIME_ENV_FILE",
        "WEB_HTPASSWD_FILE",
        "DEPLOY_DIR",
    ):
        monkeypatch.setenv(key, "/outside")
    with module.Project() as controller, controller.lock():
        environment = controller.docker_environment(manifest())
        controller.docker(["version"], environment)
        runtime = controller.handoff().runtime
        runtime.use(manifest())
        runtime.compose(manifest(), "blue", manifest()["web"], ["config", "--quiet"])
    config = json.loads((path / ".compose.handoff.json").read_text())
    assert config["name"] == "pdd-operations-production"
    assert config["services"]["api_blue"]["image"] == manifest()["api"]
    assert docker_calls
    for args, options in docker_calls:
        assert args[:5] == [
            "/usr/bin/docker",
            "--config",
            str(path / ".docker"),
            "--host",
            "unix:///var/run/docker.sock",
        ]
        assert options["env"]["DOCKER_CONFIG"] == str(path / ".docker")
        assert "/outside" not in options["env"].values()
        assert options["env"]["HOME"] == str(path)
        assert options["cwd"] == path
        if "compose" in args:
            assert args[args.index("--project-name") + 1] == "pdd-operations-production"
            assert args[args.index("--env-file") + 1] == "/dev/null"
        assert "--apply" not in args


@pytest.mark.parametrize("change", ["secret_permissions", "config_link", "helper", "plugin"])
def test_unsafe_credentials_fail_before_docker(project, docker_calls, change):
    module, path = project
    if change == "secret_permissions":
        (path / ".env.runtime").chmod(0o644)
    elif change == "config_link":
        (path / ".docker/config.json").unlink()
        (path / ".docker/config.json").symlink_to(path / ".env.runtime")
    elif change == "helper":
        (path / ".docker/config.json").write_text('{"credsStore":"outside-helper"}')
    else:
        (path / ".docker/cli-plugins").mkdir()
    with pytest.raises(module.BoundaryError), module.Project() as controller:
        controller.release(manifest())
    assert not docker_calls


def test_failed_start_preserves_previous_active_state(project, monkeypatch):
    module, path = project
    original = (path / "compose.production.yaml").read_bytes()
    with module.Project() as controller:
        handoff = seed_handoff(controller, "b")

        def fail_start(*_):
            raise subprocess.CalledProcessError(1, "candidate up --wait")

        monkeypatch.setattr(handoff.runtime, "prepare", fail_start)
        with pytest.raises(subprocess.CalledProcessError):
            controller.release(manifest())
    assert json.loads((path / ".release.json").read_text())["manifest"] == manifest("b")
    assert base64.b64decode(json.loads((path / ".release.json").read_text())["compose"]) == original
    assert "switch" not in handoff.runtime.events and "retire" not in handoff.runtime.events


def test_success_then_rollback_restores_paired_compose_and_images(project):
    module, path = project
    with module.Project() as controller, controller.lock():
        seed_handoff(controller, "b")
        old = (path / "compose.production.yaml").read_bytes()
        (path / "compose.production.yaml").write_text("new-compose")
        controller.release(manifest())
        controller.rollback()
    assert json.loads((path / ".release.json").read_text())["manifest"] == manifest("b")
    assert base64.b64decode(json.loads((path / ".release.json").read_text())["compose"]) == old
    assert json.loads((path / ".handoff.json").read_text())["active"]["manifest"] == manifest("b")


@pytest.mark.parametrize("failure", ["worker", "database"])
def test_worker_or_uninitialized_database_blocks_start(project, monkeypatch, failure):
    module, path = project
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        output = "worker-id" if failure == "worker" and "ps" in args else ""
        if "run" in args:
            output = '{"status":"empty"}'
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(subprocess, "run", run)
    with module.Project() as controller:
        runtime = controller.handoff().runtime
        monkeypatch.setattr(runtime, "networks", lambda: {})
        runtime.network_snapshot = {}
        message = "WORKER_RUNNING" if failure == "worker" else "DATABASE_NOT_INITIALIZED"
        with pytest.raises(ValueError, match=message):
            runtime.preflight(manifest())
    assert not any("up" in call for call in calls)
    assert not (path / ".release.json").exists()


def test_upload_validates_all_names_before_writing(project, docker_calls):
    module, path = project
    payload = {"manifest": manifest(), "files": {"../escape": base64.b64encode(b"bad").decode()}}
    before = (path / "compose.production.yaml").read_bytes()
    with (
        pytest.raises(module.BoundaryError, match="UPLOAD_ALLOWLIST"),
        module.Project() as controller,
    ):
        controller.receive(payload)
    assert (path / "compose.production.yaml").read_bytes() == before
    assert not (path.parent / "escape").exists()
    assert not docker_calls


def test_lock_blocks_concurrent_release(project):
    module, _ = project
    with module.Project() as first, first.lock(), module.Project() as second:
        with pytest.raises(BlockingIOError):
            second.lock()


@pytest.mark.parametrize("bad", ["latest", "other-repository", "wrong-service", "injected"])
def test_manifest_rejects_unapproved_images(project, docker_calls, bad):
    module, _ = project
    value = manifest()
    value["api"] = {
        "latest": "ghcr.io/sunny-orb/pdd-operations-platform-api:latest",
        "other-repository": "ghcr.io/other/project-api@sha256:" + "a" * 64,
        "wrong-service": value["web"],
        "injected": value["api"] + "; touch /outside",
    }[bad]
    with pytest.raises(module.BoundaryError), module.Project() as controller:
        controller.release(value)
    assert not docker_calls


def load_dispatch():
    spec = importlib.util.spec_from_file_location(
        "deployment_dispatch", ROOT / "deploy/dispatch.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incomplete_active_state_blocks_before_any_docker_call(project, docker_calls):
    module, path = project
    (path / ".release.json").write_text(json.dumps(manifest("b")))
    with pytest.raises(module.BoundaryError), module.Project() as controller:
        controller.release(manifest())
    assert not docker_calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("conclusion", "failure"),
        ("status", "in_progress"),
        ("path", ".github/workflows/ci.yml"),
        ("event", "pull_request"),
        ("head_repository", {"full_name": "attacker/fork"}),
        ("head_sha", "bad;echo unsafe"),
    ],
)
def test_deploy_rejects_untrusted_build_before_ssh(monkeypatch, field, value):
    module = load_dispatch()
    run = {
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/build.yml",
        "event": "workflow_dispatch",
        "repository": {"full_name": "sunny-orb/pdd-operations-platform"},
        "head_repository": {"full_name": "sunny-orb/pdd-operations-platform"},
        "head_sha": "a" * 40,
    }
    run[field] = value
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(run))

    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(ValueError):
        module.verify_run("123")
    assert calls == [
        ["gh", "api", "--method", "GET", "repos/sunny-orb/pdd-operations-platform/actions/runs/123"]
    ]


def test_deploy_uses_fixed_destination_and_whitelisted_payload(tmp_path, monkeypatch):
    module = load_dispatch()
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="a" * 40 + "\n")

    monkeypatch.setattr(subprocess, "run", execute)
    monkeypatch.setenv("SSH_USER", "other")
    monkeypatch.setenv("DEPLOY_DIR", "/other")
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest()))
    module.send(source, "a" * 40)
    args, options = calls[-1]
    assert args[0] == "ssh"
    assert args[args.index("-p") + 1] == "10022"
    assert args[-2] == "songbing@121.40.167.37"
    payload = json.loads(options["input"])
    assert set(payload["files"]) == {
        "compose.production.yaml",
        "manage.py",
        "release.sh",
        "rollback.sh",
        "storage.py",
        "handoff.py",
        "assets.py",
    }
    assert payload["manifest"] == manifest()


def test_build_manifest_commit_mismatch_prevents_ssh(tmp_path, monkeypatch):
    module = load_dispatch()
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="b" * 40)

    monkeypatch.setattr(subprocess, "run", execute)
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest()))
    with pytest.raises(ValueError, match="BUILD_COMMIT_MISMATCH"):
        module.send(source, "a" * 40)
    assert calls == [["git", "rev-parse", "HEAD"]]


def test_validated_upload_and_release_preserve_outside_files(project):
    module, path = project
    payload = {
        "manifest": manifest(),
        "files": {
            name: base64.b64encode((ROOT / "deploy" / name).read_bytes()).decode()
            for name in module.UPLOADS
        },
    }
    sentinel = path.parent / "other-project"
    sentinel.write_text("untouched")
    with module.Project() as controller, controller.lock():
        seed_handoff(controller, "b")
        controller.receive(payload)
    assert json.loads((path / ".release.json").read_text())["manifest"] == manifest()
    assert sentinel.read_text() == "untouched"


def test_build_only_workflow_produces_manifest_without_deploy_side_effects(tmp_path):
    def read_yaml(path):
        # 使用npm锁文件中ESLint已有的js-yaml，不新增Python解析依赖。
        output = subprocess.run(
            [
                "node",
                "-e",
                'console.log(JSON.stringify(require("js-yaml").load(require("fs").readFileSync(process.argv[1],"utf8"))))',
                str(path),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(output.stdout)

    workflow = read_yaml(ROOT / ".github/workflows/build.yml")
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert set(workflow["jobs"]) == {"checks", "images"}
    # 执行真正的清单生成步骤；无需运行Docker构建，断言产物的提交/服务/digest绑定。
    step = next(step for step in workflow["jobs"]["images"]["steps"] if step.get("id") == "refs")
    env = {
        **os.environ,
        "PREFIX": "ghcr.io/sunny-orb/pdd-operations-platform",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "API_DIGEST": "sha256:" + "a" * 64,
        "WEB_DIGEST": "sha256:" + "b" * 64,
        "WORKER_DIGEST": "sha256:" + "c" * 64,
    }
    result = subprocess.run(
        ["bash", "-eu", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads((tmp_path / "release-manifest.json").read_text())
    assert actual["commit"] == "a" * 40
    for name, digit in [("api", "a"), ("web", "b"), ("worker", "c")]:
        assert (
            actual[name] == f"ghcr.io/sunny-orb/pdd-operations-platform-{name}@sha256:" + digit * 64
        )
    deploy = read_yaml(ROOT / ".github/workflows/deploy.yml")
    assert set(deploy["on"]) == {"workflow_dispatch"}
    assert "build_run_id" in deploy["on"]["workflow_dispatch"]["inputs"]
    assert set(deploy["jobs"]) == {"deploy"}


def test_interrupted_state_write_does_not_mix_previous_images_and_compose(
    project, monkeypatch
):
    module, path = project
    with module.Project() as controller:
        handoff = seed_handoff(controller, "b")
        original = (path / "compose.production.yaml").read_bytes()
        (path / "compose.production.yaml").write_text("candidate-a")
        replace = module.os.replace

        def interrupted(source, destination, **kwargs):
            if destination == ".release.json":
                raise OSError("simulated disk failure")
            return replace(source, destination, **kwargs)

        monkeypatch.setattr(module.os, "replace", interrupted)
        with pytest.raises(OSError):
            controller.release(manifest())
        assert handoff.state()["phase"] == "committing"
        assert json.loads((path / ".release.json").read_text())["manifest"] == manifest("b")
        monkeypatch.setattr(module.os, "replace", replace)
        (path / "compose.production.yaml").write_text("candidate-c")
        with pytest.raises(ValueError, match="HANDOFF_PENDING"):
            controller.release(manifest("c"))
        handoff.reconcile()
        assert handoff.runtime.events.count("prepare") == 1
        controller.rollback()
    state = json.loads((path / ".release.json").read_text())
    assert state["manifest"] == manifest("b")
    assert base64.b64decode(state["compose"]) == original


@pytest.mark.parametrize("successful_versions", [1, 2])
def test_failed_deploy_rollback_restores_immediately_previous_success(
    project, monkeypatch, successful_versions
):
    module, path = project
    with module.Project() as controller:
        handoff = seed_handoff(controller, "a")
        if successful_versions == 2:
            controller.release(manifest("b"))
        original = (path / "compose.production.yaml").read_bytes()
        good = manifest("a" if successful_versions == 1 else "b")
        prepare = handoff.runtime.prepare

        def fail(manifest, compose):
            raise subprocess.CalledProcessError(1, "docker up --wait")

        monkeypatch.setattr(handoff.runtime, "prepare", fail)
        with pytest.raises(subprocess.CalledProcessError):
            controller.release(manifest("c"))
        monkeypatch.setattr(handoff.runtime, "prepare", prepare)
        controller.rollback()
    assert json.loads((path / ".release.json").read_text())["manifest"] == good
    assert (path / "compose.production.yaml").read_bytes() == original


def test_failed_rollback_can_recover_current_successful_version(project, monkeypatch):
    module, path = project
    with module.Project() as controller:
        handoff = seed_handoff(controller, "a")
        controller.release(manifest("b"))
        prepare = handoff.runtime.prepare

        def fail(*args):
            raise subprocess.CalledProcessError(1, "docker up --wait")

        monkeypatch.setattr(handoff.runtime, "prepare", fail)
        with pytest.raises(subprocess.CalledProcessError):
            controller.rollback()
        assert handoff.state()["phase"] == "preparing"
        assert handoff.state()["active"]["manifest"] == manifest("b")
        monkeypatch.setattr(handoff.runtime, "prepare", prepare)
        controller.rollback()
    assert json.loads((path / ".release.json").read_text())["manifest"] == manifest("b")
    assert json.loads((path / ".handoff.json").read_text())["phase"] == "idle"
