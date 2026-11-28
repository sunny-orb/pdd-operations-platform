"""F01最小字段只读接收；使用既有user身份，分页rev/时区/字段ID及双读内容均核对。"""

import json
import os
import shutil
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.modules.archive_store import run_source_cli, temporary_pages
from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import REFUND_BASE, REFUND_FIELDS, REFUND_TABLE, SOURCE_MAX_ROWS
from app.modules.sales.f01_gmv import (
    ADJUSTMENT_FIELD,
    ADJUSTMENT_LABEL,
    F01_GMV_REVISION,
    GMV_FIELD,
    GMV_LABEL,
    normalize_f01_gmv,
)
from app.modules.sales.source_normalization import normalize_source
from app.modules.sales.source_policy import MAX_ATTEMPTS, validate_read_window


def cli_command():
    binary = os.environ.get("LARK_CLI_BIN") or shutil.which("lark-cli")
    return (
        [binary]
        if binary
        else ["npm", "exec", "--yes", "--package=@larksuite/cli@1.0.98", "--", "lark-cli"]
    )


F01_FIELDS = (*REFUND_FIELDS, GMV_FIELD, ADJUSTMENT_FIELD)
FIELD_SCHEMA = dict(
    zip(
        F01_FIELDS,
        (
            ("日期", "datetime"),
            ("平台", "text"),
            ("部门", "text"),
            ("店铺", "text"),
            ("统计退款金额", "number"),
            ("统计金额", "number"),
            ("退款占比", "formula"),
            (GMV_LABEL, "formula"),
            (ADJUSTMENT_LABEL, "number"),
        ),
        strict=True,
    )
)


def validate_column_types(columns, fields=REFUND_FIELDS):
    """字段 ID 和数值/公式类型固定；来源改名可映射，类型改变拒绝。"""
    by_id = {value.get("field_id"): value.get("field_type") for value in columns.values()}
    if any(by_id.get(key) != FIELD_SCHEMA[key][1] for key in fields):
        raise SalesError("FEISHU_FIELD_SCHEMA_CHANGED")


class FeishuRefundReader:
    def __init__(self, *, root: Path, include_gmv=False, heartbeat=lambda: None):
        self.root, self.heartbeat = Path(root).resolve(), heartbeat
        self.fields = F01_FIELDS if include_gmv else REFUND_FIELDS
        self.folder = self.root / "var/ingestion/m3-feishu-staging" / str(uuid4())
        self.folder.mkdir(mode=0o700, parents=True)
        self.page = self._page

    @classmethod
    def for_test(cls, page, *, include_gmv=False):
        reader = cls.__new__(cls)
        reader.page, reader.heartbeat = page, lambda: None
        reader.fields = F01_FIELDS if include_gmv else REFUND_FIELDS
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
        for field in self.fields:
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
        result = run_source_cli(
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
        validate_column_types(columns, self.fields)
        by_id = {value.get("field_id"): name for name, value in columns.items()}
        mapping = {by_id[field]: FIELD_SCHEMA[field][0] for field in self.fields}
        normalized = [
            {
                "record_id": row["record_id"],
                **{canonical: row.get(source) for source, canonical in mapping.items()},
            }
            for row in rows
        ]
        manifest["field_mapping"] = {field: by_id[field] for field in self.fields}
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

    @temporary_pages
    def read_stable(self, window):
        validate_read_window(window)

        def normalize(rows):
            if self.fields == F01_FIELDS:
                return normalize_f01_gmv(rows, window, self.heartbeat)
            return normalize_source("shop_refunds", rows, window, self.heartbeat)

        for _ in range(MAX_ATTEMPTS):
            rows, meta = self._scan(window)
            before = normalize(rows).fingerprint
            rows, after_meta = self._scan(window)
            after = normalize(rows)
            if before == after.fingerprint and meta == after_meta:
                if self.fields == F01_FIELDS:
                    after_meta["gmv_projection"] = F01_GMV_REVISION
                return replace(after, source_metadata=after_meta)
        raise SalesError("SOURCE_CHANGED_DURING_READ", 503)
