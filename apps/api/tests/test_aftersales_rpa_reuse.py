"""时间元数据重收不复制RPA事实；旧摘要须由原归档证明可复用。"""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.modules.aftersales import rpa_publisher as publisher
from app.modules.aftersales.rpa_ingestion import REVISION
from app.modules.streaming_json import fingerprint
from tests.test_aftersales_rpa import delivery

pytest_plugins = ["tests.sales_pg_support"]


def archive_root(tmp_path, compressed):
    config = tmp_path / "config"
    config.mkdir()
    (config / "archive.toml").write_text(
        f"payload_dedup_enabled = {str(compressed).lower()}\nmin_free_bytes = 0\n"
    )
    return tmp_path / "var/ingestion/rpa"


def legacy_digest(raw, rows, **_):
    # 冻结改动前的摘要协议；不使用新实现来推导旧版本的预期值。
    return fingerprint(dict(revision=REVISION, raw=list(raw), rows=rows))


def seed(engine, rows, root, monkeypatch, legacy):
    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(publisher, "input_digest", legacy_digest, raising=False)
        return publisher.sync_rpa(engine, lambda: deepcopy(rows), root)


def stored(engine):
    with engine.connect() as c:
        versions = (
            c.execute(
                sa.text("""SELECT id, dataset_code, content_fingerprint, retained_input_ref,
                interpretation_ref, row_schema_revision FROM dataset_versions
                WHERE dataset_code LIKE 'rpa_%' ORDER BY id""")
            )
            .mappings()
            .all()
        )
        facts = {
            table: c.execute(sa.text(f"SELECT * FROM {table} ORDER BY id")).mappings().all()
            for table in ("return_records", "product_sales_records")
        }
        reports = (
            c.execute(sa.text("SELECT * FROM aftersales_reports ORDER BY id")).mappings().all()
        )
        current = c.execute(
            sa.text("SELECT dataset_code, version_id FROM current_datasets ORDER BY dataset_code")
        ).all()
    return dict(versions=versions, facts=facts, reports=reports, current=current)


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def timestamp_only(rows):
    return [{**row, "updated_at": row["updated_at"] + timedelta(hours=1)} for row in rows]


@pytest.mark.parametrize("legacy", [False, True], ids=["semantic", "legacy"])
@pytest.mark.parametrize("compressed", [False, True], ids=["plain", "compressed"])
def test_updated_at_only_reuses_all_input_identity_and_archives(
    sales_pg, tmp_path, monkeypatch, legacy, compressed
):
    root = archive_root(tmp_path, compressed)
    rows = delivery()
    # 未映射字段包括嵌套updated_at；只有最外层时间允许排除。
    rows[0]["json_data"]["unmapped"] = {"updated_at": "business", "value": "0.1000"}
    first = seed(sales_pg, rows, root, monkeypatch, legacy)
    before, archived = stored(sales_pg), files(root.parent)
    again = publisher.sync_rpa(sales_pg, lambda: timestamp_only(rows), root)
    assert again["new_inputs"] == 0
    assert again["status"] == "reused"
    assert again["version_id"] == first["version_id"]
    assert stored(sales_pg) == before
    assert files(root.parent) == archived
    with sales_pg.connect() as c:
        assert c.execute(
            sa.text("SELECT status FROM task_runs ORDER BY started_at")
        ).scalars().all() == ["succeeded", "succeeded"]


@pytest.mark.parametrize("legacy", [False, True], ids=["semantic", "legacy"])
@pytest.mark.parametrize(
    "change", ["amount", "precision", "id", "unknown", "nested_time", "delete"]
)
def test_real_raw_changes_make_new_version_and_preserve_previous_report(
    sales_pg, tmp_path, monkeypatch, legacy, change
):
    from app.modules.aftersales.repository import snapshot

    rows = delivery()
    rows[1]["json_data"]["unmapped"] = {"updated_at": "before"}
    seed(sales_pg, rows, tmp_path, monkeypatch, legacy)
    before = stored(sales_pg)
    report_id = before["reports"][0]["id"]
    with snapshot(sales_pg, report_id) as repo:
        summary = repo.source_summary("j1_returns")
    changed = timestamp_only(deepcopy(rows))
    if change == "amount":
        changed[1]["json_data"]["实际入库金额"] = "20.1256"
    elif change == "precision":
        changed[1]["json_data"]["实际入库金额"] = "10.125600"
    elif change == "id":
        changed[1]["id"] = 999
    elif change == "unknown":
        changed[1]["json_data"]["new_field"] = "evidence"
    elif change == "nested_time":
        changed[1]["json_data"]["unmapped"]["updated_at"] = "after"
    else:
        del changed[1]
    result = publisher.sync_rpa(sales_pg, lambda: changed, tmp_path)
    assert result["new_inputs"] == 1
    after = stored(sales_pg)
    assert len(after["versions"]) == 4 and len(after["reports"]) == 2
    assert all(row in after["versions"] for row in before["versions"])
    with snapshot(sales_pg, report_id) as repo:
        assert repo.source_summary("j1_returns") == summary


@pytest.mark.parametrize("legacy", [False, True], ids=["semantic", "legacy"])
def test_producer_change_keeps_fact_identity_but_revises_report(
    sales_pg, tmp_path, monkeypatch, legacy
):
    monkeypatch.setattr(publisher, "producer", lambda: "a" * 64)
    seed(sales_pg, delivery(), tmp_path, monkeypatch, legacy)
    before = stored(sales_pg)
    monkeypatch.setattr(publisher, "producer", lambda: "b" * 64)
    result = publisher.sync_rpa(sales_pg, lambda: timestamp_only(delivery()), tmp_path)
    after = stored(sales_pg)
    assert result["new_inputs"] == 0
    assert after["versions"] == before["versions"] and after["facts"] == before["facts"]
    assert len(after["reports"]) == 2


@pytest.mark.parametrize("failure", ["missing", "checksum", "interpretation"])
def test_legacy_archive_failure_does_not_create_replacement(
    sales_pg, tmp_path, monkeypatch, failure
):
    seed(sales_pg, delivery(), tmp_path, monkeypatch, True)
    before = stored(sales_pg)
    version = next(v for v in before["versions"] if v["dataset_code"] == "rpa_returns")
    path = Path(
        version["interpretation_ref"]
        if failure == "interpretation"
        else version["retained_input_ref"]
    )
    if failure == "checksum":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        path.unlink()
    archived = files(tmp_path)
    with pytest.raises(ValueError, match="ARCHIVE_"):
        publisher.sync_rpa(sales_pg, lambda: timestamp_only(delivery()), tmp_path)
    assert stored(sales_pg) == before
    assert files(tmp_path) == archived
    with sales_pg.connect() as c:
        assert (
            c.scalar(sa.text("SELECT status FROM task_runs ORDER BY started_at DESC LIMIT 1"))
            == "failed"
        )


def test_timestamp_change_is_observed_and_accepted_even_when_input_reused(sales_pg, tmp_path):
    from app.jobs.source_monitor import run_monitor
    from app.modules.data_status.repository import read_status

    rows = delivery()
    received = []
    start = datetime.now(UTC)

    def observation():
        return dict(
            basis="rpa_table",
            revision=fingerprint(rows),
            count=len(rows),
            source_updated=rows[0]["updated_at"].isoformat(),
            date="2026-11-16",
            period_count=1,
        )

    def synchronize(groups, _):
        assert groups == ("aftersales_rpa",)
        received.append(publisher.sync_rpa(sales_pg, lambda: deepcopy(rows), tmp_path))
        return {"aftersales_rpa": True}

    def check(at):
        run_monitor(
            sales_pg,
            {"aftersales_rpa": observation},
            synchronize,
            now=at,
            receiving_sources=("aftersales_rpa",),
        )
        return next(
            row for row in read_status(sales_pg)["data"]["sources"] if row["id"] == "aftersales_rpa"
        )

    first = check(start)
    rows = timestamp_only(rows)
    again = check(start + timedelta(minutes=16))
    assert received[1]["new_inputs"] == 0
    assert (
        again["upstream"]["observation"]["source_updated"]
        != first["upstream"]["observation"]["source_updated"]
    )
    assert again["upstream"]["accepted_at"] > first["upstream"]["accepted_at"]
    assert again["platform_state"] == "available"


def test_legacy_reuse_reads_only_latest_same_period_candidate(sales_pg, tmp_path, monkeypatch):
    rows = delivery()
    seed(sales_pg, rows, tmp_path, monkeypatch, True)
    first = stored(sales_pg)
    rows[0]["json_data"]["unmapped"] = "latest evidence"
    seed(sales_pg, rows, tmp_path, monkeypatch, True)
    before = stored(sales_pg)
    old = next(v for v in first["versions"] if v["dataset_code"] == "rpa_returns")
    Path(old["retained_input_ref"]).unlink()
    result = publisher.sync_rpa(sales_pg, lambda: timestamp_only(rows), tmp_path)
    assert result["new_inputs"] == 0
    assert stored(sales_pg) == before


def test_legacy_same_window_weekly_and_monthly_keep_separate_input_identity(
    sales_pg, tmp_path, monkeypatch
):
    end = date(2026, 11, 30)
    rows = delivery(end) + [
        {**row, "time_granularity": "monthly"} for row in delivery(end, offset=100)
    ]
    first = seed(sales_pg, rows, tmp_path, monkeypatch, True)
    before, archived = stored(sales_pg), files(tmp_path)
    assert {report["report_type"] for report in before["reports"]} == {"weekly", "monthly"}
    assert len(before["versions"]) == 6
    again = publisher.sync_rpa(sales_pg, lambda: timestamp_only(rows), tmp_path)
    assert again["new_inputs"] == 0
    assert again["version_id"] == first["version_id"]
    assert stored(sales_pg) == before
    assert files(tmp_path) == archived


def test_rule_revision_is_part_of_input_identity(sales_pg, tmp_path, monkeypatch):
    seed(sales_pg, delivery(), tmp_path, monkeypatch, True)
    before = stored(sales_pg)
    monkeypatch.setattr(publisher, "REVISION", "m5-rpa-period-next-test")
    result = publisher.sync_rpa(sales_pg, lambda: timestamp_only(delivery()), tmp_path)
    after = stored(sales_pg)
    assert result["new_inputs"] == 3
    assert len(after["versions"]) == 6
    assert all(v in after["versions"] for v in before["versions"])


def test_legacy_digest_must_prove_current_normalized_rows(sales_pg, tmp_path, monkeypatch):
    from decimal import Decimal

    normalize = publisher.normalize

    def different_interpretation(rows):
        groups = normalize(rows)
        returns = next(g for g in groups if g.code == "rpa_returns")
        returns.rows[0]["actual_inbound_amount"] = Decimal("99")
        return groups

    with monkeypatch.context() as patch:
        patch.setattr(publisher, "normalize", different_interpretation)
        seed(sales_pg, delivery(), tmp_path, monkeypatch, True)
    before, archived = stored(sales_pg), files(tmp_path)
    with pytest.raises(ValueError, match="RPA_ARCHIVE_CONTENT_MISMATCH"):
        publisher.sync_rpa(sales_pg, lambda: timestamp_only(delivery()), tmp_path)
    assert stored(sales_pg) == before
    assert files(tmp_path) == archived


def test_legacy_stream_obeys_existing_outer_archive_budget(sales_pg, tmp_path, monkeypatch):
    from app.modules.archive_budget import maintenance_budget

    seed(sales_pg, delivery(), tmp_path, monkeypatch, True)
    before = stored(sales_pg)
    # 本测试显式启用外部预算；正常sync本身不启用字节限速，不能据此声称生产限速。
    # 真实校验预热已有摘要缓存后，逐行读取仍须计入这个已有预算。
    for version in before["versions"]:
        publisher.require_archive(version["retained_input_ref"])
        publisher.require_archive(version["interpretation_ref"])

    with maintenance_budget(10, bytes_per_second=1):
        with pytest.raises(ValueError, match="ARCHIVE_MAINTENANCE_BUDGET"):
            publisher.sync_rpa(sales_pg, lambda: timestamp_only(delivery()), tmp_path)
    assert stored(sales_pg) == before
