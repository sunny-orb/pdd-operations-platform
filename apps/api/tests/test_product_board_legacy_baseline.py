"""三代商品归档的真实PG兼容回归；旧生成代码只作校验材料，不执行。"""

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.product_board import storage
from app.modules.product_board.incremental import restore_baseline
from tests.test_product_board_incremental import Source, heads, receive

pytest_plugins = ["tests.sales_pg_support"]

LEGACY = ("storage.py", "source.py", "profile_rules.py")
SHARED = (*LEGACY, "fact_content.py")
CURRENT = (*SHARED, "incremental.py")


def digest(value):
    """独立写入历史JSON指纹格式，避免测试借用待验证的指纹函数。"""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def publish_layout(engine, root, monkeypatch, layout=LEGACY):
    """通过真实发布事务建立合成旧版；不回写已封存版本的来源或指纹。"""
    source = Source(date(2026, 11, 12))
    for code in ("product_daily", "product_promotion"):
        for row in source.raw[code]:
            row["spu_id"] = "000900719925474099312345"
    source.raw["profile_records"][0]["spigf"] = "000900719925474099312345"
    store = storage.ProductBoardStore(engine, root)
    # 文本若被执行必须失败；归档恢复只验证生成来源，绝不导入归档内的Python。
    producer = {name: "raise AssertionError('archived code must not execute')\n" for name in layout}
    with monkeypatch.context() as patch:
        patch.setattr(storage, "producer_sources", lambda: producer)
        patch.setattr(storage, "LOADED_PRODUCER_HASH", digest(producer))
        first = store.run(lambda hb: source.read_stable())
    with engine.connect() as connection:
        versions = {
            row["dataset_code"]: dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM dataset_versions WHERE id=ANY(:ids)"),
                {"ids": list(first["versions"].values())},
            ).mappings()
        }
    return store, source, first, versions


def reseal_fixture(archive, name, content):
    """只篡改tmp_path合成材料，模拟文件校验通过但业务指纹或生成来源不相容。"""
    path = archive / name
    path.write_text(content)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(hashlib.sha256(content.encode()).hexdigest())
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for member in (path, sidecar):
        manifest["files"][member.name] = digest(member.read_text())
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.with_suffix(".json.sha256").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("layout", [LEGACY, SHARED, CURRENT])
@pytest.mark.parametrize("full", [False, True])
def test_known_layout_restores_and_preserves_window_and_full_semantics(
    sales_pg, tmp_path, monkeypatch, layout, full
):
    store, source, first, versions = publish_layout(sales_pg, tmp_path, monkeypatch, layout)
    archive = Path(versions["product_daily"]["retained_input_ref"])
    sealed = {p.name: p.read_bytes() for p in archive.iterdir()}
    for code in ("product_daily", "product_promotion", "profile_records"):
        end, rows = restore_baseline(store, code, versions[code])
        assert end == date(2026, 11, 12)
        assert isinstance(rows[0]["source_row_key"], str)
        if code == "profile_records":
            assert rows[0]["source_listing_ids_official_raw"] == "000900719925474099312345"
        else:
            assert rows[0]["source_product_id"] == "000900719925474099312345"
            assert isinstance(
                rows[0]["business_date" if code == "product_daily" else "period_start"], date
            )
            assert rows[0]["product_revenue" if code == "product_daily" else "spend"] == Decimal(
                "12.34" if code == "product_daily" else "1.23"
            )
    source.end = date(2026, 11, 13)
    next(row for row in source.raw["product_daily"] if row["dt"] == "2026-09-01")[
        "transaction_amount"
    ] = "45.67"
    second = receive(store, source, full_sources=("product_daily",) if full else ())
    with sales_pg.connect() as connection:
        amount = sa.text(
            "SELECT product_revenue FROM read_product_daily_facts "
            "WHERE dataset_version_id=:version AND business_date='2026-09-01'"
        )
        assert connection.scalar(
            amount, {"version": second["versions"]["product_daily"]}
        ) == Decimal("45.67" if full else "12.34")
        assert connection.scalar(
            amount, {"version": first["versions"]["product_daily"]}
        ) == Decimal("12.34")
    assert {p.name: p.read_bytes() for p in archive.iterdir()} == sealed


@pytest.mark.parametrize("mutation", ["payload", "producer", "revision", "schema"])
def test_legacy_tampering_is_rejected_even_after_file_checksums_pass(
    sales_pg, tmp_path, monkeypatch, mutation
):
    store, source, first, versions = publish_layout(sales_pg, tmp_path, monkeypatch)
    version = versions["product_daily"]
    archive = Path(version["retained_input_ref"])
    if mutation in ("payload", "schema"):
        rows = json.loads((archive / "product_daily.json").read_text())
        rows[0]["product_revenue" if mutation == "payload" else "unknown_column"] = (
            "999.00" if mutation == "payload" else None
        )
        reseal_fixture(archive, "product_daily.json", json.dumps(rows))
        if mutation == "schema":
            version["content_fingerprint"] = digest(
                dict(
                    rows=rows,
                    revision="m1-board-v2-1",
                    producer=version["producer_revision"],
                    end="2026-11-12",
                    inputs=None,
                )
            )
    elif mutation == "producer":
        reseal_fixture(archive, "source.py", "# altered producer\n")
    else:
        manifest = json.loads((archive / "manifest.json").read_text())
        manifest["revision"] = "m1-board-unknown"
        path = archive / "manifest.json"
        path.write_text(json.dumps(manifest))
        path.with_suffix(".json.sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="INCREMENTAL_BASELINE_MISMATCH"):
        restore_baseline(store, "product_daily", version)
    with pytest.raises(ValueError, match="INCREMENTAL_BASELINE_MISMATCH"):
        receive(store, source, full_sources=("product_daily",))
    assert heads(sales_pg) == first["versions"]


@pytest.mark.parametrize(
    "layout", [(*CURRENT, "unknown.py"), (*LEGACY, "incremental.py"), LEGACY[:2]]
)
def test_unknown_producer_layout_rejected_despite_consistent_fingerprints(
    sales_pg, tmp_path, monkeypatch, layout
):
    store, _, _, versions = publish_layout(sales_pg, tmp_path, monkeypatch, layout)
    with pytest.raises(ValueError, match="INCREMENTAL_BASELINE_MISMATCH"):
        restore_baseline(store, "product_daily", versions["product_daily"])
