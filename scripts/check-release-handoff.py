"""真实本机Docker/Traefik/Chrome演练；只创建并清理唯一测试项目前缀的资源。

用 apps/api/.venv/bin/python scripts/check-release-handoff.py 运行。
不访问生产、数据库或上游；发布实现只在本进程注入本机路径和合成镜像。
"""

import base64
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOCKET = Path.home() / ".docker/run/docker.sock"
API_BASE = "pdd-operations-api:local"
NGINX_BASE = "nginx@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949"
TRAEFIK = "traefik:v3.7.19"
SERVER = r'''
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = os.environ.get("FIXTURE_VERSION", "unknown")
ACTIVE = 0
LOCK = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        global ACTIVE
        path = urlsplit(self.path).path
        if path == "/health/ready" and VERSION == "bad":
            self.send_error(503)
            return
        if path == "/api/v1/slow":
            with LOCK:
                ACTIVE += 1
            try:
                time.sleep(8)
                self.reply({"version": VERSION, "finished": True})
            finally:
                with LOCK:
                    ACTIVE -= 1
            return
        if path == "/api/v1/active":
            self.reply({"active": ACTIVE, "version": VERSION})
            return
        self.reply({"status": "ok", "version": VERSION,
                    "data": {"date_from": "2026-10-13", "date_to": "2026-11-12",
                             "recommended_from": "2026-10-13",
                             "default_publication_id": "fixture-weather",
                             "data_version": "fixture-version"},
                    "meta": {"data_version": "fixture-version"}})

    def reply(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
'''
LEGACY_NGINX = '''server {
  listen 8080;
  root /usr/share/nginx/html;
  location = /healthz { return 200 'ok'; }
  location /api/ { proxy_pass http://api:8000; proxy_read_timeout 20s; }
  location /health/ { proxy_pass http://api:8000; }
  location / { try_files $uri $uri/ /index.html; }
}
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Rehearsal:
    def __init__(self):
        self.name = "pdd-handoff-check-" + uuid.uuid4().hex[:10]
        self.docker = shutil.which("docker")
        require(self.docker and SOCKET.is_socket(), "本机Docker Desktop不可用")
        self.created_images = []
        self.networks = []
        self.root = Path(tempfile.mkdtemp(prefix=self.name + "-", dir=REPO / "var"))
        self.port = None
        self.results = []
        self.stop = threading.Event()
        self.samples = []
        self.failures = []
        self.before_switch = None
        self.after_observe = None
        self.drain_timeout_once = False
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
        )

    def run(self, args, *, binary=False, env=None):
        command = [self.docker, "--host", "unix://" + str(SOCKET), *args]
        if args[0] == "compose" and env is not None:
            # 发布实现的独立DOCKER_CONFIG不含Desktop插件链接；直接使用同安装的Compose。
            compose = Path(self.docker).resolve().parents[1] / "cli-plugins/docker-compose"
            require(compose.is_file(), "Docker Desktop Compose插件不存在")
            command = [str(compose), *args[1:]]
        local_environment = dict(os.environ if env is None else env)
        local_environment.pop("DOCKER_CONTEXT", None)
        local_environment["DOCKER_HOST"] = "unix://" + str(SOCKET)
        try:
            result = subprocess.run(
                command, cwd=self.root, env=local_environment,
                capture_output=True, text=not binary, check=True,
                timeout=180,
            )
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode(errors="replace") if binary else error.stderr
            self.event("fixture_docker_error", operation=args[:2], detail=detail[-3000:])
            raise
        return result.stdout

    def event(self, name, **details):
        value = {"stage": name, **details}
        self.results.append(value)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def request(self, path):
        require(self.port is not None, "测试入口尚未建立")
        with self.opener.open(f"https://localhost:{self.port}{path}", timeout=15) as response:
            return response.status, response.read()

    def wait(self, check, *, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                value = check()
                if value:
                    return value
            except (OSError, urllib.error.URLError, subprocess.CalledProcessError):
                pass
            time.sleep(0.1)
        raise RuntimeError("本机测试条件超时")

    def fixture(self, version):
        build = self.root / ("fixture-" + version)
        build.mkdir()
        suffix = ("d" if version == "bad" else version) * 8 + ".js"
        chunk, entry = "lazy-" + suffix, "entry-" + suffix
        (build / "server.py").write_text(SERVER)
        (build / "initialize.py").write_text('print(\'{"status":"already_initialized"}\')\n')
        html = (
            f"<!doctype html><meta charset=utf-8><title>fixture-{version}</title>"
            f"<h1>{version}</h1><button id=lazy>load old page module</button><output id=result></output>"
            f'<script type="module" src="/assets/{entry}"></script>'
        )
        (build / "index.html").write_text(html)
        (build / "assets").mkdir()
        (build / "assets" / chunk).write_text(f"export const value='old-{version}';\n")
        (build / "assets" / entry).write_text(
            "document.querySelector('#lazy').onclick=async()=>{"
            f"const module=await import('/assets/{chunk}');"
            "document.querySelector('#result').textContent=module.value;};"
        )
        (build / "nginx.conf").write_text(LEGACY_NGINX)
        images = {}
        for kind in ("api", "web"):
            image = f"{self.name}-{kind}:{version}"
            container = self.name + "-fixture-" + kind
            # 停止态容器复制后提交合成镜像，不执行基底程序，也不留下BuildKit缓存。
            arguments = (["--env", f"FIXTURE_VERSION={version}", API_BASE, "python", "/app/fixture.py"]
                         if kind == "api" else [NGINX_BASE])
            self.run(["create", "--name", container, "--network", "none", *arguments])
            files = (("server.py", "/app/fixture.py"),
                     ("initialize.py", "/app/scripts/init-deployment-db.py")) if kind == "api" else (
                         ("nginx.conf", "/etc/nginx/conf.d/default.conf"),
                         ("index.html", "/usr/share/nginx/html/index.html"),
                         ("assets", "/usr/share/nginx/html/"))
            for name, destination in files:
                self.run(["cp", str(build / name), container + ":" + destination])
            self.run(["commit", container, image])
            self.created_images.append(image)
            self.run(["rm", container])
            images[kind] = image
        return {"commit": {"a": "a", "b": "b", "c": "c", "bad": "d"}[version] * 40,
                "api": images["api"], "web": images["web"], "worker": images["api"]}

    def setup_traefik(self):
        try:
            self.run(["image", "inspect", TRAEFIK])
        except subprocess.CalledProcessError:
            self.run(["pull", TRAEFIK])
            self.created_images.append(TRAEFIK)
        digest = json.loads(self.run(["image", "inspect", TRAEFIK]))[0]["RepoDigests"]
        network = self.name + "-traefik"
        self.run(["network", "create", network])
        self.networks.append(network)
        self.run([
            "run", "-d", "--name", self.name + "-traefik", "--network", network,
            "-p", "127.0.0.1::443", "-v", f"{SOCKET}:/var/run/docker.sock:ro", TRAEFIK,
            "--providers.docker=true", "--providers.docker.exposedbydefault=false",
            f"--providers.docker.constraints=Label(`com.docker.compose.project`, `{self.name}`)",
            "--entrypoints.websecure.address=:443", "--log.level=ERROR",
        ])
        mapping = self.run(["port", self.name + "-traefik", "443/tcp"]).strip()
        require(mapping.startswith("127.0.0.1:"), "测试端口未限定本机")
        self.port = int(mapping.rsplit(":", 1)[1])
        self.event("traefik_ready", image_digests=digest, port=self.port)

    def compose(self, manifest):
        return {
            "services": {
                "api": {"image": manifest["api"], "networks": ["app"], "init": True,
                        "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"],
                                        "interval": "1s", "timeout": "2s", "retries": 3}},
                "web": {"image": manifest["web"], "networks": ["app", "traefik"],
                        "depends_on": {"api": {"condition": "service_healthy"}},
                        "labels": {"traefik.enable": "true", "traefik.docker.network": self.networks[0],
                                   "traefik.http.routers.pdd-operations.rule": "Host(`localhost`)",
                                   "traefik.http.routers.pdd-operations.entrypoints": "websecure",
                                   "traefik.http.routers.pdd-operations.tls": "true",
                                   "traefik.http.services.pdd-operations.loadbalancer.server.port": "8080"}},
            },
            "networks": {"app": {"name": self.name + "_app"},
                         "traefik": {"external": True, "name": self.networks[0]}},
        }

    def project(self, manifests):
        sys.path.insert(0, str(REPO / "deploy"))
        manage = load_module("pdd_handoff_check_manage", REPO / "deploy/manage.py")
        manage.ROOT, manage.PROJECT = self.root, self.name
        manage.DOMAIN, manage.ACCOUNT = "localhost", pwd.getpwuid(os.geteuid()).pw_name
        network = json.loads(self.run(["network", "inspect", self.name + "_app"]))[0]
        manage.APP_SUBNET = network["IPAM"]["Config"][0]["Subnet"]
        accepted = {key: {value[key] for value in manifests}
                    for key in ("commit", "api", "web", "worker")}
        fixture_images = {value[key] for value in manifests for key in ("api", "web")}

        def validate(value):
            require(set(value) == set(accepted) and all(
                value[key] in choices for key, choices in accepted.items()
            ), "测试清单必须匹配本次合成镜像")
            return value

        manage.validate_manifest = validate
        rehearsal = self

        class LocalProject(manage.Project):
            def load_module(self, name):
                module = super().load_module(name)
                if name == "handoff.py":
                    module.TRAEFIK_NETWORK = rehearsal.networks[0]
                return module

            def docker(self, args, env, binary=False):
                # 只替换本机Docker传输；真实容器、compose配置、挂载和健康检查均执行。
                if len(args) == 2 and args[0] == "pull" and args[1] in fixture_images:
                    # 合成镜像只存在本机；不推送或向镜像仓库请求这些测试标签。
                    return rehearsal.run(["image", "inspect", args[1]], binary=binary)
                return rehearsal.run(args, binary=binary, env={**os.environ, **env,
                                     "DOCKER_HOST": "unix://" + str(SOCKET)})

            def external_marker(self):
                return rehearsal.request("/.well-known/pdd-release")[1].decode().strip()

            def public_bytes(self, path, limit):
                status, data = rehearsal.request(path)
                require(status == 200 and len(data) <= limit, "本机公开产物探针失败或超限")
                return data

            def handoff(self):
                controller = super().handoff()
                if getattr(controller, "_fixture_observers", False):
                    return controller
                switch, observe = controller.runtime.switch, controller.runtime.observe
                drain = controller.runtime.drain

                def observed_switch(state, target):
                    if rehearsal.before_switch is not None:
                        rehearsal.before_switch()
                    return switch(state, target)

                def observed_marker(state):
                    value = observe(state)
                    if rehearsal.after_observe is not None:
                        rehearsal.after_observe(value)
                    return value

                def bounded_drain(state):
                    if not rehearsal.drain_timeout_once:
                        return drain(state)
                    rehearsal.drain_timeout_once = False
                    module = self.load_module("handoff.py")
                    previous_budget = module.WAIT_SECONDS
                    try:
                        # 只缩短测试等待预算；真正轮询旧nginx worker，不伪造超时或退出。
                        module.WAIT_SECONDS = 0
                        return drain(state)
                    finally:
                        module.WAIT_SECONDS = previous_budget

                controller.runtime.switch, controller.runtime.observe = observed_switch, observed_marker
                controller.runtime.drain = bounded_drain
                controller._fixture_observers = True
                return controller

        return LocalProject()

    def stage(self, baseline):
        for name in ("manage.py", "handoff.py", "assets.py", "storage.py", "release.sh", "rollback.sh"):
            shutil.copyfile(REPO / "deploy" / name, self.root / name)
        (self.root / ".docker").mkdir(mode=0o700)
        (self.root / ".docker/config.json").write_text("{}")
        (self.root / ".docker/config.json").chmod(0o600)
        (self.root / ".env.runtime").write_text("APP_ENV=production\n")
        (self.root / ".env.runtime").chmod(0o600)
        compose = json.dumps(self.compose(baseline)).encode()
        (self.root / "compose.production.yaml").write_bytes(compose)
        (self.root / ".release.json").write_text(json.dumps({"version": 1, "manifest": baseline,
                                                           "compose": base64.b64encode(compose).decode()}))
        self.networks.append(self.name + "_app")
        self.run(["compose", "--project-name", self.name, "-f", str(self.root / "compose.production.yaml"), "up", "-d", "--wait"])
        self.wait(lambda: self.request("/api/v1/fixture")[0] == 200)
        self.event("deployment_snapshot", sha256={
            name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            for name in ("manage.py", "handoff.py", "assets.py")
        })

    def sample(self):
        while not self.stop.is_set():
            for path in ("/", "/api/v1/fixture"):
                started = time.monotonic()
                try:
                    status, content = self.request(path)
                    require(status == 200 and content, "连续请求为空或非200")
                    self.samples.append((path, time.monotonic() - started))
                except (OSError, RuntimeError, ValueError) as error:
                    self.failures.append((path, type(error).__name__))
            self.stop.wait(0.05)

    def service_id(self, service):
        return self.run(["ps", "-q", "--filter", f"label=com.docker.compose.project={self.name}",
                         "--filter", f"label=com.docker.compose.service={service}"]).strip()

    def transition(self, project, browser, expected, action):
        page = browser.new_page()
        page.goto(f"https://localhost:{self.port}/", wait_until="networkidle")
        previous = page.locator("h1").inner_text()
        pending = None
        observed = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def slow_request():
                status, body = self.request("/api/v1/slow")
                return status, body, time.monotonic()

            def before_switch():
                nonlocal pending
                require(pending is None, "同一次切换意外执行两次")
                pending = pool.submit(slow_request)
                self.wait(lambda: json.loads(self.request("/api/v1/active")[1])["active"] > 0)

            self.before_switch = before_switch
            self.after_observe = lambda value: observed.append((value, time.monotonic()))
            try:
                started = time.monotonic()
                action()
                require(pending is not None, "切换未经过真实runtime.switch")
                status, body, finished = pending.result(timeout=20)
            finally:
                self.before_switch = self.after_observe = None
        cuts = [timestamp for value, timestamp in observed if value == expected * 40]
        require(cuts and min(cuts) < finished, "慢请求未跨越实际外部切流，演练证据不足")
        require(status == 200 and json.loads(body)["version"] == previous, "切换中旧API请求被中断或串版")
        self.wait(lambda: json.loads(self.request("/api/v1/fixture")[1])["version"] == expected)
        with page.expect_response(lambda response: response.url.endswith(
            "lazy-" + previous[0] * 8 + ".js"
        )) as lazy_response:
            page.locator("#lazy").click()
        require(lazy_response.value.status == 200, "旧页懒加载资源不是200")
        page.wait_for_function("document.querySelector('#result').textContent.startsWith('old-')")
        require(page.locator("#result").inner_text() == "old-" + previous, "旧页懒加载失败或串版")
        require(project.external_marker() == expected * 40, "入口版本标记不一致")
        page.close()
        self.event("transition_passed", previous=previous, current=expected,
                   elapsed_seconds=round(time.monotonic() - started, 3), old_request_finished=True,
                   old_page_lazy_import=True)

    def exercise(self):
        from playwright.sync_api import sync_playwright

        self.setup_traefik()
        manifests = {version: self.fixture(version) for version in ("a", "b", "c", "bad")}
        self.stage(manifests["a"])
        thread = threading.Thread(target=self.sample, daemon=True)
        thread.start()
        previous_umask = os.umask(0o077)
        try:
            with self.project(list(manifests.values())) as project, sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(ignore_https_errors=True)
                try:
                    self.transition(project, context, "b", lambda: project.release(manifests["b"]))
                    gateway = self.service_id("gateway")
                    require(gateway, "首次接管未建立gateway")
                    self.transition(project, context, "c", lambda: project.release(manifests["c"]))
                    require(self.service_id("gateway") == gateway, "后续升级重建了gateway")
                    try:
                        project.release(manifests["bad"])
                    except subprocess.CalledProcessError:
                        candidates = [identifier for slot in ("api_blue", "api_green")
                                      if (identifier := self.service_id(slot))]
                        inspected = json.loads(self.run(["inspect", *candidates]))
                        unhealthy = [value for value in inspected
                                     if value["Config"]["Image"] == manifests["bad"]["api"]
                                     and value["State"].get("Health", {}).get("Status") == "unhealthy"]
                        require(len(unhealthy) == 1, "候选失败不是实际Docker健康检查失败")
                        self.event("unhealthy_candidate_rejected")
                    else:
                        raise RuntimeError("不健康候选未拒绝")
                    require(project.external_marker() == "c" * 40, "失败候选改变了当前入口")
                    project.rollback()
                    require(project.external_marker() == "c" * 40, "清理失败候选改变了当前入口")
                    self.transition(project, context, "b", project.rollback)
                    require(self.service_id("gateway") == gateway, "回退重建了gateway")

                    def fail_drain():
                        self.drain_timeout_once = True
                        try:
                            project.release(manifests["c"])
                        except ValueError as error:
                            require(str(error) == "DRAIN_TIMEOUT", "排空注入未命中预期阶段")
                            self.event("drain_timeout_observed")
                        else:
                            raise RuntimeError("真实旧worker排空未触发测试等待上限")

                    self.transition(project, context, "c", fail_drain)
                    self.transition(project, context, "b", project.rollback)
                    require(self.service_id("gateway") == gateway, "排空失败回退重建了gateway")
                finally:
                    context.close()
                    browser.close()
        finally:
            os.umask(previous_umask)
            self.stop.set()
            thread.join(timeout=20)
            self.event("traffic_observed", request_samples=len(self.samples),
                       failures=len(self.failures))
        require(not self.failures, "连续入口请求失败: " + json.dumps(self.failures))
        require(len(self.samples) >= 10, "连续请求样本不足")
        self.event("all_checks_passed", request_samples=len(self.samples), failures=0,
                   max_request_seconds=round(max(elapsed for _, elapsed in self.samples), 3))

    def cleanup(self):
        self.stop.set()
        errors = []
        try:
            rows = [json.loads(line) for line in self.run(
                ["container", "ls", "-a", "--format", "json"]
            ).splitlines()]
            for row in rows:
                if row["Names"].startswith(self.name + "-"):
                    try:
                        self.run(["rm", "-f", row["ID"]])
                    except subprocess.SubprocessError:
                        errors.append("container")
        except (ValueError, subprocess.SubprocessError):
            errors.append("container_inventory")
        for network in reversed(self.networks):
            try:
                self.run(["network", "rm", network])
            except subprocess.SubprocessError:
                errors.append("network")
        for image in reversed(self.created_images):
            try:
                self.run(["image", "rm", image])
            except subprocess.SubprocessError:
                errors.append("image")
        shutil.rmtree(self.root)
        require(not errors, "测试资源清理未完成: " + ",".join(errors))
        self.event("fixture_resources_removed")


def main():
    require(len(sys.argv) == 1, "本演练不接受生产地址或项目参数")
    rehearsal = Rehearsal()
    try:
        rehearsal.exercise()
    finally:
        rehearsal.cleanup()


if __name__ == "__main__":
    main()
