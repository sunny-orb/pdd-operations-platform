"""RPA累计历史可增长；读取、规范和旧归档仍限制单个期间/类别。"""

from copy import deepcopy
from datetime import date

import psycopg
import pytest
import sqlalchemy as sa
from psycopg.types.json import Jsonb

from app.connectors.aftersales_rpa import read_snapshot, receive_stable
from app.modules.aftersales import rpa_ingestion, rpa_publisher
from tests.test_aftersales_rpa import delivery
from tests.test_aftersales_rpa_reuse import seed, stored, timestamp_only

pytest_plugins = ["tests.sales_pg_support"]


def source_table(engine, rows):
    """只在随机隔离测试库建立合成上游，复用真实PG读取协议。"""
    url = engine.url

    def connect():
        return psycopg.connect(
            host=url.host,
            port=url.port,
            dbname=url.database,
            user=url.username,
            password=url.password,
        )

    with connect() as c:
        c.execute("""CREATE TABLE aftersales_reports_rpa (
            id bigint PRIMARY KEY,start_date date,end_date date,time_granularity text,
            class text,json_data jsonb,updated_at timestamptz)""")
        with c.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO aftersales_reports_rpa VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        row["id"],
                        row["start_date"],
                        row["end_date"],
                        row["time_granularity"],
                        row["class"],
                        Jsonb(row["json_data"]),
                        row["updated_at"],
                    )
                    for row in rows
                ],
            )
    return connect


def test_rpa_reads_and_normalizes_more_than_old_whole_history_limit(sales_pg):
    # 每类100001行，总计300003；缺SKU原行仍按原规则保留，不造业务身份。
    rows = [delivery()[i] for i in (0, 2, 3)]
    for row in rows:
        row["json_data"]["商品编码"] = None
    connect = source_table(sales_pg, [])
    with connect() as c:
        for n, row in enumerate(rows):
            c.execute(
                """INSERT INTO aftersales_reports_rpa
                SELECT i*3+%s, %s, %s, %s, %s, %s, %s
                FROM generate_series(0,100000) AS i""",
                (
                    n + 1,
                    row["start_date"],
                    row["end_date"],
                    row["time_granularity"],
                    row["class"],
                    Jsonb(row["json_data"]),
                    row["updated_at"],
                ),
            )
    received = receive_stable(connect)
    assert len(received) == 300003
    assert received[0]["id"] == 1 and received[-1]["id"] == 300003
    assert [len(g.rows) for g in rpa_ingestion.normalize(received)] == [100001] * 3


def test_rpa_single_group_over_limit_rejected_before_reading_history(sales_pg, monkeypatch):
    monkeypatch.setattr(rpa_ingestion, "MAX_GROUP_ROWS", 2, raising=False)
    rows = delivery()
    rows.append({**deepcopy(rows[0]), "id": 999})
    connect = source_table(sales_pg, rows)
    with pytest.raises(ValueError, match="RPA_GROUP_ROW_LIMIT"):
        read_snapshot(connect)


def test_rpa_normalize_rejects_single_oversized_group(monkeypatch):
    monkeypatch.setattr(rpa_ingestion, "MAX_GROUP_ROWS", 2, raising=False)
    rows = delivery()
    rows.append({**deepcopy(rows[0]), "id": 999})
    rows[-1]["json_data"]["退货原因"] = "其他"
    with pytest.raises(ValueError, match="RPA_GROUP_ROW_LIMIT"):
        rpa_ingestion.normalize(rows)


@pytest.mark.parametrize("legacy", [False, True], ids=["semantic", "legacy"])
def test_rpa_history_growth_reuses_inputs_and_protects_old_reports(
    sales_pg, tmp_path, monkeypatch, legacy
):
    monkeypatch.setattr(rpa_ingestion, "MAX_GROUP_ROWS", 2, raising=False)
    previous = delivery(date(2026, 11, 9))
    first = seed(sales_pg, previous, tmp_path, monkeypatch, legacy)
    before = stored(sales_pg)
    rows = previous + delivery(offset=100)
    connect = source_table(sales_pg, rows)
    result = rpa_publisher.sync_rpa(sales_pg, lambda: receive_stable(connect), tmp_path)
    after = stored(sales_pg)
    assert result["new_inputs"] == 3 and result["rows"] == 12
    assert len(after["reports"]) == 2 and len(after["versions"]) == 6
    assert all(v in after["versions"] for v in before["versions"])
    assert all(r in after["reports"] for r in before["reports"])
    assert first["version_id"] != result["version_id"]
    again = rpa_publisher.sync_rpa(sales_pg, lambda: timestamp_only(rows), tmp_path)
    assert again["new_inputs"] == 0 and again["version_id"] == result["version_id"]
    assert stored(sales_pg) == after
    broken = deepcopy(rows)
    broken.append({**deepcopy(broken[-6]), "id": 999})
    broken[-1]["json_data"]["退货原因"] = "其他"
    with pytest.raises(ValueError, match="RPA_GROUP_ROW_LIMIT"):
        rpa_publisher.sync_rpa(sales_pg, lambda: broken, tmp_path)
    assert stored(sales_pg) == after
    with sales_pg.connect() as c:
        assert (
            c.scalar(sa.text("SELECT status FROM task_runs ORDER BY started_at DESC LIMIT 1"))
            == "failed"
        )


def test_rpa_legacy_archive_keeps_same_per_group_row_budget(sales_pg, tmp_path, monkeypatch):
    rows = delivery()
    rows.append({**deepcopy(rows[0]), "id": 999})
    rows[-1]["json_data"]["退货原因"] = "其他"
    seed(sales_pg, rows, tmp_path, monkeypatch, True)
    group = next(g for g in rpa_ingestion.normalize(rows) if g.code == "rpa_returns")
    previous = next(v for v in stored(sales_pg)["versions"] if v["dataset_code"] == group.code)
    monkeypatch.setattr(rpa_ingestion, "MAX_GROUP_ROWS", 2, raising=False)
    with pytest.raises(ValueError, match="RPA_ARCHIVE_ROW_LIMIT"):
        rpa_publisher.legacy_input_matches(previous, group)


def test_rpa_separate_complete_snapshots_detect_cross_page_change(sales_pg):
    rows = delivery()
    # 1500行确保第二页也参与全表指纹，不把第一页稳定当成整表稳定。
    rows = [{**rows[i % 6], "id": i + 1} for i in range(1500)]
    connect = source_table(sales_pg, rows)
    calls = 0

    def changing_connect():
        nonlocal calls
        calls += 1
        if calls == 2:
            with connect() as c:
                c.execute("DELETE FROM aftersales_reports_rpa WHERE id=1500")
        return connect()

    with pytest.raises(ValueError, match="RPA_SOURCE_CHANGED"):
        receive_stable(changing_connect)
