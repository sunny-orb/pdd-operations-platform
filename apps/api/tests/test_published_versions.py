"""轻量发布检查覆盖各域真实输入；仅查询版本/目录标识，不读取经营明细。"""

from datetime import date

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from migrations_v2.baseline_20261028.schema import build_metadata
from tests.test_aftersales_api import publish

pytest_plugins = ["tests.sales_pg_support"]


def publish_pointer(engine, code, suffix="1", *, published=True):
    """在隔离PG只创建通过版本约束的元信息；无业务事实可供端点查询。"""
    tables = build_metadata().tables
    versions = tables["public.dataset_versions"]
    vid = code + "-" + suffix
    derived = code in {"profile_links", "product_scores", "aftersales_report"}
    with engine.begin() as c:
        c.execute(
            versions.insert().values(
                id=vid,
                dataset_code=code,
                origin_kind="derived" if derived else "source",
                state="draft",
                source_system=None if derived else "synthetic",
                source_locator=None if derived else "synthetic:versions-test",
                row_schema_revision="test-v1",
                producer_revision="test-v1",
                interpretation_ref="synthetic:versions-test",
                period_start=date(2026, 10, 1),
                period_end=date(2026, 10, 27),
            )
        )
        if published:
            c.execute(
                versions.update()
                .where(versions.c.id == vid)
                .values(
                    state="validated",
                    content_fingerprint="synthetic:" + vid,
                    validation_evidence_ref="synthetic:verified",
                    received_at=None if derived else sa.func.now(),
                )
            )
            c.execute(versions.update().where(versions.c.id == vid).values(state="published"))
            c.execute(
                sa.text(
                    "INSERT INTO current_datasets(dataset_code, version_id) VALUES (:code,:vid) "
                    "ON CONFLICT (dataset_code) DO UPDATE SET version_id=excluded.version_id"
                ),
                dict(code=code, vid=vid),
            )


def test_missing_database_uses_existing_error_contract():
    with TestClient(create_app(Settings(_env_file=None, database_url=""))) as client:
        response = client.get("/api/v1/published-versions")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_shanghai_day_changes_only_calendar_dependent_domains(sales_pg, monkeypatch):
    from datetime import datetime

    from app.modules import published_versions

    class FixedDateTime(datetime):
        current = datetime(2026, 11, 30, 23, 59)

        @classmethod
        def now(cls, tz=None):
            assert str(tz) == "Asia/Shanghai"
            return cls.current.replace(tzinfo=tz)

    monkeypatch.setattr(published_versions, "datetime", FixedDateTime, raising=False)
    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app) as client:
        app.state.engine = sales_pg
        before = client.get("/api/v1/published-versions").json()["data"]
        FixedDateTime.current = datetime(2026, 12, 1)
        after = client.get("/api/v1/published-versions").json()["data"]
    assert {key for key in before if before[key] != after[key]} == {"promotion", "sales"}


def test_rule_definition_changes_invalidate_consuming_domains_without_new_data(
    sales_pg, monkeypatch
):
    from app.modules.aftersales import repository as aftersales_repository
    from app.modules.product_board import repository as products_repository
    from app.modules.promotion import metrics as promotion_metrics
    from app.modules.promotion import repository as promotion_repository

    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app) as client:
        app.state.engine = sales_pg
        for module, name, replacement, consumers in (
            (products_repository, "DEFINITION_REVISION", "new-products-rules", {"products"}),
            (
                promotion_repository,
                "definition_revision",
                lambda: "new-promotion-rules",
                {"promotion"},
            ),
            (
                promotion_metrics,
                "definition_revision",
                lambda: "new-ad-rules",
                {"promotion", "sales"},
            ),
            (aftersales_repository, "revision", lambda: "new-aftersales-rules", {"aftersales"}),
        ):
            before = client.get("/api/v1/published-versions").json()["data"]
            with monkeypatch.context() as patch:
                patch.setattr(module, name, replacement)
                after = client.get("/api/v1/published-versions").json()["data"]
            assert {key for key in before if before[key] != after[key]} == consumers


def test_empty_versions_are_stable_and_each_input_only_changes_its_consumers(sales_pg):
    # 预期取自各域snapshot的实际依赖；遗漏任何当前输入都会漏掉对应页面刷新。
    consumers = {
        "product_daily": {"products", "promotion"},
        "product_promotion": {"products", "promotion"},
        "profile_records": {"products", "promotion"},
        "profile_links": {"products", "promotion"},
        "score_standards": {"products"},
        "product_scores": {"products"},
        "shop_channel_product_promotion": {"promotion", "sales"},
        "shop_channel_star_shop": {"promotion", "sales"},
        "shop_promotion_history": {"promotion", "sales"},
        "shop_daily": {"sales"},
        "shop_ratings": {"sales"},
        "shop_regions": {"sales"},
        "product_regions": {"sales"},
        "shop_refunds": {"sales"},
        "sales_targets": {"sales"},
        "jst_returns": {"aftersales"},
        "jst_shipped_sales": {"aftersales"},
        "jst_cancelled_sales": {"aftersales"},
        "refund_targets": {"aftersales"},
        "aftersales_report": {"aftersales"},
        "weather_publications": set(),
    }
    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app) as client:
        app.state.engine = sales_pg

        def get():
            response = client.get("/api/v1/published-versions")
            assert response.status_code == 200, response.text
            return response.json()["data"]

        previous = get()
        assert set(previous) == {"products", "promotion", "sales", "aftersales"}
        assert all(isinstance(v, str) and len(v) == 64 for v in previous.values())
        assert get() == previous
        for code, expected in consumers.items():
            publish_pointer(sales_pg, code)
            current = get()
            assert {key for key in previous if current[key] != previous[key]} == expected, code
            assert get() == current
            previous = current
        publish_pointer(sales_pg, "product_daily", "draft", published=False)
        assert get() == previous


def test_aftersales_old_report_addition_changes_token_without_latest_pointer_change(sales_pg):
    publish(sales_pg, "new", end=date(2026, 10, 27))
    app = create_app(Settings(_env_file=None, database_url=""))
    with TestClient(app) as client:
        app.state.engine = sales_pg
        first = client.get("/api/v1/published-versions")
        assert first.status_code == 200
        with sales_pg.connect() as c:
            pointers = c.execute(
                sa.text("SELECT * FROM current_datasets ORDER BY dataset_code")
            ).all()
        publish(sales_pg, "old", end=date(2026, 10, 20))
        with sales_pg.connect() as c:
            assert (
                c.execute(sa.text("SELECT * FROM current_datasets ORDER BY dataset_code")).all()
                == pointers
            )
        second = client.get("/api/v1/published-versions").json()["data"]
        assert first.json()["data"]["aftersales"] != second["aftersales"]
        assert all(
            first.json()["data"][key] == second[key] for key in ("products", "promotion", "sales")
        )


def test_version_check_only_reads_small_metadata_projections(sales_pg):
    app = create_app(Settings(_env_file=None, database_url=""))
    statements = []
    read_only = []

    def inspect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
        if statement.lstrip().upper().startswith("SELECT"):
            cursor.execute("SHOW transaction_read_only")
            read_only.append(cursor.fetchone()[0])

    sa.event.listen(sales_pg, "before_cursor_execute", inspect)
    try:
        with TestClient(app) as client:
            app.state.engine = sales_pg
            response = client.get("/api/v1/published-versions")
            assert response.status_code == 200
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", inspect)
    selects = [s for s in statements if s.lstrip().startswith("select")]
    assert 1 <= len(selects) <= 2
    assert read_only and set(read_only) == {"on"}
    assert all("select *" not in statement and "v.*" not in statement for statement in selects)
    assert not any(
        word in " ".join(statements)
        for word in (
            "product_daily_facts",
            "shop_daily_facts",
            "return_records",
            "product_scores ",
            "coverage_records",
            "task_runs",
            "insert ",
            "update ",
            "delete ",
        )
    )
