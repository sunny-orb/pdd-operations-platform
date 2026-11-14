"""交接状态机与固定容器边界；真实Docker流量由独立验收补齐。"""

import importlib.util
import io
import json
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def module():
    filename = ROOT / "deploy/handoff.py"
    assert filename.exists(), "平滑交接控制器尚未实现"
    spec = importlib.util.spec_from_file_location("deployment_handoff", filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def manifest(digit="a"):
    return {
        "commit": digit * 40,
        **{
            key: f"ghcr.io/sunny-orb/pdd-operations-platform-{key}@sha256:" + digit * 64
            for key in ("api", "web", "worker")
        },
    }


def record(digit="a", slot="blue"):
    return {"manifest": manifest(digit), "slot": slot, "api": digit * 64, "compose": "e30="}


def test_gateway_config_routes_unique_slot_and_retains_shared_assets():
    config = module().nginx_config(record()).decode()
    assert "api_blue:8000" in config and "http://api:8000" not in config
    assert "/srv/public/releases/" + "a" * 40 in config
    assert "root /srv/public;" in config and "try_files $uri =404" in config
    assert "proxy_read_timeout 30s" in config
    assert "Authorization" in config and "no-store" in config
    assert "a" * 40 in config and "pdd-release" in config


def test_generated_compose_only_uses_existing_networks_and_fixed_mounts(tmp_path):
    project = SimpleNamespace(
        root=tmp_path, project_name="pdd-operations-production", domain="pdd-operations.kktree.cn"
    )
    config = module().compose_config(
        project, manifest(), slot="green", gateway_image=manifest("b")["web"]
    )
    assert config["networks"]["app"] == {"external": True, "name": "pdd-operations-production_app"}
    assert config["networks"]["traefik"] == {"external": True, "name": "traefik-network"}
    assert set(config["services"]) == {"api_green", "gateway", "database"}
    gateway = config["services"]["gateway"]
    assert gateway["image"] == manifest("b")["web"]
    assert (
        gateway["labels"]["traefik.http.routers.pdd-operations-entry.service"]
        == "pdd-operations-entry"
    )
    assert all(v["read_only"] and not v["bind"]["create_host_path"] for v in gateway["volumes"])


@pytest.mark.parametrize("slot", ["blue", "green"])
def test_non_entry_services_explicitly_disable_traefik_discovery(tmp_path, slot):
    p = SimpleNamespace(
        root=tmp_path, project_name="pdd-operations-production", domain="pdd-operations.kktree.cn"
    )
    config = module().compose_config(p, manifest(), slot=slot, gateway_image=manifest()["web"])
    for service in ("api_" + slot, "database"):
        assert config["services"][service].get("labels", {}).get("traefik.enable") == "false"
    assert config["services"]["gateway"]["labels"]["traefik.enable"] == "true"


def test_static_extraction_container_explicitly_disables_traefik_discovery():
    m = module()
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.project = FakeProject()
    runtime.project.project_name = "pdd-operations-production"
    runtime.recover_extraction = lambda: None
    calls = []

    def docker(args, *, binary=False):
        calls.append(args)
        if args[0] == "create":
            return "e" * 64
        if args[0] == "inspect":
            return json.dumps(
                [
                    {
                        "Config": {
                            "Image": manifest()["web"],
                            "Labels": {"pdd.release.project": runtime.project.project_name},
                        },
                        "State": {"Running": False},
                        "Mounts": [],
                    }
                ]
            )
        assert binary and args[0] == "cp"
        return b"public-static-tar"

    runtime.docker = docker
    runtime.archive(manifest()["web"])
    create = next(args for args in calls if args[0] == "create")
    labels = [create[index + 1] for index, value in enumerate(create) if value == "--label"]
    assert "traefik.enable=false" in labels


@pytest.mark.parametrize("slot", ["legacy", "../outside", "blue; id", "other"])
def test_generated_candidate_refuses_unknown_slot(tmp_path, slot):
    p = SimpleNamespace(
        root=tmp_path, project_name="pdd-operations-production", domain="pdd-operations.kktree.cn"
    )
    with pytest.raises(ValueError):
        module().compose_config(p, manifest(), slot=slot, gateway_image=manifest()["web"])


def test_prewarm_program_is_readonly_and_checks_score_compatibility():
    script = module().BUSINESS_PROBE
    compile(script, "probe", "exec")
    assert "pending_recompute" in script and "incompatible" in script
    assert "published-versions" in script and "weather/publication" in script
    assert "aftersales" not in script
    assert "POST" not in script and "sync-project" not in script


class FakeProject:
    def __init__(self):
        self.files = {}

    def check_file(self, name, **_):
        return name in self.files

    def read(self, name):
        return self.files[name]

    def write(self, name, data):
        self.files[name] = data

    def validate_manifest(self, value):
        assert set(value) == {"commit", "api", "web", "worker"}

    def remove(self, name):
        self.files.pop(name, None)


class FakeRuntime:
    def __init__(self, active):
        self.events = []
        self.marker = active
        self.fail_warm = False
        self.fail_drain = False

    def preflight(self, _):
        self.events.append("preflight")

    def prepare(self, candidate, _):
        self.events.append("prepare")
        candidate["api"] = "c" * 64

    def warm(self, _):
        self.events.append("warm")
        if self.fail_warm:
            raise ValueError("BUSINESS_PREWARM_FAILED")

    def workers(self, _):
        return ["11:123"]

    def switch(self, state, target):
        self.events.append("switch")
        self.marker = target["manifest"]["commit"]

    def observe(self, _):
        return self.marker

    def current(self, _):
        return self.marker

    def drain(self, _):
        self.events.append("drain")
        if self.fail_drain:
            raise ValueError("DRAIN_TIMEOUT")

    def retire(self, _):
        self.events.append("retire")

    def prune(self, _):
        self.events.append("prune")

    def restore(self, _):
        self.events.append("restore")

    def first_unexposed(self, _):
        return False


def controller():
    m = module()
    p = FakeProject()
    initial = {
        "version": 1,
        "phase": "idle",
        "gateway": {"id": "f" * 64, "image": manifest()["web"]},
        "active": record(),
        "previous": None,
        "candidate": None,
        "old_workers": [],
        "legacy_web": None,
        "networks": {},
    }
    p.write(".handoff.json", json.dumps(initial).encode())
    h = m.Handoff.__new__(m.Handoff)
    h.project = p
    h.runtime = FakeRuntime("a" * 40)
    return h, p


def test_candidate_business_failure_never_switches_or_stops_active():
    h, p = controller()
    h.runtime.fail_warm = True
    with pytest.raises(ValueError, match="BUSINESS_PREWARM_FAILED"):
        h.release(manifest("b"), b"{}")
    assert "switch" not in h.runtime.events and "retire" not in h.runtime.events
    state = json.loads(p.read(".handoff.json"))
    assert state["active"]["manifest"] == manifest("a")


def test_switch_is_durable_before_drain_and_no_retire_on_drain_timeout():
    h, p = controller()
    h.runtime.fail_drain = True
    with pytest.raises(ValueError, match="DRAIN_TIMEOUT"):
        h.release(manifest("b"), b"{}")
    state = json.loads(p.read(".handoff.json"))
    assert state["phase"] == "draining" and state["candidate"]["manifest"] == manifest("b")
    assert "retire" not in h.runtime.events
    assert h.runtime.marker == "b" * 40


def test_reconcile_finishes_observed_switch_without_creating_another_candidate():
    h, p = controller()
    h.runtime.fail_drain = True
    with pytest.raises(ValueError):
        h.release(manifest("b"), b"{}")
    h.runtime.fail_drain = False
    h.reconcile()
    state = json.loads(p.read(".handoff.json"))
    assert state["phase"] == "idle" and state["active"]["manifest"] == manifest("b")
    assert h.runtime.events.count("prepare") == 1
    assert h.runtime.events.index("retire") > h.runtime.events.index("drain")


def test_ambiguous_marker_refuses_retiring_any_container():
    h, p = controller()
    h.runtime.fail_drain = True
    with pytest.raises(ValueError):
        h.release(manifest("b"), b"{}")
    h.runtime.fail_drain = False
    h.runtime.marker = "d" * 40
    with pytest.raises(ValueError, match="ROUTE_STATE_AMBIGUOUS"):
        h.reconcile()
    assert "retire" not in h.runtime.events


def test_rollback_switches_through_same_drain_path():
    h, p = controller()
    h.release(manifest("b"), b"{}")
    h.rollback()
    state = json.loads(p.read(".handoff.json"))
    assert state["active"]["manifest"] == manifest("a")
    assert h.runtime.events.count("switch") == 2
    assert h.runtime.events.count("drain") == 2


def test_project_uploads_and_loads_handoff_without_python_path(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("handoff_project_test", ROOT / "deploy/manage.py")
    manage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manage)
    assert {"handoff.py", "assets.py"} <= set(manage.UPLOADS)
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    for name in ("handoff.py", "assets.py"):
        (tmp_path / name).write_bytes((ROOT / "deploy" / name).read_bytes())
    p = manage.Project()
    p.fd = __import__("os").open(tmp_path, __import__("os").O_RDONLY)
    try:
        assert p.load_module("handoff.py").MARKER == "/.well-known/pdd-release"
        assert p.root == tmp_path
        with pytest.raises(ValueError):
            p.load_module("../elsewhere.py")
    finally:
        __import__("os").close(p.fd)


def test_prepared_failure_rollback_cleans_only_candidate():
    h, p = controller()
    h.runtime.abort = lambda state: h.runtime.events.append("abort_candidate")
    h.runtime.fail_warm = True
    with pytest.raises(ValueError):
        h.release(manifest("b"), b"{}")
    h.rollback()
    assert h.runtime.marker == "a" * 40
    assert h.runtime.events[-1] == "abort_candidate"
    assert json.loads(p.read(".handoff.json"))["phase"] == "idle"


def test_state_rejects_malformed_container_record_before_mutations():
    h, p = controller()
    state = json.loads(p.read(".handoff.json"))
    state["gateway"]["id"] = "--privileged"
    p.write(".handoff.json", json.dumps(state).encode())
    with pytest.raises(ValueError):
        h.release(manifest("b"), b"{}")
    assert not h.runtime.events


def test_rollback_after_switch_waits_for_new_requests_before_candidate_removal():
    h, p = controller()
    h.runtime.fail_drain = True
    with pytest.raises(ValueError):
        h.release(manifest("b"), b"{}")
    h.runtime.fail_drain = False
    h.runtime.abort = lambda state: h.runtime.events.append("abort_candidate")
    h.runtime.drain_reverted = lambda state: h.runtime.events.append("drain_reverted")
    h.rollback()
    state = json.loads(p.read(".handoff.json"))
    assert state["active"]["manifest"] == manifest("a") and state["candidate"] is None
    assert h.runtime.marker == "a" * 40
    assert h.runtime.events.index("drain_reverted") < h.runtime.events.index("abort_candidate")


def test_state_write_failure_after_cutover_can_resume_without_retiring_active():
    h, p = controller()
    original = p.write

    def fail_state(name, data):
        if name == ".handoff.json" and h.runtime.marker == "b" * 40:
            raise OSError("disk full")
        original(name, data)

    p.write = fail_state
    with pytest.raises(OSError):
        h.release(manifest("b"), b"{}")
    assert "retire" not in h.runtime.events
    p.write = original
    h.reconcile()
    assert json.loads(p.read(".handoff.json"))["active"]["manifest"] == manifest("b")


def test_observe_waits_for_reload_target_not_matching_old_local_and_external(monkeypatch):
    m = module()
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    runtime = m.Runtime.__new__(m.Runtime)
    local = iter(["a" * 40, "b" * 40, "b" * 40])
    external = iter(["a" * 40, "b" * 40, "b" * 40])
    runtime.project = SimpleNamespace(external_marker=lambda: next(external))
    runtime.use = lambda _: None
    runtime.networks = lambda: {}
    runtime.inspect = lambda *_: None
    runtime.verify_entry = lambda *_: None
    runtime.docker = lambda *_: next(local)
    state = {
        "gateway": {"id": "f" * 64, "image": manifest()["web"]},
        "active": record(),
        "candidate": record("b", "green"),
        "phase": "switching",
        "networks": {},
    }
    assert runtime.observe(state) == "b" * 40


def test_invalid_candidate_nginx_config_never_overwrites_restart_config():
    m = module()
    runtime = m.Runtime.__new__(m.Runtime)
    written = {"gateway/default.conf": b"known-good"}
    runtime.assets = SimpleNamespace(_write=lambda name, value: written.update({name: value}))
    runtime.use = lambda _: None
    runtime.inspect = lambda *_: None

    def command(args):
        if "-t" in args:
            raise ValueError("NGINX_CONFIG_REJECTED")

    runtime.docker = command
    state = {"gateway": {"id": "f" * 64, "image": manifest()["web"]}}
    with pytest.raises(ValueError, match="NGINX_CONFIG_REJECTED"):
        runtime.switch(state, record("b", "green"))
    assert written["gateway/default.conf"] == b"known-good"


def test_first_gateway_creation_failure_can_abort_without_route_marker():
    h, p = controller()
    state = json.loads(p.read(".handoff.json"))
    state.update(phase="switching", candidate=record("b", "green"), legacy_web="e" * 64)
    state["gateway"] = {"id": None, "image": None}
    state["active"]["slot"] = "legacy"
    p.write(".handoff.json", json.dumps(state).encode())
    h.runtime.first_unexposed = lambda _: True
    h.runtime.current = lambda _: (_ for _ in ()).throw(ValueError("GATEWAY_NOT_FOUND"))
    h.runtime.abort = lambda _: h.runtime.events.append("abort_candidate")
    h.rollback()
    final = json.loads(p.read(".handoff.json"))
    assert final["phase"] == "idle" and final["active"]["manifest"] == manifest("a")
    assert h.runtime.events == ["abort_candidate"]


@pytest.mark.parametrize("matching_nonce", [True, False])
def test_abandoned_extraction_only_removes_exact_registered_container(matching_nonce):
    m = module()
    p = FakeProject()
    p.project_name = "pdd-operations-production"
    intent = {
        "name": p.project_name + "-assets",
        "image": manifest()["web"],
        "nonce": "1" * 32,
        "id": None,
    }
    p.write(".asset-extraction.json", json.dumps(intent).encode())
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.project = p
    runtime.manifest = manifest()
    info = {
        "Id": "e" * 64,
        "Name": "/" + intent["name"],
        "Config": {
            "Image": intent["image"],
            "Labels": {
                "pdd.release.project": p.project_name,
                "pdd.release.extraction": intent["nonce"] if matching_nonce else "2" * 32,
            },
        },
        "HostConfig": {"NetworkMode": "none"},
        "State": {"Running": False},
        "Mounts": [],
    }
    calls = []

    def docker(args):
        calls.append(args)
        return "e" * 64 if args[0] == "ps" else json.dumps([info]) if args[0] == "inspect" else ""

    runtime.docker = docker
    assert hasattr(runtime, "recover_extraction"), "提取容器中断恢复尚未实现"
    if matching_nonce:
        runtime.recover_extraction()
        assert ["rm", "e" * 64] in calls and not p.check_file(".asset-extraction.json")
    else:
        with pytest.raises(ValueError):
            runtime.recover_extraction()
        assert not any(call[0] == "rm" for call in calls)


def test_entry_probe_selects_script_actually_referenced_by_index(tmp_path):
    m = module()
    asset_spec = importlib.util.spec_from_file_location(
        "handoff_asset_fixture", ROOT / "deploy/assets.py"
    )
    assets = importlib.util.module_from_spec(asset_spec)
    asset_spec.loader.exec_module(assets)
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.assets = assets.AssetStore(tmp_path)
    runtime.assets._write(
        "public/releases/" + "a" * 40 + "/index.html",
        b'<html><script type="module" src="/assets/entry-12345678.js"></script></html>',
    )
    runtime.assets._write(
        "public/catalog.json",
        json.dumps(
            {
                "a" * 40: {
                    "created": 0,
                    "files": {
                        "index.html": "1" * 64,
                        "assets/lazy-12345678.js": "2" * 64,
                        "assets/entry-12345678.js": "3" * 64,
                    },
                }
            }
        ).encode(),
    )
    calls = []
    runtime.project = SimpleNamespace(verify_public_files=lambda expected: calls.append(expected))
    assert hasattr(runtime, "verify_entry"), "入口确认应核验index及其入口JS"
    runtime.verify_entry("a" * 40)
    assert calls == [{"/index.html": "1" * 64, "/assets/entry-12345678.js": "3" * 64}]


def test_rollback_can_identify_local_version_when_external_assets_are_broken():
    m = module()
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.project = SimpleNamespace(
        external_marker=lambda: (_ for _ in ()).throw(ValueError("PUBLIC_UNAVAILABLE"))
    )
    runtime.use = lambda _: None
    runtime.networks = lambda: {}
    runtime.inspect = lambda *_: None
    runtime.docker = lambda *_: "b" * 40
    runtime.verify_entry = lambda *_: (_ for _ in ()).throw(ValueError("BAD_PUBLIC_ASSET"))
    state = {
        "gateway": {"id": "f" * 64, "image": manifest()["web"]},
        "active": record(),
        "candidate": record("b", "green"),
        "phase": "switching",
        "networks": {},
    }
    m.WAIT_SECONDS = 0
    assert runtime.current(state) == "b" * 40


@pytest.mark.parametrize("status, expected_meta_calls", [(409, 2), (503, 1)])
def test_business_prewarm_retries_only_one_version_conflict(
    monkeypatch, status, expected_meta_calls
):
    m = module()
    calls = []

    def read(url, **_):
        path = urllib.parse.urlsplit(url).path
        calls.append(path)
        if path == "/api/v1/products/meta" and calls.count(path) == 1:
            raise urllib.error.HTTPError(url, status, "fixture", {}, None)
        if path.endswith("/meta"):
            value = {
                "data": {
                    "date_from": "2026-10-01",
                    "date_to": "2026-10-30",
                    "default_publication_id": "weather",
                    "data_version": "v1",
                },
                "meta": {"data_version": "v1"},
            }
        else:
            value = {"data": {"status": "ready"}}
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(urllib.request, "urlopen", read)
    if status == 409:
        exec(m.BUSINESS_PROBE, {})
    else:
        with pytest.raises(urllib.error.HTTPError):
            exec(m.BUSINESS_PROBE, {})
    assert calls.count("/api/v1/products/meta") == expected_meta_calls


@pytest.mark.parametrize(
    "start,product_start,promotion_start",
    [
        ("2026-08-01", "2026-10-14", "2026-10-15"),
        ("2026-11-01", "2026-11-01", "2026-11-01"),
    ],
)
def test_business_probe_matches_product_default_and_preserves_promotion_window(
    monkeypatch, start, product_start, promotion_start
):
    queries = {}

    def read(url, **_):
        parsed = urllib.parse.urlsplit(url)
        queries[parsed.path] = urllib.parse.parse_qs(parsed.query)
        data = {"status": "ready"}
        if parsed.path.endswith("/meta"):
            data = {
                "date_from": start,
                "date_to": "2026-11-13",
                "default_publication_id": "weather",
                "data_version": "v1",
            }
        return io.BytesIO(json.dumps({"data": data, "meta": {"data_version": "v1"}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", read)
    exec(module().BUSINESS_PROBE, {})
    assert queries["/api/v1/products/overview"]["date_from"] == [product_start]
    assert queries["/api/v1/products/scores"]["date_from"] == [product_start]
    assert queries["/api/v1/promotion/overview"]["date_from"] == [promotion_start]


@pytest.mark.parametrize(
    "failure,reason",
    [("TimeoutError: timed out", "timeout"), ("ValueError: secret-value", "process_error")],
)
def test_failed_business_warm_reports_safe_reason_without_raw_process_output(
    capsys, failure, reason
):
    m = module()
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.use = lambda _: None
    runtime.inspect = lambda *_: None

    def fail(_):
        raise subprocess.CalledProcessError(
            1,
            ["secret-command"],
            output="secret-output",
            stderr=f"secret-trace\n{failure}\n",
        )

    runtime.docker = fail
    with pytest.raises(subprocess.CalledProcessError):
        runtime.warm(record())
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err
    summary = json.loads(captured.out)
    assert summary["event"] == "release_business_probe_failed"
    assert summary["reason"] == reason


def bootstrap_runtime(tmp_path, *, previous=True):
    """真实静态目录与发布快照校验；仅Docker边界用已知公开tar替代。"""
    m = module()
    spec = importlib.util.spec_from_file_location("bootstrap_assets", ROOT / "deploy/assets.py")
    asset_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asset_module)
    p = FakeProject()
    p.write(
        ".release.json",
        json.dumps({"version": 1, "manifest": manifest("b"), "compose": "e30="}).encode(),
    )
    if previous:
        p.write(
            ".release.previous.json",
            json.dumps({"version": 1, "manifest": manifest("a"), "compose": "e30="}).encode(),
        )
    validated = []
    controller_spec = importlib.util.spec_from_file_location(
        "bootstrap_controller", ROOT / "deploy/manage.py"
    )
    controller = importlib.util.module_from_spec(controller_spec)
    controller_spec.loader.exec_module(controller)

    def state(name):
        result = controller.Project.state(p, name)
        validated.append(name)
        return result

    p.state = state
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.project = p
    runtime.assets = asset_module.AssetStore(tmp_path)
    runtime.use = lambda _: None
    runtime.networks = lambda: {}
    runtime.one = lambda service, _: ("1" if service == "api" else "2") * 64
    calls = []

    def archive(image, identifier=None):
        calls.append(("archive", image, identifier))
        digit = "a" if image == manifest("a")["web"] else "b"
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as stream:
            for name, body in {
                "index.html": digit.encode(),
                f"assets/Page-{digit * 8}.js": digit.encode(),
            }.items():
                member = tarfile.TarInfo(name)
                member.size = len(body)
                stream.addfile(member, io.BytesIO(body))
        return data.getvalue()

    runtime.archive = archive
    runtime.docker = lambda args: calls.append(tuple(args)) or ""
    return runtime, p, calls, validated, asset_module


def test_first_handoff_imports_previous_snapshot_assets_before_new_html(tmp_path):
    runtime, _, calls, validated, _ = bootstrap_runtime(tmp_path)
    state = runtime.bootstrap()
    assert ".release.previous.json" in validated
    assert (tmp_path / "public/assets/Page-aaaaaaaa.js").read_bytes() == b"a"
    assert (tmp_path / "public/assets/Page-bbbbbbbb.js").read_bytes() == b"b"
    assert ("archive", manifest("b")["web"], "2" * 64) in calls
    assert ("archive", manifest("a")["web"], None) in calls
    assert ("pull", manifest("a")["web"]) in calls
    assert state["active"]["manifest"] == manifest("b")
    assert state["previous"] is None


def test_first_handoff_without_previous_snapshot_still_imports_active(tmp_path):
    runtime, _, calls, _, _ = bootstrap_runtime(tmp_path, previous=False)
    runtime.bootstrap()
    assert calls == [("archive", manifest("b")["web"], "2" * 64)]


def test_invalid_previous_snapshot_stops_bootstrap_before_container_reads(tmp_path):
    runtime, p, calls, _, _ = bootstrap_runtime(tmp_path)
    p.write(".release.previous.json", b'{"version": 2}')
    with pytest.raises((ValueError, AssertionError)):
        runtime.bootstrap()
    assert not calls


def test_previous_asset_import_obeys_shared_store_budget(tmp_path, monkeypatch):
    runtime, _, calls, _, asset_module = bootstrap_runtime(tmp_path, previous=False)
    runtime.bootstrap()
    initial = runtime.assets._inventory()
    runtime, _, calls, _, asset_module = bootstrap_runtime(tmp_path, previous=True)
    monkeypatch.setattr(asset_module, "MAX_BYTES", sum(map(len, initial.values())) + 50)
    with pytest.raises(ValueError, match="ASSET_BUDGET_EXCEEDED"):
        runtime.bootstrap()
    assert not any(call[0] in ("up", "stop", "kill", "rm") for call in calls)
    assert (tmp_path / "public/assets/Page-bbbbbbbb.js").read_bytes() == b"b"


def database_failure_log(*, code="QUERY_TIMEOUT", stage="sql_execute", request_ms="5501.25"):
    return (
        "2026-11-14T00:42:01.123456789Z WARNING:app.main:数据库请求失败 "
        "request_id=12345678-1234-1234-1234-123456789abc "
        "path=/api/v1/products/overview code=" + code + " type=BoardError,QueryCanceled "
        "stage="
        + stage
        + " request_elapsed_ms="
        + request_ms
        + " sql_elapsed_ms=5001.23 sql_total_ms=5200.01"
    )


def test_error_summary_only_emits_allowlisted_fields_and_small_samples():
    m = module()
    assert hasattr(m, "summarize_database_errors"), "退役前安全错误摘要尚未实现"
    secret = "DO_NOT_LEAK_SECRET"
    logs = "\n".join(
        [
            "2026-11-14T00:42:00Z SQL password=" + secret,
            database_failure_log().replace("type=BoardError,QueryCanceled", "type=" + secret),
            database_failure_log().replace(
                "path=/api/v1/products/overview", "path=/private/" + secret
            ),
            database_failure_log(code=secret),
            database_failure_log(stage=secret),
            database_failure_log().replace(
                "request_id=12345678-1234-1234-1234-123456789abc", "request_id=" + secret
            ),
            *([database_failure_log()] * 8),
        ]
    )
    value = m.summarize_database_errors(logs)
    assert secret not in json.dumps(value)
    assert value["matched"] == 10
    assert len(value["samples"]) == 5
    assert value["groups"] == [
        {
            "code": "QUERY_TIMEOUT",
            "stage": "sql_execute",
            "count": 10,
            "max_request_elapsed_ms": 5501.25,
            "max_sql_elapsed_ms": 5001.23,
            "max_sql_total_ms": 5200.01,
        }
    ]


def test_error_summary_does_not_classify_duration_as_pool_timeout():
    m = module()
    assert hasattr(m, "summarize_database_errors"), "退役前安全错误摘要尚未实现"
    value = m.summarize_database_errors(
        "\n".join(
            [
                database_failure_log(
                    code="DATABASE_BUSY", stage="pool_checkout", request_ms="5500.00"
                ),
                database_failure_log(request_ms="5500.00"),
            ]
        )
    )
    assert {(item["code"], item["stage"]) for item in value["groups"]} == {
        ("DATABASE_BUSY", "pool_checkout"),
        ("QUERY_TIMEOUT", "sql_execute"),
    }


def test_error_summary_bounds_lines_and_rejects_unstructured_tail():
    m = module()
    assert hasattr(m, "summarize_database_errors"), "退役前安全错误摘要尚未实现"
    value = m.summarize_database_errors(database_failure_log() + "\n" + ("ignored\n" * 1000))
    assert value["matched"] == 0 and value["samples"] == []


def test_old_api_error_summary_is_printed_before_container_removal(capsys):
    m = module()
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.assets = SimpleNamespace(touch=lambda *_args, **_kwargs: None)
    runtime.use = lambda _: None
    runtime.services = lambda _: ["a" * 64]
    runtime.inspect = lambda *_: {"State": {"Running": False}}
    runtime.wait_stopped = lambda *_: None
    calls = []

    def docker(args):
        calls.append(args)
        return database_failure_log() if args[0] == "logs" else ""

    runtime.docker = docker
    runtime.retire({"active": record(), "candidate": record("b", "green"), "legacy_web": None})
    output = capsys.readouterr().out
    assert output, "移除旧容器前应输出安全摘要"
    summary = json.loads(output)
    assert summary["event"] == "release_api_error_summary" and summary["matched"] == 1
    assert summary["retired_commit"] == "a" * 40 and summary["release_commit"] == "b" * 40
    assert calls[-2:] == [
        ["logs", "--since", "10m", "--tail", "1000", "--timestamps", "a" * 64],
        ["rm", "a" * 64],
    ]


def test_only_fixed_bounded_logs_command_merges_stderr(monkeypatch):
    spec = importlib.util.spec_from_file_location("logs_controller", ROOT / "deploy/manage.py")
    manage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manage)
    calls = []
    monkeypatch.setattr(
        manage.subprocess,
        "run",
        lambda args, **kwargs: (
            calls.append((args, kwargs)) or SimpleNamespace(stdout="safe fixture")
        ),
    )
    project = manage.Project()
    command = ["logs", "--since", "10m", "--tail", "1000", "--timestamps", "a" * 64]
    assert project.docker(command, {}) == "safe fixture"
    assert calls[0][1].get("stderr") == subprocess.STDOUT
    assert calls[0][1].get("stdout") == subprocess.PIPE
    with pytest.raises(ValueError):
        project.docker(["logs", "--follow", "a" * 64], {})
    assert len(calls) == 1


def test_log_read_failure_preserves_old_container_and_does_not_print_raw_output(capsys):
    m = module()
    runtime = m.Runtime.__new__(m.Runtime)
    runtime.assets = SimpleNamespace(touch=lambda *_args, **_kwargs: None)
    runtime.use = lambda _: None
    runtime.services = lambda _: ["a" * 64]
    runtime.inspect = lambda *_: {"State": {"Running": False}}
    runtime.wait_stopped = lambda *_: None
    calls = []

    def docker(args):
        calls.append(args)
        if args[0] == "logs":
            raise subprocess.CalledProcessError(1, args, output="DO_NOT_LEAK_SECRET")
        return ""

    runtime.docker = docker
    with pytest.raises(subprocess.CalledProcessError):
        runtime.retire({"active": record(), "candidate": record("b", "green"), "legacy_web": None})
    assert not any(args[0] == "rm" for args in calls)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
