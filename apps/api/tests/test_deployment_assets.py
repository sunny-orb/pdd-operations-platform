"""公开构建产物保留的真实文件系统边界；不读取经营数据。"""

import importlib.util
import io
import os
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_assets():
    path = ROOT / "deploy/assets.py"
    assert path.exists(), "公开静态产物安全保留模块尚未实现"
    spec = importlib.util.spec_from_file_location("deployment_assets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive(files=None, extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as stream:
        for name, value in (
            files or {"index.html": b"<html>app</html>", "assets/app-12345678.js": b"old"}
        ).items():
            item = tarfile.TarInfo(name)
            item.size = len(value)
            stream.addfile(item, io.BytesIO(value))
        if extra is not None:
            stream.addfile(extra)
    return output.getvalue()


def test_retained_assets_survive_new_release_and_rollback(tmp_path):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    first = store.prepare(archive(), "a" * 40, now=0, protected=set())
    second = store.prepare(
        archive({"index.html": b"new", "assets/new-87654321.js": b"new"}),
        "b" * 40,
        now=1,
        protected={"a" * 40},
    )
    assert first["commit"] == "a" * 40 and second["commit"] == "b" * 40
    assert (tmp_path / "public/assets/app-12345678.js").read_bytes() == b"old"
    assert (tmp_path / "public/assets/new-87654321.js").read_bytes() == b"new"
    assert (
        tmp_path / "public/releases" / ("a" * 40) / "index.html"
    ).read_bytes() == b"<html>app</html>"


@pytest.mark.parametrize(
    "name",
    ["../outside", "/outside", "assets/../../outside", "assets/evil\\name", "assets/evil\nname"],
)
def test_archive_rejects_escaping_or_ambiguous_paths_before_writing(tmp_path, name):
    module = load_assets()
    with pytest.raises(ValueError):
        module.AssetStore(tmp_path).prepare(
            archive({"index.html": b"ok", name: b"bad"}), "a" * 40, now=0, protected=set()
        )
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "public/releases" / ("a" * 40)).exists()


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_archive_rejects_links_and_special_files(tmp_path, kind):
    module = load_assets()
    item = tarfile.TarInfo("assets/evil")
    item.type, item.linkname = kind, "../../outside"
    with pytest.raises(ValueError):
        module.AssetStore(tmp_path).prepare(archive(extra=item), "a" * 40, now=0, protected=set())


def test_same_asset_path_with_different_content_is_rejected(tmp_path):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    store.prepare(archive(), "a" * 40, now=0, protected=set())
    with pytest.raises(ValueError, match="ASSET_CONTENT_CONFLICT"):
        store.prepare(
            archive({"index.html": b"new", "assets/app-12345678.js": b"changed"}),
            "b" * 40,
            now=1,
            protected={"a" * 40},
        )
    assert (tmp_path / "public/assets/app-12345678.js").read_bytes() == b"old"


def test_budget_refusal_preserves_active_files(tmp_path, monkeypatch):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    store.prepare(archive(), "a" * 40, now=0, protected=set())
    before = (tmp_path / "public/catalog.json").read_bytes()
    monkeypatch.setattr(module, "MAX_BYTES", 32)
    with pytest.raises(ValueError, match="ASSET_BUDGET_EXCEEDED"):
        store.prepare(
            archive({"index.html": b"new", "assets/new-87654321.js": b"x" * 64}),
            "b" * 40,
            now=1,
            protected={"a" * 40},
        )
    assert (tmp_path / "public/catalog.json").read_bytes() == before


def test_expiration_preserves_active_previous_and_deduplicated_assets(tmp_path):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    for digit in "abc":
        store.prepare(
            archive({"index.html": digit.encode(), f"assets/{digit}-12345678.js": digit.encode()}),
            digit * 40,
            now=0,
            protected=set(),
        )
    store.prepare(
        archive({"index.html": b"d", "assets/d-12345678.js": b"d"}),
        "d" * 40,
        now=module.RETENTION_SECONDS + 1,
        protected={"b" * 40, "c" * 40},
    )
    store.prune(now=module.RETENTION_SECONDS + 1, protected={"b" * 40, "c" * 40, "d" * 40})
    assert not (tmp_path / "public/assets/a-12345678.js").exists()
    assert all((tmp_path / f"public/assets/{digit}-12345678.js").exists() for digit in "bcd")


@pytest.mark.parametrize("linked", ["directory", "symlink", "hardlink"])
def test_existing_links_are_refused_without_touching_outside(tmp_path, linked):
    module = load_assets()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"protected")
    if linked == "directory":
        (tmp_path / "public").symlink_to(outside, target_is_directory=True)
    else:
        store = module.AssetStore(tmp_path)
        store.prepare(archive(), "a" * 40, now=0, protected=set())
        path = tmp_path / "public/assets/app-12345678.js"
        path.unlink()
        path.symlink_to(marker) if linked == "symlink" else os.link(marker, path)
    with pytest.raises(ValueError):
        module.AssetStore(tmp_path).prepare(archive(), "b" * 40, now=1, protected={"a" * 40})
    assert marker.read_bytes() == b"protected"


def test_constructing_asset_store_does_not_leak_directory_handles(tmp_path):
    module = load_assets()
    descriptors = Path("/dev/fd")
    before = len(list(descriptors.iterdir()))
    for _ in range(20):
        module.AssetStore(tmp_path)
    assert len(list(descriptors.iterdir())) <= before + 1


def test_prune_removes_only_empty_registered_release_directories(tmp_path):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    store.prepare(archive(), "a" * 40, now=0, protected=set())
    store.prune(now=module.RETENTION_SECONDS + 1, protected=set())
    assert not (tmp_path / "public/releases" / ("a" * 40)).exists()
    assert (tmp_path / "public/releases").is_dir()


def test_interrupted_prune_resumes_without_catalog_drift(tmp_path, monkeypatch):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    store.prepare(archive(), "a" * 40, now=0, protected=set())
    original = store._write

    def interrupt(relative, data):
        if relative == "public/catalog.json":
            raise OSError("interrupted after unlink")
        return original(relative, data)

    monkeypatch.setattr(store, "_write", interrupt)
    with pytest.raises(OSError):
        store.prune(now=module.RETENTION_SECONDS + 1, protected=set())
    monkeypatch.setattr(store, "_write", original)
    store.prune(now=module.RETENTION_SECONDS + 1, protected=set())
    store.prepare(archive(), "b" * 40, now=module.RETENTION_SECONDS + 2, protected=set())
    assert (tmp_path / "public/assets/app-12345678.js").read_bytes() == b"old"


def test_retention_starts_when_long_running_release_stops_serving(tmp_path):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    day = 86400
    store.prepare(archive(), "a" * 40, now=0, protected=set())
    store.prepare(archive({"index.html": b"B"}), "b" * 40, now=20 * day, protected={"a" * 40})
    assert hasattr(store, "touch"), "交接时需更新最后服务时间"
    store.touch("a" * 40, now=20 * day)
    store.prepare(archive({"index.html": b"C"}), "c" * 40, now=21 * day, protected={"b" * 40})
    store.prune(now=21 * day, protected={"b" * 40, "c" * 40})
    assert (tmp_path / "public/assets/app-12345678.js").exists()


def test_nested_prune_can_resume_after_directory_removal(tmp_path, monkeypatch):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    store.prepare(
        archive({"index.html": b"a", "icons/nested/icon.svg": b"svg"}),
        "a" * 40,
        now=0,
        protected=set(),
    )
    original = store._write

    def interrupt(name, data):
        if name == "public/catalog.json":
            raise OSError("after directory cleanup")
        original(name, data)

    monkeypatch.setattr(store, "_write", interrupt)
    with pytest.raises(OSError):
        store.prune(now=module.RETENTION_SECONDS + 1, protected=set())
    monkeypatch.setattr(store, "_write", original)
    store.prune(now=module.RETENTION_SECONDS + 1, protected=set())
    assert not (tmp_path / "public/releases" / ("a" * 40)).exists()


def test_reconcile_only_removes_reserved_safe_uncommitted_files(tmp_path):
    module = load_assets()
    store = module.AssetStore(tmp_path)
    store.prepare(archive(), "a" * 40, now=0, protected=set())
    pending = tmp_path / "public/assets" / (".pending-" + "1" * 32)
    pending.write_bytes(b"uncommitted public bytes")
    store.prune(now=1, protected={"a" * 40})
    assert not pending.exists()
    marker = tmp_path / "marker"
    marker.write_bytes(b"outside")
    pending.symlink_to(marker)
    with pytest.raises(ValueError):
        store.prune(now=1, protected={"a" * 40})
    assert pending.is_symlink() and marker.read_bytes() == b"outside"


def test_public_files_remain_readable_under_private_umask(tmp_path):
    module = load_assets()
    previous = os.umask(0o077)
    try:
        store = module.AssetStore(tmp_path)
        store.prepare(archive(), "a" * 40, now=0, protected=set())
    finally:
        os.umask(previous)
    assert (tmp_path / "public").stat().st_mode & 0o777 == 0o755
    assert (
        tmp_path / "public/releases" / ("a" * 40) / "index.html"
    ).stat().st_mode & 0o777 == 0o644
