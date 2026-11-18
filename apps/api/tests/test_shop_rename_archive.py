"""合成改名前协议归档→当前增量发布；真实PG，未知版本及被改证据不放行。"""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.sales import storage
from app.modules.sales.archive_replay import restore_candidate
from app.modules.sales.contracts import SalesError
from app.modules.sales.datasets import EXTENDED_REVISION, READ_SPECS, REFUND_SHOPS
from app.modules.sales.normalization import canonical, fingerprint
from app.modules.sales.source_policy import METRICS, POLICY_REVISION, SHOPS
from tests.test_sales_extended_sources import sample
from tests.test_sales_window_incremental import normalize, receive, row, scope, store_for

pytest_plugins = ["tests.sales_pg_support"]
OLD = "kocotree童装童鞋旗舰店"
NEW = "kocotree青少年服饰旗舰店"
CODES = (
    "shop_daily",
    "shop_ratings",
    "shop_channel_product_promotion",
    "shop_channel_star_shop",
    "shop_regions",
    "product_regions",
)


def legacy_sources():
    """只逆转本次名称差异生成旧源码证据；文本只做摘要，绝不执行。"""
    sources = storage.producer_sources()
    sources["modules/sales/normalization.py"] = (
        Path(__file__).parent / "fixtures/sales_normalization_20261116.py.txt"
    ).read_text()
    sources.pop("modules/sales/normalization_compat.py", None)
    for name in ("normalization.py", "source_normalization.py"):
        key = "modules/sales/" + name
        sources[key] = sources[key].replace("SHOP_NAME_ALIASES, ", "")
    key = "modules/sales/normalization.py"
    sources[key] = sources[key].replace(
        "name not in SHOP_NAME_ALIASES[source_shop]", "name != SHOPS[source_shop][1]"
    )
    key = "modules/sales/source_normalization.py"
    sources[key] = sources[key].replace(
        "if (\n                source_shop not in spec.shop_ids\n"
        '                or item.get("shop_name") not in SHOP_NAME_ALIASES[source_shop]\n'
        "            ):",
        'if source_shop not in spec.shop_ids or item.get("shop_name") != SHOPS[source_shop][1]:',
    )
    key = "modules/sales/source_policy.py"
    source = sources[key].replace(NEW, OLD).replace("青少年服饰店", "童装童鞋店")
    sources[key] = (
        source[: source.index("# 2026-11-16用户确认")] + source[source.index("METRICS =") :]
    )
    key = "modules/sales/datasets.py"
    sources[key] = sources[key].replace(
        "# 别名数量不等于店铺数量；旧事实与新接收记录都归到同一内部ID。\n"
        'REFUND_SHOPS["kk树-kocotree青少年服饰旗舰店-配饰"] = "spitztx"\n\n',
        "",
    )
    key = "modules/sales/promotion_history.py"
    sources[key] = (
        sources[key]
        .replace("SHOPS, display_shop_name", "SHOPS")
        .replace(
            'display_shop_name(row["shop_id"], row["source_shop_name"])', 'row["source_shop_name"]'
        )
    )
    return sources


def legacy_candidate(code, candidate, sources):
    old_shops = {**SHOPS, "271470254": ("spitztx", OLD, "童装童鞋店")}
    old_refunds = {name: shop for name, shop in REFUND_SHOPS.items() if NEW not in name}
    module = "normalization.py" if code == "shop_daily" else "source_normalization.py"
    digest = dict(
        policy=POLICY_REVISION if code == "shop_daily" else EXTENDED_REVISION,
        mapping=old_shops,
        normalizer_revision=hashlib.sha256(sources["modules/sales/" + module].encode()).hexdigest(),
        start=str(candidate.window.start),
        end=str(candidate.window.end),
        rows=candidate.raw_rows,
    )
    if code == "shop_daily":
        digest["metrics"] = METRICS
    else:
        digest.update(source=asdict(READ_SPECS[code]), refund_shops=old_refunds)
    return replace(candidate, fingerprint=fingerprint(digest)), old_shops


def original_history_sources(sources):
    """10/30固定历史归档：尚无推广扩窗和地区40万预算，指标解释未变。"""
    sources = dict(sources)
    key = "modules/sales/source_policy.py"
    sources[key] = (
        sources[key]
        .replace("PROMOTION_START = date(2025, 2, 1)\n", "")
        .replace(
            'PROMOTION_CODES = ("shop_channel_product_promotion", "shop_channel_star_shop")\n', ""
        )
        .replace("def approved_window(code=None):", "def approved_window():")
        .replace(
            "from .contracts import PromotionReadWindow, ReadWindow",
            "from .contracts import ReadWindow",
        )
        .replace(
            "    if code in PROMOTION_CODES:\n"
            "        return PromotionReadWindow("
            "PROMOTION_START, calendar_through(), tuple(SHOPS))\n",
            "",
        )
        .replace(
            "def validate_read_window(window, code=None):", "def validate_read_window(window):"
        )
        .replace("approved = approved_window(code)", "approved = approved_window()")
    )
    key = "modules/sales/datasets.py"
    sources[key] = sources[key].replace("PRODUCT_REGION_SOURCE_MAX_ROWS = 400_000\n", "")
    return sources


def publish_legacy(pg, root, code, monkeypatch):
    # 明星店渠道范围没有该店；仍须验证其旧归档能随共享协议升级。
    identity = {} if code == "shop_channel_star_shop" else dict(shop_id="271470254", shop_name=OLD)
    original = [row(code, "old", "2026-11-11", **identity)]
    sources = legacy_sources()
    candidate, shops = legacy_candidate(
        code, normalize(code, original, scope(code, "2026-11-11")), sources
    )
    with monkeypatch.context() as m:
        m.setattr(storage, "producer_sources", lambda: sources)
        m.setattr(storage, "LOADED_PRODUCER_SOURCES", sources)
        m.setattr(storage, "SHOPS", shops)
        result = store_for(pg, root, code, "2026-11-11").publish_candidate(candidate)
    return result, candidate, identity


@pytest.mark.parametrize("code", CODES)
def test_old_baseline_upgrades_with_only_one_day_read_and_no_historical_changes(
    sales_pg, tmp_path, monkeypatch, code
):
    first, baseline, identity = publish_legacy(sales_pg, tmp_path, code, monkeypatch)
    folder = tmp_path / first.version_id
    archived = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    assert restore_candidate(folder).rows == baseline.rows
    if identity:
        identity["shop_name"] = NEW
    delta = [row(code, "new", "2026-11-12", "3", **identity)]
    windows = []
    upgraded = receive(sales_pg, tmp_path, code, delta, windows=windows)
    assert windows[0].start == windows[0].end == date(2026, 11, 12)
    rebuilt = restore_candidate(tmp_path / upgraded.version_id)
    assert next(r for r in rebuilt.rows if r["source_row_key"] == "old") == baseline.rows[0]
    if identity:
        assert (
            next(r for r in rebuilt.rows if r["source_row_key"] == "new")["source_shop_name"] == NEW
        )
    assert receive(sales_pg, tmp_path, code, delta).version_id == upgraded.version_id
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == archived


@pytest.mark.parametrize(
    "tamper", ["producer", "policy", "mapping", "input", "alias", "pg_producer"]
)
def test_legacy_evidence_changes_reject_and_keep_current(sales_pg, tmp_path, monkeypatch, tamper):
    first, _, _ = publish_legacy(sales_pg, tmp_path, "shop_daily", monkeypatch)
    folder = tmp_path / first.version_id
    manifest = json.loads((folder / "interpretation.json").read_text())
    if tamper == "producer":
        sources = json.loads((folder / "producer-source.json").read_text())
        sources["modules/sales/normalization.py"] += "\n"
        (folder / "producer-source.json").write_text(canonical(sources))
        manifest["producer_revision"] = "sha256:" + fingerprint(sources)
    elif tamper == "policy":
        manifest["policy"] += "-unknown"
    elif tamper == "mapping":
        manifest["shops"]["271470254"][1] = NEW
    elif tamper in ("input", "alias"):
        file = folder / "input.ndjson"
        payload = (
            file.read_text().replace(OLD, NEW) if tamper == "alias" else file.read_text() + " "
        )
        file.write_text(payload)
        if tamper == "alias":
            manifest["input_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    else:
        sources = json.loads((folder / "producer-source.json").read_text())
        sources["unrelated.txt"] = "different producer"
        (folder / "producer-source.json").write_text(canonical(sources))
        manifest["producer_revision"] = "sha256:" + fingerprint(sources)
    (folder / "interpretation.json").write_text(canonical(manifest))
    # 同时重封文件摘要，验证内部语义/PG锚点，而不只停在外层checksum。
    for name in ("input.ndjson", "producer-source.json", "interpretation.json"):
        (folder / (name + ".sha256")).write_text(
            hashlib.sha256((folder / name).read_bytes()).hexdigest()
        )
    if tamper == "pg_producer":
        assert restore_candidate(folder).fingerprint == manifest["fingerprint"]
    else:
        with pytest.raises(SalesError, match="ARCHIVE_"):
            restore_candidate(folder)
    with pytest.raises(SalesError, match="ARCHIVE_"):
        receive(sales_pg, tmp_path, "shop_daily", [row("shop_daily", "new", "2026-11-12")])
    with sales_pg.connect() as c:
        assert (
            c.scalar(
                sa.text("SELECT version_id FROM current_datasets WHERE dataset_code='shop_daily'")
            )
            == first.version_id
        )


def test_legacy_refunds_restore_original_name_and_amount(sales_pg, tmp_path, monkeypatch):
    from app.modules.sales.source_normalization import normalize_source

    code = "shop_refunds"
    sources = legacy_sources()
    candidate, shops = legacy_candidate(
        code, normalize_source(code, [sample(code, 店铺=f"kk树-{OLD}-配饰")], scope(code)), sources
    )
    with monkeypatch.context() as m:
        m.setattr(storage, "producer_sources", lambda: sources)
        m.setattr(storage, "LOADED_PRODUCER_SOURCES", sources)
        m.setattr(storage, "SHOPS", shops)
        result = store_for(sales_pg, tmp_path, code).publish_candidate(candidate)
    assert restore_candidate(tmp_path / result.version_id) == candidate


@pytest.mark.parametrize("original_config", [False, True])
def test_legacy_history_restores_original_fact_names(
    sales_pg, tmp_path, monkeypatch, original_config
):
    from app.modules.sales.promotion_history import APPROVED, HISTORY_WINDOW, normalize_history

    inputs = [
        dict(
            shop_id=sid,
            date=day,
            source_table=table,
            record_id=f"rec{i}" if table else None,
            spend="0",
            revenue="0",
            impressions="0",
            clicks="0",
        )
        for i, (sid, day, table) in enumerate(APPROVED)
    ]
    candidate = normalize_history(inputs)
    candidate = replace(
        candidate,
        rows=tuple(
            {**r, "source_shop_name": OLD if r["shop_id"] == "spitztx" else r["source_shop_name"]}
            for r in candidate.rows
        ),
    )
    sources = legacy_sources()
    if original_config:
        sources = original_history_sources(sources)
    with monkeypatch.context() as m:
        m.setattr(storage, "producer_sources", lambda: sources)
        m.setattr(storage, "LOADED_PRODUCER_SOURCES", sources)
        m.setattr(storage, "SHOPS", {**SHOPS, "271470254": ("spitztx", OLD, "童装童鞋店")})
        store = storage.ShopDailyStore(
            sales_pg,
            archive_root=tmp_path,
            approved=HISTORY_WINDOW,
            spec=READ_SPECS["shop_promotion_history"],
        )
        result = store.publish_candidate(candidate)
    assert restore_candidate(tmp_path / result.version_id) == candidate


@pytest.mark.parametrize(
    "case", ["only_policy", "only_datasets", "other_dataset", "future_interpreter"]
)
def test_original_history_configuration_cannot_be_mixed_or_widened(monkeypatch, case):
    from pathlib import Path

    from app.modules.sales.rename_archive import validate_legacy_interpretation

    baseline = legacy_sources()
    sources = original_history_sources(baseline)
    if case == "only_policy":
        sources["modules/sales/datasets.py"] = baseline["modules/sales/datasets.py"]
    elif case == "only_datasets":
        sources["modules/sales/source_policy.py"] = baseline["modules/sales/source_policy.py"]
    elif case == "future_interpreter":
        read = Path.read_bytes
        monkeypatch.setattr(
            Path, "read_bytes", lambda p: read(p) + (b"\n" if p.name == "normalization.py" else b"")
        )
    code = "shop_ratings" if case == "other_dataset" else "shop_promotion_history"
    manifest = dict(
        policy=EXTENDED_REVISION,
        dataset=asdict(READ_SPECS[code]),
        shops={**SHOPS, "271470254": ("spitztx", OLD, "童装童鞋店")},
    )
    with pytest.raises(SalesError, match="ARCHIVE_INTERPRETER_MISMATCH"):
        validate_legacy_interpretation(manifest, sources)
