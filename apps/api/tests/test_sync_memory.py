"""同步内存上界回归：真实分块与归档必须保留逐字节身份和可重放输入。"""

import hashlib
import json
import tracemalloc
from datetime import date
from decimal import Decimal

from app.modules.fact_content import _blocks

pytest_plugins = ["tests.sales_pg_support"]


def test_fact_block_preparation_does_not_keep_all_numeric_tokens():
    """重新保存所有桶的数值字符串投影会超过4MiB；分块身份不能随优化改变。"""
    rows = [
        dict(
            source_row_key=f"r{i:06}",
            row_kind="detail",
            shop_match_status="not_provided",
            date_status="verified_period",
            period_start=date(2026, 10, 26),
            period_end=date(2026, 10, 26),
            sales_amount=Decimal("100.01000000000001"),
            source_sales_qty=Decimal("1.50"),
            source_style_code="测试款0007",
        )
        for i in range(12000)
    ]
    tracemalloc.start()
    try:
        result = _blocks(
            "product_sales_records",
            {"dataset_code": "jst_shipped_sales", "row_schema_revision": "test"},
            rows,
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    identity = hashlib.sha256(
        json.dumps([(key, bucket, len(values)) for key, bucket, values in result]).encode()
    ).hexdigest()
    assert identity == "3e82907bd776c6104e28860ffcc969056706af5be88f13dd18b7ac8fc78f8a3f"
    assert peak < 4 * 1024**2
    assert sum(len(values) for _, _, values in result) == 12000


def test_sales_archive_streams_and_retains_replayable_bytes(sales_pg, tmp_path):
    """归档整份拼接或读回整份字节会超过8MiB；摘要和完整重放仍须通过。"""
    from app.modules.archive_payload import open_payload
    from app.modules.sales.archive_replay import restore_candidate
    from app.modules.sales.datasets import SPECS
    from app.modules.sales.normalization import canonical
    from app.modules.sales.source_normalization import normalize_source
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_product_regions_incremental import scope
    from tests.test_sales_extended_sources import sample

    rows = [
        sample("product_regions", id=f"large-{i}", product_name="商品" * 512) for i in range(4000)
    ]
    candidate = normalize_source("product_regions", rows, scope())
    expected = hashlib.sha256()
    for row in candidate.raw_rows:
        expected.update((canonical(row) + "\n").encode())
    store = ShopDailyStore(
        sales_pg,
        archive_root=tmp_path / "ingestion" / "product_regions",
        approved=scope(),
        spec=SPECS["product_regions"],
    )
    tracemalloc.start()
    try:
        archive, interpretation = store._archive("memory-regression", candidate, lambda **_: None)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024**2
    from pathlib import Path

    metadata = json.loads(Path(interpretation).read_text())
    assert metadata["input_sha256"] == expected.hexdigest()
    actual = hashlib.sha256()
    with open_payload(archive) as stream:
        while chunk := stream.read(1024 * 1024):
            actual.update(chunk)
    assert actual.hexdigest() == expected.hexdigest()
    restored = restore_candidate(Path(archive).parent)
    assert restored.fingerprint == candidate.fingerprint
    assert restored.rows == candidate.rows


def test_sales_fingerprint_keeps_exact_bytes_without_whole_json_allocation():
    """恢复整份canonical().encode()会超过2MiB；Unicode、Decimal、空值字节不能变。"""
    from datetime import datetime

    from app.modules.sales.normalization import canonical, fingerprint

    value = {
        "rows": [{"商品": '测试\n"' * 512, "amount": Decimal("-0.000"), "null": None}] * 4000,
        "date": datetime(2026, 11, 18, 12, 34, 56),
        "zero": 0,
    }
    expected = hashlib.sha256(canonical(value).encode()).hexdigest()
    tracemalloc.start()
    try:
        actual = fingerprint(value)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected
    assert peak < 2 * 1024**2


def test_empty_archive_preserves_legacy_single_newline(sales_pg, tmp_path):
    from dataclasses import replace
    from pathlib import Path

    from app.modules.sales.normalization import normalize_shop_daily
    from app.modules.sales.storage import ShopDailyStore
    from tests.test_sales_source import raw_row, window

    candidate = replace(normalize_shop_daily([raw_row()], window()), rows=(), raw_rows=())
    store = ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window())
    archive, interpretation = store._archive("empty", candidate, lambda **_: None)
    assert Path(archive).read_bytes() == b"\n"
    assert (
        json.loads(Path(interpretation).read_text())["input_sha256"]
        == hashlib.sha256(b"\n").hexdigest()
    )


def test_previous_normalizer_archive_replays_and_reuses_version(sales_pg, tmp_path, monkeypatch):
    """只改内存算法不能把旧基线判未知、换内容指纹或制造另一份发布输入。"""
    from pathlib import Path

    import sqlalchemy as sa

    from app.modules.sales import storage
    from app.modules.sales.archive_replay import restore_candidate
    from app.modules.sales.normalization import normalize_shop_daily
    from tests.test_sales_source import raw_row, window

    candidate = normalize_shop_daily([raw_row()], window())
    assert (
        candidate.fingerprint == "bd101e711f735579fbde0101bc200395dbb12dded9672b84996e845aff0673dd"
    )
    sources = storage.producer_sources()
    sources["modules/sales/normalization.py"] = (
        Path(__file__).parent / "fixtures/sales_normalization_20261116.py.txt"
    ).read_text()
    sources.pop("modules/sales/normalization_compat.py", None)
    store = storage.ShopDailyStore(sales_pg, archive_root=tmp_path, approved=window())
    with monkeypatch.context() as m:
        m.setattr(storage, "producer_sources", lambda: sources)
        m.setattr(storage, "LOADED_PRODUCER_SOURCES", sources)
        first = store.publish_candidate(candidate)
    assert restore_candidate(tmp_path / first.version_id) == candidate
    existing = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    second = store.publish_candidate(candidate)
    assert second.reused and second.version_id == first.version_id
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == existing
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM dataset_versions")) == 1
