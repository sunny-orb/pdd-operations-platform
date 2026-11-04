"""Actions侧只接受本仓库成功构建；固定SSH端点传输白名单，禁止任意目标参数。"""

import argparse
import base64
import importlib.util
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "release_controller", DIRECTORY / "manage.py"
)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def verify_run(run_id):
    controller.require(re.fullmatch("[1-9][0-9]*", run_id), "INVALID_RUN_ID")
    result = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/sunny-orb/pdd-operations-platform/actions/runs/{run_id}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    run = json.loads(result.stdout)
    controller.require(
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("path") == ".github/workflows/build.yml"
        and run.get("event") == "workflow_dispatch"
        and run.get("repository", {}).get("full_name")
        == "sunny-orb/pdd-operations-platform"
        and run.get("head_repository", {}).get("full_name")
        == "sunny-orb/pdd-operations-platform",
        "UNTRUSTED_BUILD_RUN",
    )
    sha = run.get("head_sha", "")
    controller.require(re.fullmatch("[0-9a-f]{40}", sha), "INVALID_BUILD_SHA")
    return sha


def send(manifest_path, expected_commit):
    manifest = controller.validate_manifest(json.loads(Path(manifest_path).read_text()))
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    controller.require(
        manifest["commit"] == expected_commit == actual, "BUILD_COMMIT_MISMATCH"
    )
    payload = {
        "manifest": manifest,
        "files": {
            name: base64.b64encode((DIRECTORY / name).read_bytes()).decode()
            for name in controller.UPLOADS
        },
    }
    # 源自已校验构建提交的控制器直接经python -c执行；先检查再写白名单文件。
    command = (
        "/usr/bin/python3 -I -B -c "
        + shlex.quote((DIRECTORY / "manage.py").read_text())
        + " receive"
    )
    subprocess.run(
        [
            "ssh",
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-p",
            "10022",
            "songbing@121.40.167.37",
            command,
        ],
        input=json.dumps(payload),
        text=True,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    verify = commands.add_parser("verify-run")
    verify.add_argument("run_id")
    upload = commands.add_parser("send")
    upload.add_argument("manifest")
    upload.add_argument("expected_commit")
    args = parser.parse_args()
    if args.action == "verify-run":
        sha = verify_run(args.run_id)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"commit={sha}\nrun_id={args.run_id}\n")
    else:
        send(args.manifest, args.expected_commit)


if __name__ == "__main__":
    main()
