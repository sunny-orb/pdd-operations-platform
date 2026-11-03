"""原版拼多多渠道GMV目标只读CLI投影；逐页/双读校验，不读取退款字段。"""

import json
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.connectors.sales_feishu import cli_command
from app.modules.sales.contracts import SalesError
from app.modules.sales.source_policy import MAX_ATTEMPTS
from app.modules.sales.targets import TARGET_BASE, TARGET_FIELDS, TARGET_TABLE, normalize_targets

MAX_TARGET_ROWS = 1000


def validate_fields(columns):
    by_id = {v.get("field_id"): (name, v.get("field_type")) for name, v in columns.items()}
    if any(
        fid not in by_id or by_id[fid][1] != kind for fid, (_name, kind) in TARGET_FIELDS.items()
    ):
        raise SalesError("FEISHU_TARGET_FIELD_SCHEMA_CHANGED")
    return {fid: by_id[fid][0] for fid in TARGET_FIELDS}


class FeishuTargetReader:
    def __init__(self, *, root, heartbeat=lambda: None):
        self.root, self.heartbeat = Path(root).resolve(), heartbeat
        self.folder = self.root / "var/ingestion/m3-target-staging" / str(uuid4())
        self.folder.mkdir(mode=0o700, parents=True)
        self.page = self._page

    @classmethod
    def for_test(cls, page):
        reader = cls.__new__(cls)
        reader.page, reader.heartbeat = page, lambda: None
        return reader

    def _page(self, offset):
        destination = self.folder / (str(uuid4()) + ".ndjson")
        args = cli_command() + [
            "base",
            "+record-list",
            "--base-token",
            TARGET_BASE,
            "--table-id",
            TARGET_TABLE,
            "--as",
            "user",
        ]
        for fid in TARGET_FIELDS:
            args += ["--field-id", fid]
        args += [
            "--offset",
            str(offset),
            "--limit",
            "2000",
            "--format",
            "ndjson",
            "--output",
            str(destination.relative_to(self.root)),
        ]
        result = subprocess.run(args, cwd=self.root, capture_output=True, text=True, timeout=50)
        if result.returncode:
            raise SalesError("FEISHU_TARGET_READ_FAILED", 503)
        meta_path = destination.with_suffix(".manifest.json")
        destination.chmod(0o600)
        meta_path.chmod(0o600)
        meta = json.loads(meta_path.read_text())
        mapping = validate_fields(meta.get("columns", {}))
        source = [
            json.loads(line, parse_float=Decimal)
            for line in destination.read_text().splitlines()
            if line
        ]
        rows = [
            {
                "record_id": r.get("record_id"),
                **{name: r.get(mapping[fid]) for fid, (name, _kind) in TARGET_FIELDS.items()},
            }
            for r in source
        ]
        return rows, meta

    def _scan(self):
        rows, offset, reference = [], 0, None
        while True:
            self.heartbeat()
            page, meta = self.page(offset)
            mapping = validate_fields(meta.get("columns", {}))
            if (
                meta.get("base_token") != TARGET_BASE
                or meta.get("table_id") != TARGET_TABLE
                or meta.get("timezone") != "Asia/Shanghai"
                or meta.get("rev") is None
                or meta.get("records_count") != len(page)
            ):
                raise SalesError("FEISHU_TARGET_MANIFEST_INVALID")
            current = dict(
                base_token=TARGET_BASE,
                table_id=TARGET_TABLE,
                timezone="Asia/Shanghai",
                rev=meta["rev"],
                field_mapping=mapping,
                field_types={fid: kind for fid, (_name, kind) in TARGET_FIELDS.items()},
            )
            if reference is not None and reference != current:
                raise SalesError("FEISHU_TARGET_REVISION_CHANGED", 503)
            reference = current
            rows.extend(page)
            if len(rows) > MAX_TARGET_ROWS:
                raise SalesError("FEISHU_TARGET_ROW_LIMIT")
            if meta.get("has_more") is False:
                return rows, {**reference, "stable_full_scope": True, "records_count": len(rows)}
            following = meta.get("next_offset")
            if (
                meta.get("has_more") is not True
                or not page
                or not isinstance(following, int)
                or following <= offset
            ):
                raise SalesError("FEISHU_TARGET_PAGINATION_INCOMPLETE")
            offset = following

    def read_stable(self, *, basis="original_gmv"):
        for _ in range(MAX_ATTEMPTS):
            before_rows, before_meta = self._scan()
            before = normalize_targets(before_rows, basis=basis, source_metadata=before_meta)
            after_rows, after_meta = self._scan()
            after = normalize_targets(after_rows, basis=basis, source_metadata=after_meta)
            # 全表四字段投影双读一致；财年之外仅归档，不进入目标合计。
            if before.raw_rows == after.raw_rows and before_meta == after_meta:
                return replace(after, source_metadata=after_meta)
        raise SalesError("FEISHU_TARGET_CHANGED_DURING_READ", 503)
