"""安装本机launchd分钟检查入口；源时间配置由sync-project.py读取。"""

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "cn.kocotree.pdd-operations.sync"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    folder = Path.home() / "Library/LaunchAgents"
    target = folder / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain + "/" + LABEL], capture_output=True, check=False
    )
    if args.remove:
        target.unlink(missing_ok=True)
        print("已移除本机同步定时器，来源及业务版本未删除")
        return
    folder.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "var/project-sync"
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("scheduler.log", "scheduler-error.log"):
        path = logs / name
        path.touch(exist_ok=True)
        path.chmod(0o600)
    data = {
        "Label": LABEL,
        "WorkingDirectory": str(ROOT),
        "ProgramArguments": [
            str(ROOT / "apps/api/.venv/bin/python"),
            str(ROOT / "scripts/sync-project.py"),
            "--scheduled",
        ],
        "StartInterval": 60,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "StandardOutPath": str(logs / "scheduler.log"),
        "StandardErrorPath": str(logs / "scheduler-error.log"),
    }
    with target.open("wb") as f:
        plistlib.dump(data, f)
    target.chmod(0o600)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    print(
        "本机定时器已加载；每日钟点从config/sync.toml热读取；睡眠/关机后本日漏点合并补跑"
    )


if __name__ == "__main__":
    main()
