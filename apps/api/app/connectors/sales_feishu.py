"""F01最小字段只读接收；使用既有user身份，分页rev/时区/字段ID及双读内容均核对。"""

import json
import os
import shutil
import subprocess
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import REFUND_BASE, REFUND_FIELDS, REFUND_TABLE, SOURCE_MAX_ROWS
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import MAX_ATTEMPTS, validate_read_window


def cli_command():
    binary = os.environ.get("LARK_CLI_BIN") or shutil.which("lark-cli")
    return (
        [binary]
        if binary
        else ["npm", "exec", "--yes", "--package=@larksuite/cli@1.0.98", "--", "lark-cli"]
    )


def validate_column_types(columns):
    """字段ID固定且类型须与已核七字段相同；改名可映射，变型拒绝。"""
    by_id = {value.get("field_id"): value.get("field_type") for value in columns.values()}
    expected = ("datetime", "text", "text", "text", "number", "number", "formula")
    if any(by_id.get(key) != kind for key, kind in zip(REFUND_FIELDS, expected, strict=True)):
        raise SalesError("FEISHU_FIELD_SCHEMA_CHANGED")


class FeishuRefundReader:
    def __init__(self, *, root: Path, heartbeat=lambda: None):
        self.root, self.heartbeat = Path(root).resolve(), heartbeat
        self.folder = self.root / "var/ingestion/m3-feishu-staging" / str(uuid4())
        self.folder.mkdir(mode=0o700, parents=True)
        self.page = self._page

    @classmethod
    def for_test(cls, page):
        reader = cls.__new__(cls)
        reader.page, reader.heartbeat = page, lambda: None
        return reader

    def _page(self, window, offset):
        destination = self.folder / (str(uuid4()) + ".ndjson")
        conditions = {
            "logic": "and",
            "conditions": [
                ["平台", "==", "拼多多"],
                ["日期", ">", f"ExactDate({window.start - timedelta(days=1)} 23:59:59.999)"],
                ["日期", "<", f"ExactDate({window.end + timedelta(days=1)} 00:00:00)"],
            ],
        }
        arguments = cli_command() + [
            "base",
            "+record-list",
            "--base-token",
            REFUND_BASE,
            "--table-id",
            REFUND_TABLE,
            "--as",
            "user",
        ]
        for field in REFUND_FIELDS:
            arguments += ["--field-id", field]
        arguments += [
            "--filter-json",
            json.dumps(conditions, ensure_ascii=False),
            "--offset",
            str(offset),
            "--limit",
            "2000",
            "--format",
            "ndjson",
            "--output",
            str(destination.relative_to(self.root)),
        ]
        result = subprocess.run(
            arguments, cwd=self.root, capture_output=True, text=True, timeout=50
        )
        if result.returncode:
            raise SalesError("FEISHU_READ_FAILED", 503)
        rows = [
            json.loads(line, parse_float=Decimal) for line in destination.read_text().splitlines()
        ]
        manifest_path = destination.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text())
        destination.chmod(0o600)
        manifest_path.chmod(0o600)
        columns = manifest.get("columns", {})
        validate_column_types(columns)
        by_id = {value.get("field_id"): name for name, value in columns.items()}
        expected_names = ("日期", "平台", "部门", "店铺", "统计退款金额", "统计金额", "退款占比")
        if any(field not in by_id for field in REFUND_FIELDS):
            raise SalesError("FEISHU_FIELD_SCHEMA_CHANGED")
        mapping = {
            by_id[field]: name for field, name in zip(REFUND_FIELDS, expected_names, strict=True)
        }
        normalized = [
            {
                "record_id": row["record_id"],
                **{canonical: row.get(source) for source, canonical in mapping.items()},
            }
            for row in rows
        ]
        manifest["field_mapping"] = {field: by_id[field] for field in REFUND_FIELDS}
        return normalized, manifest

    def _scan(self, window):
        rows, offset, revision, field_mapping = [], 0, None, None
        while True:
            self.heartbeat()
            page, manifest = self.page(window, offset)
            if (
                manifest.get("base_token") != REFUND_BASE
                or manifest.get("table_id") != REFUND_TABLE
                or manifest.get("timezone") != "Asia/Shanghai"
                or manifest.get("rev") is None
                or manifest.get("records_count") != len(page)
            ):
                raise SalesError("FEISHU_MANIFEST_INVALID")
            if revision is not None and (
                manifest["rev"] != revision or manifest.get("field_mapping") != field_mapping
            ):
                raise SalesError("FEISHU_REVISION_CHANGED", 503)
            revision, field_mapping = manifest["rev"], manifest.get("field_mapping")
            rows.extend(page)
            if len(rows) > SOURCE_MAX_ROWS:
                raise SalesError("SOURCE_ROW_LIMIT")
            if manifest.get("has_more") is False:
                break
            following = manifest.get("next_offset")
            if not page or not isinstance(following, int) or following <= offset:
                raise SalesError("FEISHU_PAGINATION_INCOMPLETE")
            offset = following
        return rows, dict(rev=revision, timezone="Asia/Shanghai", field_mapping=field_mapping)

    def read_stable(self, window):
        validate_read_window(window)
        for _ in range(MAX_ATTEMPTS):
            rows, meta = self._scan(window)
            before = normalize_source("shop_refunds", rows, window, self.heartbeat).fingerprint
            rows, after_meta = self._scan(window)
            after = normalize_source("shop_refunds", rows, window, self.heartbeat)
            if before == after.fingerprint and meta == after_meta:
                return replace(after, source_metadata=after_meta)
        raise SalesError("SOURCE_CHANGED_DURING_READ", 503)
