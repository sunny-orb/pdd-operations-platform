"""宿主启用入口只检查/exec本项目已有worker，不拉镜像或操作网络。"""

import base64
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from tests.test_deployment_release import manifest, project

__all__ = ["project"]
ROOT = Path(__file__).resolve().parents[3]


def load():
    spec = importlib.util.spec_from_file_location("storage_host", ROOT / "deploy/storage.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def host(project, monkeypatch):
    controller, path = project
    m = load()
    monkeypatch.setattr(m, "controller", lambda: controller)
    (path / "runtime").mkdir(mode=0o700)
    monkeypatch.setattr(m, "WORKER_UID", (path / "runtime").stat().st_uid)
    (path / ".release.json").write_text(
        json.dumps(
            {
                "version": 1,
                "manifest": manifest(),
                "compose": base64.b64encode(b"name: fixed").decode(),
            }
        )
    )
    item = {
        "Id": "a" * 64,
        "State": {"Running": True},
        "Config": {
            "Image": manifest()["worker"],
            "User": "app",
            "WorkingDir": "/app",
            "Labels": {
                "com.docker.compose.project": "pdd-operations-production",
                "com.docker.compose.service": "worker",
                "com.docker.compose.project.working_dir": str(path),
            },
        },
        "Mounts": [
            {"Type": "bind", "Source": str(path / "runtime"), "Destination": "/app/var", "RW": True}
        ],
        "HostConfig": {"ReadonlyRootfs": True},
    }
    calls = []

    def docker(self, args, env):
        calls.append(args)
        if args[0] == "ps":
            return "a" * 64 + "\n"
        if args[0] == "inspect":
            return json.dumps([item])
        return '{"production_delete_enabled":false}'

    monkeypatch.setattr(controller.Project, "docker", docker)
    return m, path, item, calls


def test_preflight_is_default_and_enable_only_executes_verified_worker(host):
    m, path, _, calls = host
    assert m.run()["production_delete_enabled"] is False
    assert calls[-1] == [
        "exec",
        "--user",
        "10001:10001",
        "a" * 64,
        "python",
        "scripts/storage-policy.py",
        "preflight",
    ]
    m.run("enable")
    assert calls[-1][-1] == "enable"
    assert all(c[0] in {"ps", "inspect", "exec"} for c in calls)
    assert list(path.glob("runtime/*")) == []


@pytest.mark.parametrize(
    "bad",
    [
        "other-project",
        "other-mount",
        "wrong-image",
        "not-running",
        "writable-root",
        "symlink-runtime",
    ],
)
def test_unapproved_worker_or_runtime_is_rejected_before_exec(host, bad):
    m, path, item, calls = host
    if bad == "other-project":
        item["Config"]["Labels"]["com.docker.compose.project"] = "other"
    elif bad == "other-mount":
        item["Mounts"][0]["Source"] = "/home/other/runtime"
    elif bad == "wrong-image":
        item["Config"]["Image"] = "other:latest"
    elif bad == "not-running":
        item["State"]["Running"] = False
    elif bad == "writable-root":
        item["HostConfig"]["ReadonlyRootfs"] = False
    else:
        shutil.rmtree(path / "runtime")
        (path / "runtime").symlink_to(path, target_is_directory=True)
    with pytest.raises(ValueError):
        m.run("enable")
    assert not any(c[0] == "exec" for c in calls)


def test_local_invocation_cannot_control_server():
    with pytest.raises(ValueError):
        load().run("enable")


@pytest.mark.parametrize(
    "destination", ["/app", "/app/scripts", "/app/var/storage-policy.json", "/app/var/child"]
)
def test_overlapping_code_or_runtime_mount_cannot_override_verified_worker(host, destination):
    m, _, item, calls = host
    item["Mounts"].append(
        {"Type": "bind", "Source": "/home/other", "Destination": destination, "RW": True}
    )
    with pytest.raises(ValueError, match="STORAGE_WRONG_MOUNT"):
        m.run("enable")
    assert not any(c[0] == "exec" for c in calls)
