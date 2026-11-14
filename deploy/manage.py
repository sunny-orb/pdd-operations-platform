"""固定项目的发布控制器；不创建目录、登录Docker、初始化或迁移数据库。"""

import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import types
import urllib.request
import uuid
from pathlib import Path

ROOT = Path("/home/songbing/pdd-operations-platform")
ACCOUNT = "songbing"
PROJECT = "pdd-operations-production"
DOMAIN = "pdd-operations.kktree.cn"
APP_SUBNET = "10.254.240.0/28"
UPLOADS = (
    "compose.production.yaml",
    "release.sh",
    "rollback.sh",
    "manage.py",
    "storage.py",
    "handoff.py",
    "assets.py",
)
STATE = (
    ".release.json",
    ".release.previous.json",
    ".release.recovery.json",
    ".compose.rollback.yaml",
    ".release.lock",
    ".handoff.json",
    ".compose.handoff.json",
    ".asset-extraction.json",
)
FILES = UPLOADS + STATE + (".env", ".env.runtime", ".htpasswd", "compose.sync.yaml")


class BoundaryError(ValueError):
    """目标或配置越界，停止操作。"""


def require(condition, message):
    if not condition:
        raise BoundaryError(message)


def validate_manifest(value):
    """镜像必须来自本项目且不可变；清单绑定同一次源码提交。"""
    require(
        isinstance(value, dict) and set(value) == {"commit", "api", "web", "worker"},
        "INVALID_MANIFEST",
    )
    require(
        isinstance(value["commit"], str)
        and re.fullmatch("[0-9a-f]{40}", value["commit"]),
        "INVALID_COMMIT",
    )
    for name in ("api", "web", "worker"):
        require(
            isinstance(value[name], str)
            and re.fullmatch(
                rf"ghcr.io/sunny-orb/pdd-operations-platform-{name}@sha256:[0-9a-f]{{64}}",
                value[name],
            ),
            "INVALID_IMAGE",
        )
    return value


class Project:
    def __init__(self):
        self.fd = None
        self._modules = {}
        self._handoff = None

    @property
    def root(self):
        return ROOT

    @property
    def project_name(self):
        return PROJECT

    @property
    def domain(self):
        return DOMAIN

    @property
    def app_subnet(self):
        return APP_SUBNET

    @staticmethod
    def validate_manifest(value):
        return validate_manifest(value)

    def load_module(self, name):
        """从已校验项目文件读取源码；远端stdin入口不依赖sys.path或__file__。"""
        require(name in ("handoff.py", "assets.py"), "UNAPPROVED_DEPLOY_MODULE")
        if name not in self._modules:
            module = types.ModuleType("pdd_release_" + name[:-3])
            module.__file__ = str(ROOT / name)
            # 来源仅为上述白名单、经fd校验读取的受信发布源码，不接收任意代码输入。
            exec(compile(self.read(name), module.__file__, "exec"), module.__dict__)  # noqa: S102
            self._modules[name] = module
        return self._modules[name]

    def handoff(self):
        if self._handoff is None:
            self._handoff = self.load_module("handoff.py").Handoff(self)
        return self._handoff

    def external_marker(self):
        """固定生产HTTPS入口核对实际接管版本；不接受CLI或环境覆盖。"""
        value = (
            self.public_bytes("/.well-known/pdd-release", 128).decode("ascii").strip()
        )
        require(re.fullmatch("[0-9a-f]{40}", value), "INVALID_PUBLIC_RELEASE_MARKER")
        return value

    def public_bytes(self, path, limit):
        """仅固定HTTPS站点的公开文件；测试可替换方法，生产无可变URL入口。"""
        require(
            path in ("/index.html", "/.well-known/pdd-release")
            or (
                path.startswith("/assets/")
                and ".." not in path
                and re.fullmatch(r"/[A-Za-z0-9_@.+/-]+", path)
            ),
            "INVALID_PUBLIC_PROBE_PATH",
        )
        request = urllib.request.Request(
            "https://" + DOMAIN + path, headers={"Cache-Control": "no-cache"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            require(response.status == 200, "PUBLIC_ASSET_UNAVAILABLE")
            value = response.read(limit + 1)
        require(len(value) <= limit, "PUBLIC_ASSET_TOO_LARGE")
        return value

    def verify_public_files(self, expected):
        for path, digest in expected.items():
            require(re.fullmatch("[0-9a-f]{64}", digest), "INVALID_PUBLIC_ASSET_DIGEST")
            actual = hashlib.sha256(
                self.public_bytes(path, 64 * 1024 * 1024)
            ).hexdigest()
            require(actual == digest, "PUBLIC_ASSET_DIGEST_MISMATCH")

    def __enter__(self):
        uid = os.geteuid()
        require(
            pwd.getpwuid(uid).pw_name == ACCOUNT and os.getuid() == uid, "WRONG_ACCOUNT"
        )
        # 逐层lstat；不能以真实路径检查代替符号链接检查。
        for path in (*reversed(ROOT.parents), ROOT):
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode), "UNSAFE_DIRECTORY")
            require(not info.st_mode & 0o022, "WRITABLE_DIRECTORY")
        require(ROOT.resolve(strict=True) == ROOT, "WRONG_REALPATH")
        require(ROOT.stat().st_uid == uid, "WRONG_OWNER")
        self.fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.check_files()
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)

    def check_file(self, name, *, required=False, private=False):
        require(name in FILES, "UNKNOWN_FILE")
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            require(not required, "MISSING_FILE: " + name)
            return False
        require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_FILE: " + name
        )
        require(
            info.st_uid == os.geteuid() and not info.st_mode & 0o022,
            "UNSAFE_FILE_OWNER: " + name,
        )
        if private:
            require(not info.st_mode & 0o077, "PRIVATE_FILE_MODE: " + name)
        return True

    def check_files(self):
        for name in FILES:
            self.check_file(name, private=name in (".env.runtime", ".htpasswd"))
        # 不访问来源数据目录；仅检查本次发布会使用的凭据目录。
        for name in (".docker",):
            path = ROOT / name
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == os.geteuid()
                    and not info.st_mode & 0o022,
                    "UNSAFE_SUBDIRECTORY: " + name,
                )

    def read(self, name):
        self.check_file(name, required=True)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    def write(self, name, data):
        self.check_file(name)
        temporary = ".pending-" + uuid.uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def remove(self, name):
        require(name == ".asset-extraction.json", "UNAPPROVED_STATE_REMOVAL")
        if self.check_file(name):
            os.unlink(name, dir_fd=self.fd)
            os.fsync(self.fd)

    def lock(self):
        self.check_file(".release.lock")
        fd = os.open(
            ".release.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "r+")

    def docker_environment(self, manifest):
        self.check_files()
        self.check_file(".env.runtime", required=True, private=True)
        # 旧口令文件仅为历史版本回退保留；存在时继续检查其权限和链接。
        self.check_file(".htpasswd", private=True)
        config_dir = ROOT / ".docker"
        info = config_dir.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077,
            "UNSAFE_DOCKER_DIRECTORY",
        )
        config = config_dir / "config.json"
        info = config.lstat()
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077,
            "UNSAFE_DOCKER_CONFIG",
        )
        directory_fd = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                "config.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
            )
            with os.fdopen(descriptor) as stream:
                settings = json.load(stream)
        finally:
            os.close(directory_fd)
        # 禁止全局凭据助手、context、cliPluginsExtraDirs等改变执行边界。
        require(
            set(settings) <= {"auths"}
            and set(settings.get("auths", {})) <= {"ghcr.io"},
            "UNSUPPORTED_DOCKER_CONFIG",
        )
        # 项目插件搜索路径内不允许注入Docker可执行插件。
        require(
            not (config_dir / "cli-plugins").exists()
            and not (config_dir / "cli-plugins").is_symlink(),
            "DOCKER_PLUGIN_OVERRIDE",
        )
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(ROOT),
            "DOCKER_CONFIG": str(config_dir),
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "API_IMAGE": manifest["api"],
            "WEB_IMAGE": manifest["web"],
            "APP_DOMAIN": DOMAIN,
            "RUNTIME_ENV_FILE": str(ROOT / ".env.runtime"),
            "WEB_HTPASSWD_FILE": str(ROOT / ".htpasswd"),
        }

    def docker(self, args, env, *, binary=False):
        if binary:
            require(
                len(args) == 3
                and args[0] == "cp"
                and args[2] == "-"
                and re.fullmatch(r"[0-9a-f]{64}:/usr/share/nginx/html/\.", args[1]),
                "UNAPPROVED_BINARY_DOCKER_OPERATION",
            )
        result = subprocess.run(
            [
                "/usr/bin/docker",
                "--config",
                str(ROOT / ".docker"),
                "--host",
                "unix:///var/run/docker.sock",
                *args,
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=not binary,
        )
        if binary:
            require(len(result.stdout) <= 72 * 1024 * 1024, "STATIC_ARCHIVE_TOO_LARGE")
        return result.stdout

    def state(self, name):
        value = json.loads(self.read(name))
        require(
            isinstance(value, dict)
            and set(value) == {"version", "manifest", "compose"}
            and value["version"] == 1,
            "INVALID_RELEASE_STATE",
        )
        validate_manifest(value["manifest"])
        compose = base64.b64decode(value["compose"], validate=True)
        require(0 < len(compose) <= 200_000, "INVALID_STATE_COMPOSE")
        return value, compose

    def release(self, manifest):
        validate_manifest(manifest)
        self.docker_environment(manifest)
        self.handoff().release(manifest, self.read("compose.production.yaml"))

    def rollback(self):
        self.handoff().rollback()

    def receive(self, payload):
        require(
            isinstance(payload, dict) and set(payload) == {"files", "manifest"},
            "INVALID_UPLOAD",
        )
        manifest = validate_manifest(payload["manifest"])
        require(
            isinstance(payload["files"], dict)
            and set(payload["files"]) == set(UPLOADS),
            "UPLOAD_ALLOWLIST",
        )
        decoded = {
            name: base64.b64decode(value, validate=True)
            for name, value in payload["files"].items()
        }
        require(
            all(0 < len(value) <= 200_000 for value in decoded.values()), "UPLOAD_SIZE"
        )
        # 所有文件、私密配置及旧版本状态完整核对后才允许上传落盘。
        self.docker_environment(manifest)
        if self.check_file(".release.json"):
            self.state(".release.json")
        for name, value in decoded.items():
            self.write(name, value)
        self.release(manifest)


def main():
    try:
        require(
            len(sys.argv) == 2
            and sys.argv[1] in ("receive", "release", "rollback", "reconcile", "check"),
            "INVALID_ACTION",
        )
        with Project() as project:
            if sys.argv[1] == "check":
                print("项目目录边界检查通过；未执行发布")
                return 0
            with project.lock():
                if sys.argv[1] == "rollback":
                    project.rollback()
                elif sys.argv[1] == "reconcile":
                    project.handoff().reconcile()
                else:
                    value = json.loads(sys.stdin.read(1_000_001))
                    if sys.argv[1] == "receive":
                        project.receive(value)
                    else:
                        project.release(value)
        print("应用就绪检查通过；数据库未初始化或迁移，请继续HTTPS和业务验收")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # 不输出Docker stderr或凭据；具体边界错误仅包含固定文件名。
        print(
            str(error) if isinstance(error, BoundaryError) else type(error).__name__,
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
