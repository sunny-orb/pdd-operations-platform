"""RPA交付→真实PG→看板：合计不重算、缺值授权、原子发布及历史固定。"""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

pytest_plugins = ["tests.sales_pg_support"]


def delivery(end=date(2026, 11, 16), offset=0):
    common = {
        "商品编码": "001",
        "款式编码": "K01",
        "产品分类": "童帽",
        "虚拟分类": "帽子",
        "颜色规格": "蓝色",
    }
    items = [
        (
            "按退货原因",
            dict(
                common, 商品名称="帽子全称", 退货原因="尺码不合适", 实退数量="2", 实际入库金额="nan"
            ),
        ),
        (
            "按退货原因",
            dict(
                common,
                商品名称="帽子全称",
                退货原因="质量问题",
                实退数量="1",
                实际入库金额="10.1256",
            ),
        ),
        ("销售已发货", dict(common, 商品简称="帽子简称", 实发数量="10", 实发金额="100")),
        ("销售已取消", dict(common, 商品简称="帽子简称", 销售数量="2", 销售金额="25")),
    ]
    for category, qty, amount, q, a in [
        ("销售已发货", "实发数量", "实发金额", "10", "100"),
        ("销售已取消", "销售数量", "销售金额", "2", "25"),
    ]:
        items.append((category, {**dict.fromkeys(common, ""), "商品简称": "", qty: q, amount: a}))
    return [
        dict(
            id=offset + i + 1,
            start_date=end - timedelta(days=29),
            end_date=end,
            time_granularity="weekly",
            **{"class": cl},
            json_data=j,
            updated_at=datetime(2026, 11, 16, tzinfo=UTC),
        )
        for i, (cl, j) in enumerate(items)
    ]


def test_rpa_numbers_totals_and_original_period():
    from app.modules.aftersales.rpa_ingestion import normalize

    groups = normalize(delivery())
    returns = next(g for g in groups if g.code == "rpa_returns")
    shipped = next(g for g in groups if g.code == "rpa_shipped_sales")
    assert [r["actual_inbound_amount"] for r in returns.rows] == [Decimal(0), Decimal("10.1256")]
    assert returns.rows[0]["period_start"] == date(2026, 10, 18)
    assert returns.rows[0]["shop_match_status"] == "not_provided"
    assert returns.raw[0]["json_data"]["实际入库金额"] == "nan"
    assert [r["row_kind"] for r in shipped.rows] == ["detail", "total"]
    assert shipped.rows[0]["source_product_code"] == "001"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_column",
        "invalid_number",
        "infinity",
        "bad_total",
        "duplicate",
        "missing_role",
        "conflicting_sku",
        "bad_period",
    ],
)
def test_rpa_rejects_ambiguous_delivery(failure):
    from app.modules.aftersales.rpa_ingestion import normalize

    rows = delivery()
    if failure == "missing_column":
        del rows[0]["json_data"]["实际入库金额"]
    elif failure == "invalid_number":
        rows[0]["json_data"]["实际入库金额"] = "坏数据"
    elif failure == "infinity":
        rows[0]["json_data"]["实际入库金额"] = "Infinity"
    elif failure == "bad_total":
        rows[-2]["json_data"]["实发金额"] = "200"
    elif failure == "duplicate":
        rows.append({**rows[0], "id": 999})
    elif failure == "missing_role":
        rows = [r for r in rows if r["class"] != "销售已取消"]
    elif failure == "conflicting_sku":
        rows[2]["json_data"]["款式编码"] = "OTHER"
    else:
        rows[0]["start_date"] = date(2026, 11, 17)
    with pytest.raises(ValueError, match="RPA_"):
        normalize(rows)


def test_publish_real_pg_totals_idempotence_yoy_and_failed_refresh(sales_pg, tmp_path):
    from app.modules.aftersales.query import AfterQuery, overview
    from app.modules.aftersales.repository import snapshot
    from app.modules.aftersales.rpa_publisher import sync_rpa

    rows = delivery(date(2025, 11, 16), 100) + delivery(date(2026, 11, 9), 200) + delivery()
    first = sync_rpa(sales_pg, lambda: deepcopy(rows), tmp_path)
    with snapshot(sales_pg) as repo:
        report_id = repo.report["id"]
        assert repo.report["previous_comparison_status"] == "available"
        assert repo.report["yoy_comparison_status"] == "available"
        board = overview(repo, AfterQuery())
        assert board["category_risks"][0]["risk_score"] == 100
        assert board["category_actions"] and board["style_actions"]
        assert board["decision"]["title"] == "童帽"
        assert len(board["actions"]) == 2
        assert all(a["status"] not in ("预警 P1", "关注 P2") for a in board["actions"])
        values = {m["key"]: m["value"] for m in board["metrics"]}
        assert values["returned_qty"] == Decimal(3)
        assert values["inbound_amount"] == Decimal("10.1256")
        assert values["gmv"] == Decimal(125)
        assert values["total_rate"] == Decimal("0.2810048")
        assert repo.source_summary("j2_shipped_sales")["rows"] == 1
    again = sync_rpa(sales_pg, lambda: deepcopy(rows), tmp_path)
    assert again["version_id"] == first["version_id"]
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM aftersales_reports")) == 3
    broken = deepcopy(rows)
    broken[-2]["json_data"]["实发金额"] = "900"
    with pytest.raises(ValueError, match="RPA_"):
        sync_rpa(sales_pg, lambda: broken, tmp_path)
    with snapshot(sales_pg) as repo:
        assert repo.report["id"] == report_id
    # 同源覆盖修订；旧报告仍固定旧值，不能跟随更新。
    changed = deepcopy(rows)
    changed[-5]["json_data"]["实际入库金额"] = "20.1256"
    sync_rpa(sales_pg, lambda: changed, tmp_path)
    with snapshot(sales_pg, report_id) as repo:
        assert repo.source_summary("j1_returns")["actual_inbound_amount"] == Decimal("10.1256")
    with snapshot(sales_pg) as repo:
        assert repo.source_summary("j1_returns")["actual_inbound_amount"] == Decimal("20.1256")


def test_summary_preserves_metrics_and_detail_retains_actions(sales_pg, tmp_path, monkeypatch):
    from app.api import aftersales as api
    from app.modules.aftersales.rpa_publisher import sync_rpa
    from tests.test_aftersales_api import client_for

    sync_rpa(sales_pg, lambda: delivery(date(2025, 11, 16), 100) + delivery(), tmp_path)
    calculate = api.overview
    calls = []

    def counted(repo, q):
        calls.append(repo.token)
        return calculate(repo, q)

    monkeypatch.setattr(api, "overview", counted)
    with client_for(sales_pg) as client:
        summary = client.get("/api/v1/aftersales/overview?detail=summary").json()["data"]
        full = client.get("/api/v1/aftersales/overview?detail=full").json()["data"]
        assert not summary["actions"] and full["actions"]
        assert summary == {
            **full,
            "actions": [],
            "category_actions": full["category_actions"][:3],
            "style_actions": full["style_actions"][:3],
        }
        report = full["report"]["id"]
        explicit = client.get("/api/v1/aftersales/overview", params={"report_id": report})
        assert len(full["trend"]) == 2
        assert explicit.json()["data"] == {
            **full,
            "trend": [point for point in full["trend"] if point["id"] == report],
        }
        assert len(calls) == 1
        cache = client.app.state.aftersales_overview_cache
        version, size = api.prewarm_aftersales_overview(sales_pg, cache, None)
        assert size and len(calls) == 2
        historical = full["trend"][0]["id"]
        assert client.get(
            "/api/v1/aftersales/overview", params={"report_id": historical}
        ).is_success
        assert len(calls) == 2
        assert api.prewarm_aftersales_overview(sales_pg, cache, version) == (version, None)


@pytest.mark.parametrize("sku", ["1", None])
def test_rpa_style_groups_pair_by_native_codes_and_roll_up_skus(sku):
    from app.modules.aftersales.metrics import groups

    base = dict(_rpa=True, source_product_category="帽子", source_style_code="K01")
    ret = [
        {
            **base,
            "source_product_code": sku,
            "source_product_name": "全称",
            "actual_returned_qty": Decimal(1),
            "actual_inbound_amount": Decimal(10),
        }
    ]
    ship = [
        {
            **base,
            "source_product_code": sku,
            "source_product_name": "简称",
            "sales_amount": Decimal(100),
        }
        for sku in ("1", "2")
    ]
    cancel = [{**base, "source_product_code": "1", "sales_amount": Decimal(25)}]
    result = groups([ret, ship, cancel], "style")
    assert len(result) == 1
    assert result[0]["metrics"]["gmv"] == Decimal(225)
    assert result[0]["metrics"]["inbound_amount"] == Decimal(10)


@pytest.mark.parametrize(
    "unknown_amount,absolute,expected",
    [("0", None, Decimal(125)), ("5", None, None), ("0", "20", None)],
)
def test_rpa_zero_amount_unclassified_sales_do_not_hide_known_category_denominators(
    unknown_amount, absolute, expected
):
    from app.modules.aftersales.metrics import groups

    base = dict(
        _rpa=True, source_product_category="帽子", source_style_code="K01", source_product_code="1"
    )
    returns = [{**base, "actual_returned_qty": Decimal(1), "actual_inbound_amount": Decimal(10)}]
    shipped = [
        {**base, "sales_amount": Decimal(100)},
        {"_rpa": True, "source_product_code": "unknown", "sales_amount": Decimal(unknown_amount)},
    ]
    cancelled = [{**base, "sales_amount": Decimal(25)}]
    if absolute is not None:
        shipped[-1]["_absolute_sales_amount"] = Decimal(absolute)
    result = next(
        r for r in groups([returns, shipped, cancelled], "category") if r["key"] == "帽子"
    )
    assert result["metrics"]["gmv"] == expected


def test_rpa_source_checks_two_independent_transactions(sales_pg):
    import psycopg
    from psycopg.types.json import Jsonb

    from app.connectors.aftersales_rpa import receive_stable

    url = sales_pg.url

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
        for row in delivery():
            c.execute(
                "INSERT INTO aftersales_reports_rpa VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    row["id"],
                    row["start_date"],
                    row["end_date"],
                    row["time_granularity"],
                    row["class"],
                    Jsonb(row["json_data"]),
                    row["updated_at"],
                ),
            )
    assert len(receive_stable(connect)) == 6
    calls = 0

    def changing_connect():
        nonlocal calls
        calls += 1
        if calls == 2:
            with connect() as writer:
                writer.execute(
                    "UPDATE aftersales_reports_rpa SET json_data=json_data || "
                    '\'{"实际入库金额":"20"}\' WHERE id=1'
                )
        return connect()

    with pytest.raises(ValueError, match="RPA_SOURCE_CHANGED"):
        receive_stable(changing_connect)


def test_rpa_existing_connection_options_are_overridden_once_and_read_only(sales_pg):
    from app.connectors.aftersales_rpa import connect_readonly

    url = sales_pg.url
    with connect_readonly(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=2,
        options="-c default_transaction_read_only=off",
    ) as c:
        assert c.execute("SHOW transaction_read_only").fetchone()[0] == "on"


def test_rpa_client_read_deadline_closes_stalled_connection(sales_pg, monkeypatch):
    import time

    import psycopg

    from app.connectors import aftersales_rpa

    monkeypatch.setattr(aftersales_rpa, "READ_TIMEOUT_SECONDS", 0.05, raising=False)
    url = sales_pg.url
    c = aftersales_rpa.connect_readonly(
        host=url.host, port=url.port, dbname=url.database, user=url.username, password=url.password
    )
    started = time.monotonic()
    try:
        with pytest.raises(psycopg.OperationalError):
            c.execute("SELECT pg_sleep(1)")
        assert c.closed
        assert time.monotonic() - started < 0.8
    finally:
        c.close()


def test_rpa_keeps_archived_previous_when_source_rotates_and_jst_cannot_replace(sales_pg, tmp_path):
    from app.modules.aftersales.publisher import publish_reports
    from app.modules.aftersales.repository import snapshot
    from app.modules.aftersales.rpa_publisher import sync_rpa

    sync_rpa(sales_pg, lambda: delivery(date(2026, 11, 9)), tmp_path)
    with snapshot(sales_pg) as repo:
        previous = repo.report["id"]
    sync_rpa(sales_pg, delivery, tmp_path)
    publish_reports(sales_pg, tmp_path)
    with snapshot(sales_pg) as repo:
        assert repo.report["period_end"] == date(2026, 11, 16)
        assert repo.report["previous_report_id"] == previous


def test_rpa_db_failure_rolls_back_all_facts_and_records_failure(sales_pg, tmp_path, monkeypatch):
    from app.modules.aftersales import rpa_publisher

    write = rpa_publisher.private

    def fail_report(path, value):
        if path.name == "report.json":
            raise OSError("synthetic archive failure")
        return write(path, value)

    monkeypatch.setattr(rpa_publisher, "private", fail_report)
    with pytest.raises(OSError):
        rpa_publisher.sync_rpa(sales_pg, delivery, tmp_path)
    with sales_pg.connect() as c:
        for table in (
            "return_records",
            "product_sales_records",
            "dataset_versions",
            "aftersales_reports",
        ):
            assert c.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 0
        assert c.scalar(sa.text("SELECT status FROM task_runs")) == "failed"


def test_rpa_source_switch_changes_default_and_token(sales_pg, tmp_path):
    from app.modules.aftersales.repository import snapshot
    from app.modules.aftersales.rpa_publisher import sync_rpa
    from tests.test_aftersales_api import publish

    publish(sales_pg)
    sync_rpa(sales_pg, delivery, tmp_path)
    with snapshot(sales_pg) as repo:
        before = repo.token
        assert repo.is_rpa()
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "UPDATE current_datasets SET version_id='report-1' "
                "WHERE dataset_code='aftersales_report'"
            )
        )
    with snapshot(sales_pg) as repo:
        assert repo.report["id"] == "report1"
        assert repo.token != before


def test_rpa_publication_is_not_requeued_as_jst_or_marked_stale(sales_pg, tmp_path):
    from app.jobs.source_monitor_runtime import pending_processing
    from app.modules.aftersales.rpa_publisher import sync_rpa
    from tests.test_aftersales_api import publish

    publish(sales_pg)
    with sales_pg.begin() as c:
        for code in ("jst_returns", "jst_shipped_sales", "jst_cancelled_sales"):
            c.execute(
                sa.text("INSERT INTO current_datasets(dataset_code,version_id) VALUES (:code,:id)"),
                dict(code=code, id=code + "1"),
            )
    sync_rpa(sales_pg, delivery, tmp_path)
    with sales_pg.connect() as c:
        assert "aftersales_report" not in pending_processing(c)
        assert (
            c.scalar(
                sa.text("""SELECT count(*) FROM current_datasets d
            JOIN version_inputs i ON i.output_version_id=d.version_id
            JOIN dataset_versions src ON src.id=i.input_version_id
            LEFT JOIN current_datasets p ON p.dataset_code=src.dataset_code
            WHERE d.dataset_code='aftersales_report'
            AND i.input_version_id IS DISTINCT FROM p.version_id""")
            )
            == 0
        )


def test_rpa_monthly_and_weekly_same_end_choose_same_default_and_source_heads(sales_pg, tmp_path):
    from app.modules.aftersales.repository import snapshot
    from app.modules.aftersales.rpa_publisher import sync_rpa

    weekly = delivery(date(2026, 10, 31))
    monthly = delivery(date(2026, 10, 31), 100)
    for row in monthly:
        row.update(time_granularity="monthly", start_date=date(2026, 10, 1))
    for rows in (monthly + weekly, weekly):
        sync_rpa(sales_pg, lambda rows=rows: deepcopy(rows), tmp_path)
        with snapshot(sales_pg) as repo:
            assert repo.report["report_type"] == "monthly"
            selected = repo.report["dataset_version_id"]
        with sales_pg.connect() as c:
            assert (
                c.scalar(
                    sa.text(
                        "SELECT version_id FROM current_datasets "
                        "WHERE dataset_code='aftersales_report'"
                    )
                )
                == selected
            )
            assert (
                c.scalar(
                    sa.text("""SELECT count(*) FROM version_inputs i
                JOIN dataset_versions v ON v.id=i.input_version_id
                JOIN current_datasets d ON d.dataset_code=v.dataset_code
                WHERE i.output_version_id=:id AND d.version_id<>i.input_version_id"""),
                    dict(id=selected),
                )
                == 0
            )


def test_rule_only_revision_reuses_source_facts(sales_pg, tmp_path, monkeypatch):
    from app.modules.aftersales import rpa_publisher as publisher

    first = publisher.sync_rpa(sales_pg, delivery, tmp_path)
    with sales_pg.connect() as c:
        count = c.scalar(sa.text("select count(*) from return_records"))
    monkeypatch.setattr(publisher, "producer", lambda: "f" * 64)
    updated = publisher.sync_rpa(sales_pg, delivery, tmp_path)
    assert updated["version_id"] != first["version_id"]
    assert updated["new_inputs"] == 0
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("select count(*) from return_records")) == count
