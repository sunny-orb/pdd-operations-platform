"""固定宿主项目的复用启用入口；只exec现有worker，不部署或操作网络。"""

import importlib.util
import json
import os
import pwd
import re
import stat
import sys
from pathlib import Path

ROOT = Path("/home/songbing/pdd-operations-platform")
WORKER_UID = 10001


def controller():
    """加载前先限定自身目录、真实账号及控制器文件，拒绝链接转向其他项目。"""
    if (
        Path(__file__).parent != ROOT
        or pwd.getpwuid(os.geteuid()).pw_name != "songbing"
    ):
        raise ValueError("STORAGE_HOST_CONTEXT")
    for path in (*reversed(ROOT.parents), ROOT):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("STORAGE_HOST_DIRECTORY")
    path = ROOT / "manage.py"
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
    ):
        raise ValueError("STORAGE_HOST_CONTROLLER")
    spec = importlib.util.spec_from_file_location("storage_release_controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(action="preflight"):
    """固定三个动作；默认预检不写策略或数据库，启停也不启动新容器。"""
    if action not in {"preflight", "enable", "disable"}:
        raise ValueError("STORAGE_ACTION")
    m = controller()
    with m.Project() as project, project.lock():
        state, _ = project.state(".release.json")
        env = project.docker_environment(state["manifest"])
        runtime = m.ROOT / "runtime"
        info = runtime.lstat()
        m.require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == WORKER_UID
            and not info.st_mode & 0o022,
            "STORAGE_HOST_RUNTIME",
        )
        identifiers = project.docker(
            [
                "ps",
                "--filter",
                f"label=com.docker.compose.project={m.PROJECT}",
                "--filter",
                "label=com.docker.compose.service=worker",
                "--filter",
                "status=running",
                "--format",
                "{{.ID}}",
            ],
            env,
        ).split()
        m.require(
            len(identifiers) == 1 and re.fullmatch("[0-9a-f]{12,64}", identifiers[0]),
            "STORAGE_UNIQUE_WORKER_REQUIRED",
        )
        items = json.loads(project.docker(["inspect", identifiers[0]], env))
        m.require(isinstance(items, list) and len(items) == 1, "STORAGE_WORKER_INSPECT")
        item = items[0]
        config = item.get("Config", {})
        labels = config.get("Labels", {})
        m.require(
            re.fullmatch("[0-9a-f]{64}", item.get("Id", ""))
            and item["Id"].startswith(identifiers[0])
            and item.get("State", {}).get("Running") is True
            and config.get("Image") == state["manifest"]["worker"]
            and config.get("User") in ("app", "10001", "10001:10001")
            and config.get("WorkingDir") == "/app"
            and labels.get("com.docker.compose.project") == m.PROJECT
            and labels.get("com.docker.compose.service") == "worker"
            and labels.get("com.docker.compose.project.working_dir") == str(m.ROOT)
            and item.get("HostConfig", {}).get("ReadonlyRootfs") is True,
            "STORAGE_WRONG_WORKER",
        )
        # /app代码及runtime不能被第二个挂载覆盖；其他项目路径不得借子挂载进入。
        mounts = [
            v
            for v in item.get("Mounts", [])
            if v.get("Destination") == "/"
            or v.get("Destination", "").startswith("/app/")
            or v.get("Destination") == "/app"
        ]
        m.require(
            len(mounts) == 1
            and mounts[0].get("Destination") == "/app/var"
            and mounts[0].get("Type") == "bind"
            and mounts[0].get("Source") == str(runtime)
            and mounts[0].get("RW") is True,
            "STORAGE_WRONG_MOUNT",
        )
        return json.loads(
            project.docker(
                [
                    "exec",
                    "--user",
                    "10001:10001",
                    item["Id"],
                    "python",
                    "scripts/storage-policy.py",
                    action,
                ],
                env,
            )
        )


if __name__ == "__main__":
    try:
        if len(sys.argv) > 2:
            raise ValueError("STORAGE_ACTION")
        print(json.dumps(run(sys.argv[1] if len(sys.argv) == 2 else "preflight")))
    except Exception as exc:  # noqa: BLE001 — 不回显Docker stderr或环境中的凭据。
        print(
            "启用检查未完成："
            + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
