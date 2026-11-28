"""公共 SOP 的服务端登记。模型按短目录选文，可引用的内容来自所选文章本次核验的修订。"""

import os
import re
import tomllib
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.connectors.forecast_sop import (
    DOC_URL,
    FeishuSopReader,
    ForecastSourceError,
    validated_document_id,
)

from .public_sop_passages import relevant_passages
from .sop_transport import WorkerSopReader, worker_token

# 每个进程最多保留八篇近期使用的原文，大文仍可按次读取和摘句。
_CACHE_DOCUMENTS = 8
_CACHE_ARTICLE_CHARS = 128_000


@dataclass(frozen=True)
class PublicSopEntry:
    key: str
    document_id: str
    title: str
    description: str


class PublicSopSession:
    """一次提问固定每篇文档的首次读取结果，各个关键词可在同版原文中重新取证。"""

    def __init__(self):
        self._reads = {}
        self._lock = Lock()

    def _read(self, library, entry) -> tuple[int, str]:
        first = Future()
        with self._lock:
            shared = self._reads.setdefault((library, entry.key), first)
        if shared is first:
            try:
                first.set_result(library._current_text(entry))
            except BaseException as exc:
                # 等待同篇的请求也必须收到这次核验的失败。
                first.set_exception(exc)
        return shared.result()


class PublicSopLibrary:
    def __init__(self, entries, reader_factory):
        self._entries = {}
        for entry in entries:
            if entry.key in self._entries or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", entry.key):
                raise ValueError("PUBLIC_SOP_KEY")
            validated_document_id(entry.document_id)
            self._entries[entry.key] = entry
        self._titles = {"".join(entry.title.casefold().split()) for entry in self._entries.values()}
        self._reader = reader_factory
        self._locks = {key: Lock() for key in self._entries}
        self._cached: OrderedDict[str, tuple[int, str]] = OrderedDict()
        self._cache_lock = Lock()

    def catalog_entries(self) -> list[dict]:
        return [
            {"key": entry.key, "title": entry.title, "description": entry.description}
            for entry in self._entries.values()
        ]

    def _current_text(self, entry) -> tuple[int, str]:
        """每次提问重新核验访问权限与修订。同一篇的并发请求仅下载一次同版正文。"""
        with self._locks[entry.key]:
            try:
                reader = self._reader(entry)
                revision = reader.revision()
                with self._cache_lock:
                    cached = self._cached.get(entry.key)
                    if cached is not None and cached[0] == revision:
                        self._cached.move_to_end(entry.key)
                        return cached
                    self._cached.pop(entry.key, None)
                content = reader.markdown(revision)
            except ForecastSourceError:
                with self._cache_lock:
                    self._cached.pop(entry.key, None)
                raise
            if len(content) <= _CACHE_ARTICLE_CHARS:
                with self._cache_lock:
                    self._cached[entry.key] = (revision, content)
                    if len(self._cached) > _CACHE_DOCUMENTS:
                        self._cached.popitem(last=False)
            return revision, content

    def read_public_sop(self, key: str, query: str, *, session: PublicSopSession | None = None):
        """按登记键核验原文，返回带飞书修订的摘句或当次不可用的原因。"""
        entry = self._entries.get(key) if isinstance(key, str) else None
        if entry is None:
            return _empty_result("not_registered")
        query = (query or "")[:1000]
        question = "".join(query.casefold().split())
        named = {name for name in re.findall(r"《([^》]+)》", question) if name in self._titles}
        if named and "".join(entry.title.casefold().split()) not in question:
            return _empty_result("no_match", key=key, title=entry.title)
        try:
            if session is None:
                revision, content = self._current_text(entry)
            else:
                revision, content = session._read(self, entry)
        except ForecastSourceError as exc:
            return _empty_result("unavailable", key=entry.key, title=entry.title, reason=exc.reason)
        state, passages = relevant_passages(content, query, entry.title, entry.description)
        return {
            "key": key,
            "title": entry.title,
            "source_url": DOC_URL + entry.document_id,
            "revision": revision,
            "state": state,
            "passages": passages,
        }


def _registered_entries():
    with Path(__file__).with_suffix(".toml").open("rb") as stream:
        return [PublicSopEntry(key, **fields) for key, fields in tomllib.load(stream).items()]


def _empty_result(state, **fields):
    return {
        "key": None,
        "title": None,
        "source_url": None,
        "revision": None,
        "state": state,
        "passages": [],
        **fields,
    }


def _reader(entry):
    if os.environ.get("APP_ENV") == "production":
        return WorkerSopReader(entry.key)
    return FeishuSopReader(entry.document_id, identity="user")


_entries = _registered_entries()
_library = PublicSopLibrary(_entries, _reader)


def registered_entry(key: str | None) -> PublicSopEntry | None:
    return next((entry for entry in _entries if entry.key == key), None)


def catalog_entries() -> list[dict]:
    """仅返回服务端已登记的短标题和适用范围，发现资料不请求飞书。"""
    if os.environ.get("APP_ENV") == "production" and not worker_token():
        return []
    return _library.catalog_entries()


def read_public_sop(key: str, query: str, *, session: PublicSopSession | None = None) -> dict:
    """本机读取本次选中的授权 SOP；身份、飞书地址及文档 ID 均由服务端决定。"""
    if os.environ.get("APP_ENV") == "production" and not worker_token():
        return _empty_result("unavailable", reason="disabled")
    return _library.read_public_sop(key, query, session=session)
