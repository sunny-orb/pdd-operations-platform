"""真实PG验证售后同内容接收复用归档，缺档失败不能切换当前版。"""

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.aftersales.ingestion import normalize
from app.modules.aftersales.publisher import FACTS, publish_source
from app.modules.aftersales.targets import publish_targets
from tests.test_aftersales_ingestion import sample
from tests.test_aftersales_targets import config

pytest_plugins = ["tests.sales_pg_support"]
KINDS = [*FACTS, "refund_targets"]


def publish(engine, root, kind, *, changed=False):
    """合成来源或目标通过真实发布入口；修订保留相同业务身份。"""
    if kind == "refund_targets":
        value = config()
        if changed:
            value["total_refund_rate"]["upper"] = "0.44"
        return publish_targets(engine, value, root)
    end, raw, rows = sample(date(2026, 10, 27))(kind)
    if changed:
        raw[0]["aftersale_num"] = "3"
        raw[0]["sale_num"] = "3"
        rows = normalize(kind, raw, end)
    return publish_source(engine, kind, lambda hb: (end, raw, rows), root)


def archives(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def version(engine, vid):
    with engine.connect() as c:
        return (
            c.execute(sa.text("SELECT * FROM dataset_versions WHERE id=:v"), {"v": vid})
            .mappings()
            .one()
        )


@pytest.mark.parametrize("kind", KINDS)
def test_unchanged_reuses_archive_and_records_success(sales_pg, tmp_path, kind):
    first = publish(sales_pg, tmp_path, kind)
    before = archives(tmp_path)
    folders = set(tmp_path.iterdir())
    second = publish(sales_pg, tmp_path, kind)
    assert second["version_id"] == first["version_id"]
    assert archives(tmp_path) == before
    assert set(tmp_path.iterdir()) == folders
    original = version(sales_pg, first["version_id"])
    with sales_pg.connect() as c:
        tasks = c.execute(sa.text("SELECT * FROM task_runs ORDER BY queued_at")).mappings().all()
        assert len(tasks) == 2
        assert all(t["status"] == "succeeded" for t in tasks)
        assert all(t["result_version_id"] == first["version_id"] for t in tasks)
        assert all(t["evidence_ref"] == original["interpretation_ref"] for t in tasks)
        assert c.scalar(sa.text("SELECT count(*) FROM dataset_versions")) == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field", ["retained_input_ref", "interpretation_ref"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_unusable_prior_archive_rejects_reuse_without_new_files(
    sales_pg, tmp_path, kind, field, damage
):
    first = publish(sales_pg, tmp_path, kind)
    changed = publish(sales_pg, tmp_path, kind, changed=True)
    assert changed["version_id"] != first["version_id"]
    old = version(sales_pg, first["version_id"])
    path = Path(old[field])
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("corrupted synthetic archive")
    before = archives(tmp_path)
    folders = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="ARCHIVE_(RESTORE_REQUIRED|CHECKSUM)"):
        publish(sales_pg, tmp_path, kind)
    assert archives(tmp_path) == before
    assert set(tmp_path.iterdir()) == folders
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT version_id FROM current_datasets")) == changed["version_id"]
        assert c.scalar(sa.text("SELECT count(*) FROM dataset_versions")) == 2
        assert (
            c.scalar(sa.text("SELECT status FROM task_runs ORDER BY queued_at DESC LIMIT 1"))
            == "failed"
        )


@pytest.mark.parametrize("kind", KINDS)
def test_changed_content_keeps_old_archive_and_publishes_revision(sales_pg, tmp_path, kind):
    first = publish(sales_pg, tmp_path, kind)
    before = archives(tmp_path)
    changed = publish(sales_pg, tmp_path, kind, changed=True)
    after = archives(tmp_path)
    assert changed["version_id"] != first["version_id"]
    assert all(after[name] == content for name, content in before.items())
    assert len(after) > len(before)
    assert version(sales_pg, first["version_id"])["state"] == "published"
    assert version(sales_pg, changed["version_id"])["state"] == "published"


def test_invalid_target_does_not_accept_old_result(sales_pg, tmp_path):
    first = publish(sales_pg, tmp_path, "refund_targets")
    before = archives(tmp_path)
    value = config()
    value["total_refund_rate"]["lower"] = "0.99"
    with pytest.raises(ValueError):
        publish_targets(sales_pg, value, tmp_path)
    assert archives(tmp_path) == before
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT version_id FROM current_datasets")) == first["version_id"]
        assert (
            c.scalar(sa.text("SELECT status FROM task_runs ORDER BY queued_at DESC LIMIT 1"))
            == "failed"
        )
