"""只管理项目公开网页产物；不读取运行归档、来源或数据库文件。"""

import errno
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import uuid

MAX_BYTES = 64 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_FILES = 4096


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_name(name):
    """tar与目录共用严格相对路径；拒绝转义、控制字符和隐藏路径。"""
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    require(name and len(name) <= 240, "INVALID_ASSET_PATH")
    require(not name.startswith("/") and "\\" not in name, "INVALID_ASSET_PATH")
    require(
        all(
            re.fullmatch(r"[A-Za-z0-9_@.+-]+", p)
            and p not in (".", "..")
            and not p.startswith(".")
            for p in name.split("/")
        ),
        "INVALID_ASSET_PATH",
    )
    return name


class AssetStore:
    """公开产物目录，输入已校验项目路径；所有读写检查所有者、类型与链接。"""

    def __init__(self, root):
        self.root = root
        self.uid = os.geteuid()
        for directory in ("public", "public/assets", "public/releases"):
            os.close(self._directory(directory, create=True))

    def _directory(self, relative, *, create=False):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.split("/"):
                safe_name(part)
                made = False
                if create:
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=fd)
                        made = True
                    except FileExistsError:
                        pass
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                require(
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == self.uid
                    and not info.st_mode & 0o022,
                    "UNSAFE_ASSET_DIRECTORY",
                )
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                if made:
                    os.fchmod(child, 0o755)
                os.close(fd)
                fd = child
                require(
                    os.fstat(fd).st_mode & 0o555 == 0o555, "UNREADABLE_PUBLIC_DIRECTORY"
                )
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _file(self, relative):
        parent, name = relative.rsplit("/", 1)
        fd = self._directory(parent)
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == self.uid
                and not info.st_mode & 0o022,
                "UNSAFE_ASSET_FILE",
            )
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            require(len(data) <= MAX_BYTES, "ASSET_BUDGET_EXCEEDED")
            return data
        finally:
            os.close(fd)

    def _write(self, relative, data):
        parent, name = relative.rsplit("/", 1)
        fd = self._directory(parent, create=True)
        temporary = ".pending-" + uuid.uuid4().hex
        try:
            try:
                self._file(relative)
            except FileNotFoundError:
                pass
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=fd,
            )
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.close(fd)

    def _inventory(self, relative="public", result=None, total=None):
        result = {} if result is None else result
        total = [0] if total is None else total
        fd = self._directory(relative)
        try:
            for name in os.listdir(fd):
                safe_name(name)
                path = relative + "/" + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    self._inventory(path, result, total)
                else:
                    total[0] += info.st_size
                    require(total[0] <= MAX_BYTES, "ASSET_BUDGET_EXCEEDED")
                    result[path] = self._file(path)
                require(len(result) <= MAX_FILES * 16, "ASSET_FILE_LIMIT")
            return result
        finally:
            os.close(fd)

    def _catalog(self):
        try:
            value = json.loads(self._file("public/catalog.json"))
        except FileNotFoundError:
            return {}
        require(isinstance(value, dict), "INVALID_ASSET_CATALOG")
        for commit, entry in value.items():
            require(
                re.fullmatch("[0-9a-f]{40}", commit) and isinstance(entry, dict),
                "INVALID_ASSET_CATALOG",
            )
            require(
                set(entry) == {"created", "files"}
                and isinstance(entry["created"], (int, float))
                and isinstance(entry["files"], dict),
                "INVALID_ASSET_CATALOG",
            )
            for name, digest in entry["files"].items():
                safe_name(name)
                require(
                    isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest),
                    "INVALID_ASSET_CATALOG",
                )
        return value

    @staticmethod
    def destination(commit, name):
        return (
            "public/" + name
            if name.startswith("assets/")
            else f"public/releases/{commit}/{name}"
        )

    def prepare(self, archive, commit, *, now, protected):
        """校验全部tar再写入不可变产物；超预算拒绝，准备阶段绝不删除旧产物。"""
        require(re.fullmatch("[0-9a-f]{40}", commit), "INVALID_ASSET_COMMIT")
        require(len(archive) <= MAX_BYTES + MAX_FILES * 2048, "ASSET_BUDGET_EXCEEDED")
        files = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as source:
            for member in source:
                if member.isdir() and member.name in (".", "./"):
                    continue
                name = safe_name(member.name)
                require(member.isdir() or member.isfile(), "INVALID_ASSET_TYPE")
                if member.isdir():
                    continue
                require(
                    name not in files and len(files) < MAX_FILES,
                    "DUPLICATE_OR_EXCESS_ASSET",
                )
                total += member.size
                require(
                    0 <= member.size <= MAX_BYTES and total <= MAX_BYTES,
                    "ASSET_BUDGET_EXCEEDED",
                )
                files[name] = source.extractfile(member).read()
        require("index.html" in files and files["index.html"], "MISSING_ASSET_INDEX")
        inventory = self._inventory()
        catalog = self._catalog()
        for old_commit, entry in catalog.items():
            for name, digest in entry["files"].items():
                content = inventory.get(self.destination(old_commit, name))
                require(
                    content is not None
                    and hashlib.sha256(content).hexdigest() == digest,
                    "ASSET_CATALOG_DRIFT",
                )
        digests = {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        }
        if commit in catalog:
            require(catalog[commit]["files"] == digests, "RELEASE_CONTENT_CONFLICT")
        planned = dict(inventory)
        for name, data in files.items():
            path = self.destination(commit, name)
            require(
                path not in inventory or inventory[path] == data,
                "ASSET_CONTENT_CONFLICT",
            )
            planned[path] = data
        # 整个受控目录的实际字节均计入，未完成写入也不能绕过预算。
        catalog[commit] = {
            "created": max(now, catalog.get(commit, {}).get("created", now)),
            "files": digests,
        }
        metadata = json.dumps(catalog, sort_keys=True).encode()
        planned["public/catalog.json"] = metadata
        require(sum(map(len, planned.values())) <= MAX_BYTES, "ASSET_BUDGET_EXCEEDED")
        for name, data in files.items():
            path = self.destination(commit, name)
            if path not in inventory:
                self._write(path, data)
        self._write("public/catalog.json", metadata)
        return {
            "commit": commit,
            "root": f"/srv/public/releases/{commit}",
            "assets": sorted(name for name in files if name.startswith("assets/")),
        }

    def prune(self, *, now, protected):
        """仅切换成功且旧请求结束后调用；保留最近七天及明确保护版本。"""
        self._recover_pending()
        inventory = self._inventory()
        catalog = self._catalog()
        kept = {
            commit: entry
            for commit, entry in catalog.items()
            if commit in protected or now - entry["created"] <= RETENTION_SECONDS
        }
        referenced = {
            self.destination(commit, name)
            for commit, entry in kept.items()
            for name in entry["files"]
        }
        obsolete = {
            self.destination(commit, name)
            for commit, entry in catalog.items()
            if commit not in kept
            for name in entry["files"]
        } - referenced
        # 元信息先保留旧引用；每个删除目标在锁内重新检查，不扫除未登记文件。
        for relative in sorted(obsolete):
            if relative not in inventory:
                continue
            self._file(relative)
            parent, name = relative.rsplit("/", 1)
            fd = self._directory(parent)
            try:
                os.unlink(name, dir_fd=fd)
            finally:
                os.close(fd)
        # 仅尝试移除刚才登记文件的空父目录；不递归删除未知文件或入口根目录。
        directories = set()
        for path in obsolete:
            parent = path.rsplit("/", 1)[0]
            while parent not in ("public", "public/assets", "public/releases"):
                directories.add(parent)
                parent = parent.rsplit("/", 1)[0]
        for relative in sorted(
            directories, key=lambda path: path.count("/"), reverse=True
        ):
            parent, name = relative.rsplit("/", 1)
            fd = None
            try:
                fd = self._directory(parent)
                os.close(self._directory(relative))
                os.rmdir(name, dir_fd=fd)
            except FileNotFoundError:
                pass
            except OSError as error:
                if error.errno != errno.ENOTEMPTY:
                    raise
            finally:
                if fd is not None:
                    os.close(fd)
        self._write("public/catalog.json", json.dumps(kept, sort_keys=True).encode())

    def touch(self, commit, *, now):
        """保留窗口从最后退出服务起算；回切撤出的新HTML也至少保留七天。"""
        catalog = self._catalog()
        if commit in catalog:
            catalog[commit]["created"] = max(now, catalog[commit]["created"])
            self._write(
                "public/catalog.json", json.dumps(catalog, sort_keys=True).encode()
            )

    def _recover_pending(self, relative="public"):
        """恢复本模块保留的临时文件命名空间；链接、目录及未知隐藏文件仍拒绝。"""
        fd = self._directory(relative)
        try:
            for name in os.listdir(fd):
                path = relative + "/" + name
                if re.fullmatch(r"\.pending-[0-9a-f]{32}", name):
                    self._file(path)
                    os.unlink(name, dir_fd=fd)
                else:
                    safe_name(name)
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        self._recover_pending(path)
        finally:
            os.close(fd)
