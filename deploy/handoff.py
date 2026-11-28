"""项目入口热加载与两代API交接；不改共享代理、不迁移数据库、不触发同步。"""

import base64
import json
import os
import re
import subprocess
import time
import uuid
from html.parser import HTMLParser

TRAEFIK_NETWORK = "traefik-network"
WAIT_SECONDS = 120
POLL_SECONDS = 1
MARKER = "/.well-known/pdd-release"

# 只读业务预热在候选API内执行；失败输出只含错误类别，不打印经营响应/环境。
BUSINESS_PROBE = r"""
import datetime,json,urllib.error,urllib.parse,urllib.request
def get(path, query=None):
    url='http://127.0.0.1:8000'+path
    if query: url+='?'+urllib.parse.urlencode(query)
    with urllib.request.urlopen(url,timeout=10) as response:
        value=json.load(response)
    if not isinstance(value,dict) or 'error' in value: raise ValueError('INVALID_BUSINESS_RESPONSE')
    return value
def window(value, days=30):
    data=value['data']; end=datetime.date.fromisoformat(data['date_to'])
    start=max(datetime.date.fromisoformat(data['date_from']),end-datetime.timedelta(days=days-1))
    return dict(date_from=str(start),date_to=str(end),data_version=value['meta']['data_version'])
def compatible(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k=='status' and isinstance(v,str) and (v=='pending_recompute' or v.startswith('incompatible')):
                raise ValueError('SCORE_NOT_COMPATIBLE')
            compatible(v)
    elif isinstance(value,list):
        for item in value: compatible(item)
def run():
    get('/health/ready')
    get('/api/v1/published-versions')
    q=window(get('/api/v1/products/meta'), days=31)
    get('/api/v1/products/overview',q)
    compatible(get('/api/v1/products/scores',q))
    q=window(get('/api/v1/promotion/meta',dict(mode='product')));q['mode']='product'
    get('/api/v1/promotion/overview',dict(q,detail='display'))
    get('/api/v1/promotion/diagnosis',dict(q,detail='summary'))
    get('/api/v1/sales/filters')
    get('/api/v1/data-status')
    w=get('/api/v1/weather/meta')['data']
    if not w.get('default_publication_id') or not w.get('data_version'): raise ValueError('WEATHER_NOT_READY')
    get('/api/v1/weather/publication',dict(publication_id=w['default_publication_id'],data_version=w['data_version']))
for attempt in range(2):
    try:
        run()
        break
    except urllib.error.HTTPError as error:
        if error.code != 409 or attempt:
            raise
print('BUSINESS_PREWARM_OK')
"""

WORKERS = r"""for f in /proc/[0-9]*/stat; do
p=${f%/stat}; a=$(tr '\000' ' ' < "$p/cmdline" 2>/dev/null) || continue
case "$a" in "nginx: worker process"*) awk '{print $1 ":" $22}' "$f";; esac
done"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def summarize_database_errors(logs):
    """只解析既有数据库失败格式；忽略path/type原串、SQL及其他日志，不按耗时猜原因。"""
    stages = {
        "DATABASE_BUSY": "pool_checkout",
        "QUERY_TIMEOUT": "sql_execute",
        "DATABASE_UNAVAILABLE": "connection",
        "DATABASE_ERROR": "sql_execute",
    }
    number = r"\d{1,9}(?:\.\d{1,6})?"
    nullable = rf"(?:{number}|unavailable)"
    pattern = re.compile(
        r"request_id=(?P<request_id>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|unavailable) "
        r"path=\S{1,200} code=(?P<code>[A-Z_]+) type=\S{1,256} "
        r"stage=(?P<stage>[a-z_]+) "
        rf"request_elapsed_ms=(?P<request_elapsed_ms>{number}) "
        rf"sql_elapsed_ms=(?P<sql_elapsed_ms>{nullable}) "
        rf"sql_total_ms=(?P<sql_total_ms>{nullable})"
    )
    groups, samples, matched = {}, [], 0
    for line in logs.splitlines()[-1000:]:
        if len(line) > 2048 or "数据库请求失败 " not in line:
            continue
        timestamp = re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z ", line)
        if timestamp is None:
            continue
        result = pattern.fullmatch(line.split("数据库请求失败 ", 1)[1])
        if result is None:
            continue
        values = result.groupdict()
        code, stage = values["code"], values["stage"]
        if stages.get(code) != stage:
            continue
        matched += 1
        sample = {
            "timestamp": timestamp.group().strip(),
            "request_id": values["request_id"],
            "code": code,
            "stage": stage,
        }
        group = groups.setdefault(code, {"code": code, "stage": stage, "count": 0})
        group["count"] += 1
        for name in ("request_elapsed_ms", "sql_elapsed_ms", "sql_total_ms"):
            value = None if values[name] == "unavailable" else float(values[name])
            sample[name] = value
            previous = group.get("max_" + name)
            group["max_" + name] = (
                max(previous or 0, value) if value is not None else previous
            )
        samples.append(sample)
        samples = samples[-5:]
    return {
        "window_seconds": 600,
        "line_limit": 1000,
        "matched": matched,
        "groups": [groups[code] for code in sorted(groups)],
        "samples": samples,
    }


class EntryScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("src"):
            self.sources.append(dict(attrs)["src"])


def nginx_config(record):
    """一份配置同时绑定静态版本和API代际；HUP前旧worker仍使用旧配置。"""
    commit = record["manifest"]["commit"]
    require(re.fullmatch("[0-9a-f]{40}", commit), "INVALID_RELEASE_COMMIT")
    require(record["slot"] in ("legacy", "blue", "green"), "INVALID_API_SLOT")
    upstream = "api" if record["slot"] == "legacy" else "api_" + record["slot"]
    return f"""server {{
    listen 8080;
    server_name _;
    server_tokens off;
    root /srv/public/releases/{commit};
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_min_length 1024;
    gzip_comp_level 5;
    gzip_types application/javascript application/json application/manifest+json application/xml image/svg+xml text/css text/javascript text/plain text/xml;
    location = /healthz {{ access_log off; return 200 'ok'; }}
    location = {MARKER} {{ add_header Cache-Control "no-store" always; return 200 '{commit}'; }}
    location /api/v1/assistant/ {{
        resolver 127.0.0.11 valid=1s;
        set $api http://{upstream}:8000;
        proxy_pass $api;
        proxy_set_header Host $host;
        proxy_set_header Authorization "";
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_hide_header Cache-Control;
        add_header Cache-Control "no-store" always;
        proxy_buffering off;
        client_max_body_size 256k;
        proxy_read_timeout 120s;
    }}
    location /api/ {{
        resolver 127.0.0.11 valid=1s;
        set $api http://{upstream}:8000;
        proxy_pass $api;
        proxy_set_header Host $host;
        proxy_set_header Authorization "";
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_hide_header Cache-Control;
        add_header Cache-Control "no-store" always;
        proxy_read_timeout 30s;
    }}
    location ~ "^/assets/[^/]+-[A-Za-z0-9_-]{{8}}\\.(?:js|css)$" {{
        root /srv/public;
        try_files $uri =404;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }}
    location /assets/ {{ root /srv/public; try_files $uri =404; add_header Cache-Control "no-cache" always; }}
    location / {{ try_files $uri $uri/ /index.html; add_header Cache-Control "no-cache" always; }}
}}
""".encode()


def compose_config(project, manifest, *, slot, gateway_image):
    """生成封闭Compose，不执行上传的任意服务定义，网络只引用既有对象。"""
    require(slot in ("blue", "green"), "INVALID_API_SLOT")
    logging = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    base = {
        "image": manifest["api"],
        "env_file": [{"path": str(project.root / ".env.runtime"), "format": "raw"}],
        "environment": {"APP_ENV": "production"},
        "networks": ["app"],
        # 只有gateway是入口；不依赖共享Docker provider的exposedByDefault设置。
        "labels": {"traefik.enable": "false"},
        "init": True,
        "logging": logging,
    }
    api = dict(
        base,
        restart="unless-stopped",
        healthcheck={
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=8)",
            ],
            "interval": "5s",
            "timeout": "10s",
            "retries": 5,
            "start_period": "20s",
        },
    )
    gateway = {
        "image": gateway_image,
        "restart": "unless-stopped",
        "networks": ["app", "traefik"],
        "logging": logging,
        "volumes": [
            {
                "type": "bind",
                "source": str(project.root / source),
                "target": target,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
            for source, target in (
                ("public", "/srv/public"),
                ("gateway", "/etc/nginx/conf.d"),
            )
        ],
        "healthcheck": {
            "test": [
                "CMD",
                "wget",
                "-q",
                "-O",
                "/dev/null",
                "http://127.0.0.1:8080/index.html",
            ],
            "interval": "2s",
            "timeout": "5s",
            "retries": 5,
        },
        "labels": {
            "traefik.enable": "true",
            "traefik.docker.network": TRAEFIK_NETWORK,
            "traefik.http.routers.pdd-operations-entry.rule": f"Host(`{project.domain}`)",
            "traefik.http.routers.pdd-operations-entry.entrypoints": "websecure",
            "traefik.http.routers.pdd-operations-entry.tls": "true",
            "traefik.http.routers.pdd-operations-entry.tls.certresolver": "myresolver",
            "traefik.http.routers.pdd-operations-entry.priority": "100",
            "traefik.http.routers.pdd-operations-entry.service": "pdd-operations-entry",
            "traefik.http.services.pdd-operations-entry.loadbalancer.server.port": "8080",
        },
    }
    return {
        "name": project.project_name,
        "services": {
            "api_" + slot: api,
            "gateway": gateway,
            "database": dict(
                base,
                profiles=["maintenance"],
                command=["python", "scripts/init-deployment-db.py"],
                restart="no",
            ),
        },
        "networks": {
            "app": {"external": True, "name": project.project_name + "_app"},
            "traefik": {"external": True, "name": TRAEFIK_NETWORK},
        },
    }


class Runtime:
    """所有Docker、公开探针与项目文件操作通过已验证Project边界。"""

    def __init__(self, project):
        self.project = project
        self.assets_module = project.load_module("assets.py")
        self.assets = self.assets_module.AssetStore(project.root)
        self.env = None
        self.network_snapshot = None

    def docker(self, args, *, binary=False):
        require(self.env is not None, "RUNTIME_NOT_INITIALIZED")
        return self.project.docker(args, self.env, binary=binary)

    def use(self, manifest):
        self.manifest = manifest
        self.env = self.project.docker_environment(manifest)

    def services(self, service, *, all_containers=True):
        args = ["ps"] + (["-a"] if all_containers else [])
        result = self.docker(
            [
                *args,
                "--filter",
                "label=com.docker.compose.project=" + self.project.project_name,
                "--filter",
                "label=com.docker.compose.service=" + service,
                "--format",
                "{{.ID}}",
            ]
        )
        return result.split()

    def inspect(self, identifier, service, image):
        require(re.fullmatch("[0-9a-f]{12,64}", identifier), "INVALID_CONTAINER_ID")
        values = json.loads(self.docker(["inspect", identifier]))
        require(isinstance(values, list) and len(values) == 1, "UNKNOWN_CONTAINER")
        value = values[0]
        labels = value["Config"].get("Labels") or {}
        require(
            labels.get("com.docker.compose.project") == self.project.project_name
            and labels.get("com.docker.compose.service") == service,
            "FOREIGN_CONTAINER",
        )
        require(value["Config"]["Image"] == image, "CONTAINER_IMAGE_DRIFT")
        expected_networks = {self.project.project_name + "_app"}
        if service in ("web", "gateway"):
            expected_networks.add(TRAEFIK_NETWORK)
        require(
            set(value["NetworkSettings"]["Networks"]) == expected_networks,
            "CONTAINER_NETWORK_DRIFT",
        )
        if self.network_snapshot:
            for name, attached in value["NetworkSettings"]["Networks"].items():
                require(
                    attached["NetworkID"] == self.network_snapshot[name]["id"],
                    "CONTAINER_NETWORK_ID_DRIFT",
                )
        expected_mounts = (
            {
                str(self.project.root / source): target
                for source, target in (
                    ("public", "/srv/public"),
                    ("gateway", "/etc/nginx/conf.d"),
                )
            }
            if service == "gateway"
            else {}
        )
        mounts = value.get("Mounts", [])
        require(len(mounts) == len(expected_mounts), "CONTAINER_MOUNT_DRIFT")
        for mount in mounts:
            require(
                mount["Type"] == "bind"
                and mount.get("RW") is False
                and expected_mounts.get(mount["Source"]) == mount["Destination"],
                "CONTAINER_MOUNT_DRIFT",
            )
        return value

    def one(self, service, image):
        values = self.services(service)
        require(len(values) == 1, "EXPECTED_SINGLE_CONTAINER:" + service)
        return self.inspect(values[0], service, image)["Id"]

    def compose(self, manifest, slot, gateway_image, args):
        config = compose_config(
            self.project, manifest, slot=slot, gateway_image=gateway_image
        )
        self.project.write(".compose.handoff.json", json.dumps(config).encode())
        return self.docker(
            [
                "compose",
                "--project-name",
                self.project.project_name,
                "--project-directory",
                str(self.project.root),
                "--env-file",
                "/dev/null",
                "-f",
                str(self.project.root / ".compose.handoff.json"),
                *args,
            ]
        )

    def networks(self):
        result = {}
        for name in (self.project.project_name + "_app", TRAEFIK_NETWORK):
            values = json.loads(self.docker(["network", "inspect", name]))
            require(len(values) == 1 and values[0]["Name"] == name, "NETWORK_NOT_FOUND")
            value = values[0]
            result[name] = {"id": value["Id"], "ipam": value["IPAM"]}
            if name.endswith("_app"):
                require(
                    [entry.get("Subnet") for entry in value["IPAM"]["Config"]]
                    == [self.project.app_subnet],
                    "APP_NETWORK_SUBNET_DRIFT",
                )
        return result

    def preflight(self, manifest, *, schema_manifest=None):
        self.use(manifest)
        require(not self.services("worker", all_containers=False), "WORKER_RUNNING")
        # 名称、ID与IPAM同时固定，不能同名替换或隐式扩网。
        require(self.networks() == self.network_snapshot, "NETWORK_ID_OR_IPAM_DRIFT")
        disk = os.statvfs(self.project.root)
        require(
            disk.f_bavail * disk.f_frsize >= 128 * 1024 * 1024,
            "INSUFFICIENT_RELEASE_DISK_SPACE",
        )
        self.docker(["pull", manifest["api"]])
        self.docker(["pull", manifest["web"]])
        schema_manifest = schema_manifest or manifest
        status = self.compose(
            schema_manifest, "blue", schema_manifest["web"], ["run", "--rm", "--no-deps", "database"]
        )
        require(
            json.loads(status) == {"status": "already_initialized"},
            "DATABASE_NOT_INITIALIZED",
        )

    def archive(self, image, identifier=None):
        """停止态临时镜像容器只用于docker cp tar；不执行镜像命令。"""
        created = identifier is None
        if created:
            self.recover_extraction()
            name = self.project.project_name + "-assets"
            nonce = uuid.uuid4().hex
            intent = {"name": name, "image": image, "nonce": nonce, "id": None}
            self.project.write(".asset-extraction.json", json.dumps(intent).encode())
            identifier = self.docker(
                [
                    "create",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--label",
                    "pdd.release.project=" + self.project.project_name,
                    "--label",
                    "pdd.release.extraction=" + nonce,
                    "--label",
                    "traefik.enable=false",
                    image,
                ]
            ).strip()
            require(
                re.fullmatch("[0-9a-f]{64}", identifier), "INVALID_EXTRACTION_CONTAINER"
            )
            intent["id"] = identifier
            self.project.write(".asset-extraction.json", json.dumps(intent).encode())
        try:
            if created:
                value = json.loads(self.docker(["inspect", identifier]))[0]
                require(
                    value["Config"]["Image"] == image
                    and value["Config"]["Labels"].get("pdd.release.project")
                    == self.project.project_name
                    and not value["State"]["Running"]
                    and not value.get("Mounts"),
                    "UNSAFE_ASSET_CONTAINER",
                )
            return self.docker(
                ["cp", identifier + ":/usr/share/nginx/html/.", "-"], binary=True
            )
        finally:
            if created:
                self.recover_extraction()

    def recover_extraction(self):
        """仅恢复已登记nonce的停止态提取容器，容器创建前中断亦可幂等核对。"""
        if not self.project.check_file(".asset-extraction.json"):
            return
        intent = json.loads(self.project.read(".asset-extraction.json"))
        require(
            isinstance(intent, dict)
            and set(intent) == {"name", "image", "nonce", "id"},
            "INVALID_EXTRACTION_STATE",
        )
        require(
            intent["name"] == self.project.project_name + "-assets"
            and re.fullmatch("[0-9a-f]{32}", intent["nonce"]),
            "INVALID_EXTRACTION_STATE",
        )
        require(
            intent["id"] is None or re.fullmatch("[0-9a-f]{64}", intent["id"]),
            "INVALID_EXTRACTION_STATE",
        )
        self.project.validate_manifest(dict(self.manifest, web=intent["image"]))
        identifiers = self.docker(
            [
                "ps",
                "-a",
                "--filter",
                "name=^/" + intent["name"] + "$",
                "--format",
                "{{.ID}}",
            ]
        ).split()
        require(len(identifiers) <= 1, "EXTRACTION_CONTAINER_DRIFT")
        if identifiers:
            value = json.loads(self.docker(["inspect", identifiers[0]]))[0]
            labels = value["Config"].get("Labels") or {}
            require(
                value["Name"] == "/" + intent["name"]
                and value["Config"]["Image"] == intent["image"]
                and labels.get("pdd.release.project") == self.project.project_name
                and labels.get("pdd.release.extraction") == intent["nonce"],
                "EXTRACTION_CONTAINER_DRIFT",
            )
            require(
                intent["id"] is None or value["Id"] == intent["id"],
                "EXTRACTION_CONTAINER_DRIFT",
            )
            require(
                not value["State"]["Running"]
                and not value.get("Mounts")
                and value["HostConfig"]["NetworkMode"] == "none",
                "UNSAFE_EXTRACTION_CONTAINER",
            )
            self.docker(["rm", value["Id"]])
        self.project.remove(".asset-extraction.json")

    def bootstrap(self):
        require(self.project.check_file(".release.json"), "LEGACY_RELEASE_REQUIRED")
        old, _ = self.project.state(".release.json")
        manifest = old["manifest"]
        previous_manifest = None
        if self.project.check_file(".release.previous.json"):
            previous, _ = self.project.state(".release.previous.json")
            if previous["manifest"] != manifest:
                previous_manifest = previous["manifest"]
        self.use(manifest)
        self.network_snapshot = self.networks()
        api = self.one("api", manifest["api"])
        web = self.one("web", manifest["web"])
        # 首次交接在state落盘前中断，也能恢复本模块未提交的临时公开文件。
        self.assets._recover_pending()
        # 首次接管前已有浏览器可能仍运行上一成功版HTML。只从已验证镜像
        # 提取公开静态产物；沿用总预算，任一缺失/冲突均在切流前拒绝。
        protected = {manifest["commit"]}
        if previous_manifest is not None:
            protected.add(previous_manifest["commit"])
        self.assets.prepare(
            self.archive(manifest["web"], web),
            manifest["commit"],
            now=time.time(),
            protected=protected,
        )
        if previous_manifest is not None:
            self.docker(["pull", previous_manifest["web"]])
            self.assets.prepare(
                self.archive(previous_manifest["web"]),
                previous_manifest["commit"],
                now=time.time(),
                protected=protected,
            )
        active = {
            "manifest": manifest,
            "slot": "legacy",
            "api": api,
            "compose": old["compose"],
        }
        return {
            "version": 1,
            "phase": "idle",
            "gateway": {"id": None, "image": None},
            "active": active,
            "previous": None,
            "candidate": None,
            "old_workers": [],
            "legacy_web": web,
            "networks": self.network_snapshot,
        }

    def prepare(self, candidate, state):
        manifest = candidate["manifest"]
        self.use(manifest)
        slot = candidate["slot"]
        require(not self.services("api_" + slot), "CANDIDATE_SLOT_OCCUPIED")
        protected = {
            state[key]["manifest"]["commit"]
            for key in ("active", "previous")
            if state[key]
        }
        self.assets.prepare(
            self.archive(manifest["web"]),
            manifest["commit"],
            now=time.time(),
            protected=protected,
        )
        self.compose(
            manifest,
            slot,
            state["gateway"]["image"] or manifest["web"],
            [
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                str(WAIT_SECONDS),
                "api_" + slot,
            ],
        )
        candidate["api"] = self.one("api_" + slot, manifest["api"])

    def warm(self, candidate):
        self.use(candidate["manifest"])
        service = "api" if candidate["slot"] == "legacy" else "api_" + candidate["slot"]
        self.inspect(candidate["api"], service, candidate["manifest"]["api"])
        try:
            result = self.docker(
                ["exec", candidate["api"], "python", "-c", BUSINESS_PROBE]
            )
        except subprocess.CalledProcessError as error:
            # 仅报告固定分类，不能回显进程命令、响应正文或原始stderr。
            tail = (
                error.stderr[-1000:].splitlines()
                if isinstance(error.stderr, str)
                else []
            )
            reason = (
                "timeout"
                if tail[-1:] == ["TimeoutError: timed out"]
                else "process_error"
            )
            print(
                json.dumps(
                    {
                        "event": "release_business_probe_failed",
                        "reason": reason,
                        "release_commit": candidate["manifest"]["commit"],
                    }
                ),
                flush=True,
            )
            raise
        require(result.strip() == "BUSINESS_PREWARM_OK", "BUSINESS_PREWARM_FAILED")

    def workers(self, state):
        self.use(state["active"]["manifest"])
        gateway = state["gateway"]
        if not gateway["id"]:
            return []
        self.inspect(gateway["id"], "gateway", gateway["image"])
        result = self.docker(["exec", gateway["id"], "sh", "-c", WORKERS]).split()
        require(
            all(re.fullmatch("[0-9]+:[0-9]+", value) for value in result),
            "INVALID_NGINX_WORKERS",
        )
        return result

    def switch(self, state, target):
        self.use(target["manifest"])
        gateway = state["gateway"]
        config = nginx_config(target)
        if gateway["id"]:
            self.inspect(gateway["id"], "gateway", gateway["image"])
            # 非.conf候选不被运行入口的include载入；校验通过才原子替换启动配置。
            self.assets._write("gateway/candidate.server", config)
            self.assets._write(
                "gateway/candidate.main",
                b"error_log /dev/stderr; events {} http { include /etc/nginx/mime.types; include /etc/nginx/conf.d/candidate.server; }",
            )
            self.docker(
                [
                    "exec",
                    gateway["id"],
                    "nginx",
                    "-t",
                    "-c",
                    "/etc/nginx/conf.d/candidate.main",
                ]
            )
            self.assets._write("gateway/default.conf", config)
            self.docker(["exec", gateway["id"], "nginx", "-s", "reload"])
        else:
            self.assets._write("gateway/default.conf", config)
            require(
                target["slot"] in ("blue", "green"), "FIRST_GATEWAY_REQUIRES_CANDIDATE"
            )
            gateway["image"] = target["manifest"]["web"]
            existing = self.services("gateway")
            if existing:
                gateway["id"] = self.one("gateway", gateway["image"])
            else:
                self.compose(
                    target["manifest"],
                    target["slot"],
                    gateway["image"],
                    [
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        str(WAIT_SECONDS),
                        "gateway",
                    ],
                )
                gateway["id"] = self.one("gateway", gateway["image"])

    def observe(self, state):
        target = (
            state["active"]
            if state["phase"] == "reverting"
            else state["candidate"] or state["active"]
        )
        return self._observe(state, {target["manifest"]["commit"]})

    def current(self, state):
        # 回切可以由外部失败触发，不能要求坏版本的公网资产先通过才允许恢复。
        self.use((state["candidate"] or state["active"])["manifest"])
        require(self.networks() == state["networks"], "NETWORK_ID_OR_IPAM_DRIFT")
        gateway = state["gateway"]
        if not gateway["id"] and self.services("gateway"):
            gateway["image"] = state["candidate"]["manifest"]["web"]
            gateway["id"] = self.one("gateway", gateway["image"])
        require(gateway["id"], "GATEWAY_NOT_FOUND")
        self.inspect(gateway["id"], "gateway", gateway["image"])
        marker = self.docker(
            [
                "exec",
                gateway["id"],
                "wget",
                "-q",
                "-O",
                "-",
                "http://127.0.0.1:8080" + MARKER,
            ]
        ).strip()
        require(
            marker
            in {
                state[key]["manifest"]["commit"]
                for key in ("active", "candidate")
                if state[key]
            },
            "ROUTE_STATE_AMBIGUOUS",
        )
        return marker

    def _observe(self, state, expected):
        self.use((state["candidate"] or state["active"])["manifest"])
        require(self.networks() == state["networks"], "NETWORK_ID_OR_IPAM_DRIFT")
        gateway = state["gateway"]
        if not gateway["id"] and self.services("gateway"):
            gateway["image"] = state["candidate"]["manifest"]["web"]
            gateway["id"] = self.one("gateway", gateway["image"])
        require(gateway["id"], "GATEWAY_NOT_FOUND")
        self.inspect(gateway["id"], "gateway", gateway["image"])
        deadline = time.monotonic() + WAIT_SECONDS
        consecutive = 0
        while True:
            local = self.docker(
                [
                    "exec",
                    gateway["id"],
                    "wget",
                    "-q",
                    "-O",
                    "-",
                    "http://127.0.0.1:8080" + MARKER,
                ]
            ).strip()
            try:
                external = self.project.external_marker()
            except (OSError, ValueError):
                external = None
            consecutive = (
                consecutive + 1 if local == external and local in expected else 0
            )
            if consecutive >= 2:
                self.verify_entry(local)
                return local
            require(time.monotonic() < deadline, "EXTERNAL_HANDOFF_NOT_CONFIRMED")
            time.sleep(POLL_SECONDS)

    def verify_entry(self, commit):
        """marker之外核验真实HTML及其首个外部入口脚本，拒绝健康假阳性。"""
        files = self.assets._catalog()[commit]["files"]
        parser = EntryScripts()
        parser.feed(
            self.assets._file("public/releases/" + commit + "/index.html").decode(
                "utf-8"
            )
        )
        require(parser.sources, "MISSING_ENTRY_SCRIPT")
        script = parser.sources[0]
        require(
            script.startswith("/assets/")
            and script.endswith(".js")
            and script[1:] in files,
            "INVALID_ENTRY_SCRIPT",
        )
        self.project.verify_public_files(
            {"/index.html": files["index.html"], script: files[script[1:]]}
        )

    def wait_stopped(self, identifier, service, image):
        deadline = time.monotonic() + WAIT_SECONDS
        while self.inspect(identifier, service, image)["State"]["Running"]:
            require(time.monotonic() < deadline, "DRAIN_TIMEOUT")
            time.sleep(POLL_SECONDS)

    def drain(self, state):
        self.use(state["candidate"]["manifest"])
        if state["legacy_web"] and self.services("web"):
            old = state["active"]
            identifier = state["legacy_web"]
            running = self.inspect(identifier, "web", old["manifest"]["web"])["State"][
                "Running"
            ]
            if running:
                self.docker(["update", "--restart=no", identifier])
                self.docker(["kill", "--signal=QUIT", identifier])
            self.wait_stopped(identifier, "web", old["manifest"]["web"])
        deadline = time.monotonic() + WAIT_SECONDS
        while set(state["old_workers"]) & set(self.workers(state)):
            require(time.monotonic() < deadline, "DRAIN_TIMEOUT")
            time.sleep(POLL_SECONDS)

    def restore(self, state):
        """回切前确认旧API仍可服务；已退出则在同一空槽恢复旧镜像再预热。"""
        old = state["active"]
        self.use(old["manifest"])
        service = "api" if old["slot"] == "legacy" else "api_" + old["slot"]
        existing = self.services(service)
        if existing:
            old["api"] = self.one(service, old["manifest"]["api"])
            info = self.inspect(old["api"], service, old["manifest"]["api"])
            if not info["State"]["Running"]:
                self.docker(["start", old["api"]])
        else:
            slot = "blue" if old["slot"] == "legacy" else old["slot"]
            config = compose_config(
                self.project,
                old["manifest"],
                slot=slot,
                gateway_image=state["gateway"]["image"],
            )
            if old["slot"] == "legacy":
                config["services"]["api"] = config["services"].pop("api_blue")
            self.project.write(".compose.handoff.json", json.dumps(config).encode())
            self.docker(
                [
                    "compose",
                    "--project-name",
                    self.project.project_name,
                    "--project-directory",
                    str(self.project.root),
                    "--env-file",
                    "/dev/null",
                    "-f",
                    str(self.project.root / ".compose.handoff.json"),
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "--wait-timeout",
                    str(WAIT_SECONDS),
                    service,
                ]
            )
            old["api"] = self.one(service, old["manifest"]["api"])
        self.warm(old)

    def drain_reverted(self, state):
        deadline = time.monotonic() + WAIT_SECONDS
        while set(state["old_workers"]) & set(self.workers(state)):
            require(time.monotonic() < deadline, "DRAIN_TIMEOUT")
            time.sleep(POLL_SECONDS)

    def retire(self, state):
        """仅已切流且旧nginx请求全部结束后退出旧API；从不发送SIGKILL。"""
        old = state["active"]
        self.assets.touch(old["manifest"]["commit"], now=time.time())
        self.use(state["candidate"]["manifest"])
        service = "api" if old["slot"] == "legacy" else "api_" + old["slot"]
        if old["api"]:
            # 幂等恢复：只检查本项目对应服务；已删除则不寻找其他ID代替。
            candidates = self.services(service)
            if candidates:
                require(
                    len(candidates) == 1 and old["api"].startswith(candidates[0]),
                    "RETIRED_CONTAINER_DRIFT",
                )
                info = self.inspect(old["api"], service, old["manifest"]["api"])
                if info["State"]["Running"]:
                    self.docker(["update", "--restart=no", old["api"]])
                    self.docker(["kill", "--signal=TERM", old["api"]])
                self.wait_stopped(old["api"], service, old["manifest"]["api"])
                # 停止后、删除前保留切换窗口的安全摘要；读取失败保留容器供续查。
                logs = self.docker(
                    [
                        "logs",
                        "--since",
                        "10m",
                        "--tail",
                        "1000",
                        "--timestamps",
                        old["api"],
                    ]
                )
                summary = summarize_database_errors(logs)
                summary.update(
                    event="release_api_error_summary",
                    retired_commit=old["manifest"]["commit"],
                    release_commit=state["candidate"]["manifest"]["commit"],
                    container=old["api"][:12],
                )
                print(
                    json.dumps(summary, sort_keys=True, separators=(",", ":")),
                    flush=True,
                )
                self.docker(["rm", old["api"]])
        if state["legacy_web"]:
            identifier = state["legacy_web"]
            if self.services("web"):
                info = self.inspect(identifier, "web", old["manifest"]["web"])
                require(not info["State"]["Running"], "LEGACY_WEB_STILL_RUNNING")
                self.docker(["rm", identifier])

    def prune(self, state):
        protected = {
            state[key]["manifest"]["commit"]
            for key in ("active", "previous")
            if state[key]
        }
        self.assets.prune(now=time.time(), protected=protected)

    def abort(self, state):
        """仅未切流候选可直接撤销；活动入口与旧API完全不动。"""
        candidate = state["candidate"]
        self.assets.touch(candidate["manifest"]["commit"], now=time.time())
        self.use(candidate["manifest"])
        self.recover_extraction()
        service = "api_" + candidate["slot"]
        identifiers = self.services(service)
        require(len(identifiers) <= 1, "CANDIDATE_SLOT_DRIFT")
        if identifiers:
            info = self.inspect(identifiers[0], service, candidate["manifest"]["api"])
            if candidate["api"]:
                require(info["Id"] == candidate["api"], "CANDIDATE_SLOT_DRIFT")
            if info["State"]["Running"]:
                self.docker(["update", "--restart=no", info["Id"]])
                self.docker(["kill", "--signal=TERM", info["Id"]])
            self.wait_stopped(info["Id"], service, candidate["manifest"]["api"])
            self.docker(["rm", info["Id"]])

    def first_unexposed(self, state):
        """首次入口尚未创建的中断：核对旧服务后才能撤销孤立候选。"""
        if (
            state["gateway"]["id"]
            or not state["legacy_web"]
            or state["active"]["slot"] != "legacy"
        ):
            return False
        self.use(state["active"]["manifest"])
        if self.services("gateway"):
            return False
        old = state["active"]
        require(
            self.inspect(state["legacy_web"], "web", old["manifest"]["web"])["State"][
                "Running"
            ],
            "LEGACY_WEB_UNAVAILABLE",
        )
        require(
            self.inspect(old["api"], "api", old["manifest"]["api"])["State"]["Running"],
            "LEGACY_API_UNAVAILABLE",
        )
        return True


class Handoff:
    """持久化交接意图；任何不确定状态保留双方，不盲删活动容器。"""

    def __init__(self, project):
        self.project = project
        self.runtime = Runtime(project)

    def save(self, state):
        self.project.write(".handoff.json", json.dumps(state, sort_keys=True).encode())

    def state(self):
        if not self.project.check_file(".handoff.json"):
            value = self.runtime.bootstrap()
            self.save(value)
            return value
        value = json.loads(self.project.read(".handoff.json"))
        require(
            isinstance(value, dict)
            and set(value)
            == {
                "version",
                "phase",
                "gateway",
                "active",
                "previous",
                "candidate",
                "old_workers",
                "legacy_web",
                "networks",
            }
            and value["version"] == 1,
            "INVALID_HANDOFF_STATE",
        )
        require(isinstance(value["networks"], dict), "INVALID_NETWORK_SNAPSHOT")
        self.runtime.network_snapshot = value["networks"]
        require(
            value["phase"]
            in (
                "idle",
                "preparing",
                "prepared",
                "switching",
                "draining",
                "committing",
                "reverting",
            ),
            "INVALID_HANDOFF_PHASE",
        )
        require(
            isinstance(value["gateway"], dict)
            and set(value["gateway"]) == {"id", "image"},
            "INVALID_GATEWAY_STATE",
        )
        require(
            value["gateway"]["id"] is None
            or re.fullmatch("[0-9a-f]{64}", value["gateway"]["id"]),
            "INVALID_GATEWAY_ID",
        )
        require(
            value["legacy_web"] is None
            or re.fullmatch("[0-9a-f]{64}", value["legacy_web"]),
            "INVALID_LEGACY_WEB_ID",
        )
        require(
            isinstance(value["old_workers"], list)
            and all(
                isinstance(worker, str) and re.fullmatch("[0-9]+:[0-9]+", worker)
                for worker in value["old_workers"]
            ),
            "INVALID_WORKER_STATE",
        )
        for key in ("active", "previous", "candidate"):
            record = value[key]
            if record is not None:
                require(
                    set(record) == {"manifest", "slot", "api", "compose"}
                    and record["slot"] in ("legacy", "blue", "green"),
                    "INVALID_HANDOFF_RECORD",
                )
                self.project.validate_manifest(record["manifest"])
                require(
                    re.fullmatch("[0-9a-f]{40}", record["manifest"]["commit"]),
                    "INVALID_HANDOFF_COMMIT",
                )
                require(
                    record["api"] is None
                    or re.fullmatch("[0-9a-f]{64}", record["api"]),
                    "INVALID_HANDOFF_CONTAINER",
                )
                require(
                    0
                    < len(base64.b64decode(record["compose"], validate=True))
                    <= 200_000,
                    "INVALID_HANDOFF_COMPOSE",
                )
        if value["gateway"]["image"] is not None:
            self.project.validate_manifest(
                dict(value["active"]["manifest"], web=value["gateway"]["image"])
            )
        require(
            value["active"] is not None
            and (value["candidate"] is None) == (value["phase"] == "idle"),
            "INVALID_HANDOFF_PHASE_RECORDS",
        )
        return value

    def release(self, manifest, compose, *, rollback=False):
        state = self.state()
        require(state["phase"] == "idle", "HANDOFF_PENDING_RECONCILE_OR_ROLLBACK")
        if manifest == state["active"]["manifest"]:
            return
        if rollback:
            # 加法迁移保留在库中，由认识当前目录的镜像核验，再预热上一版读路径。
            self.runtime.preflight(manifest, schema_manifest=state["active"]["manifest"])
        else:
            self.runtime.preflight(manifest)
        candidate = {
            "manifest": manifest,
            "slot": "green" if state["active"]["slot"] == "blue" else "blue",
            "api": None,
            "compose": base64.b64encode(compose).decode(),
        }
        state.update(phase="preparing", candidate=candidate)
        self.save(state)
        self.runtime.prepare(candidate, state)
        state["phase"] = "prepared"
        self.save(state)
        self.runtime.warm(candidate)
        state["old_workers"] = self.runtime.workers(state)
        state["phase"] = "switching"
        self.save(state)
        self.runtime.switch(state, candidate)
        self.save(state)
        self.reconcile()

    def reconcile(self):
        state = self.state()
        if state["phase"] == "idle":
            # 上次已完成切流后若清理中断，仅续做已批准的过期静态清理。
            self.runtime.prune(state)
            return
        if state["phase"] == "reverting":
            self.runtime.switch(state, state["active"])
            marker = self.runtime.observe(state)
            require(
                marker == state["active"]["manifest"]["commit"], "ROUTE_STATE_AMBIGUOUS"
            )
            self.runtime.drain_reverted(state)
            self.runtime.abort(state)
            state.update(phase="idle", candidate=None, old_workers=[])
            self.save(state)
            return
        require(
            state["phase"] in ("switching", "draining", "committing"),
            "PREPARATION_INCOMPLETE_USE_ROLLBACK",
        )
        require(
            self.runtime.observe(state) == state["candidate"]["manifest"]["commit"],
            "ROUTE_STATE_AMBIGUOUS",
        )
        if state["phase"] != "committing":
            state["phase"] = "draining"
            self.save(state)
            self.runtime.drain(state)
            # 退出/删除前已持久化排空阶段；中断后由容器实际状态幂等续接。
            self.runtime.retire(state)
            state["phase"] = "committing"
            self.save(state)
        old, new = state["active"], state["candidate"]
        self.project.write(
            ".release.previous.json",
            json.dumps(
                {"version": 1, "manifest": old["manifest"], "compose": old["compose"]}
            ).encode(),
        )
        self.project.write(
            ".release.json",
            json.dumps(
                {"version": 1, "manifest": new["manifest"], "compose": new["compose"]}
            ).encode(),
        )
        state.update(
            phase="idle",
            active=new,
            previous=old,
            candidate=None,
            old_workers=[],
            legacy_web=None,
        )
        self.save(state)
        self.runtime.prune(state)

    def rollback(self):
        state = self.state()
        if state["phase"] in ("preparing", "prepared"):
            self.runtime.abort(state)
            state.update(phase="idle", candidate=None, old_workers=[])
            self.save(state)
            return
        if state["phase"] in ("switching", "draining", "reverting"):
            if state["phase"] != "reverting":
                if state["phase"] == "switching" and self.runtime.first_unexposed(
                    state
                ):
                    self.runtime.abort(state)
                    state.update(phase="idle", candidate=None, old_workers=[])
                    self.save(state)
                    return
                require(
                    self.runtime.current(state)
                    in {
                        state[key]["manifest"]["commit"]
                        for key in ("active", "candidate")
                    },
                    "ROUTE_STATE_AMBIGUOUS",
                )
                self.runtime.restore(state)
                state["old_workers"] = self.runtime.workers(state)
                state["phase"] = "reverting"
                self.save(state)
            self.reconcile()
            return
        require(state["phase"] == "idle", "HANDOFF_PENDING_RECONCILE_REQUIRED")
        target = state["previous"]
        require(target is not None, "NO_PREVIOUS_HANDOFF_RELEASE")
        self.release(
            target["manifest"], base64.b64decode(target["compose"], validate=True), rollback=True
        )
