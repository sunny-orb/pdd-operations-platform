"""真实PG内容复用：只写随机测试库，旧历史与当前身份不变。"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from tests.test_published_versions import publish_pointer

pytest_plugins = ["tests.sales_pg_support"]


def rows(amount="100.01000000000001"):
    return [
        dict(
            source_row_key=k,
            row_kind="detail",
            shop_match_status="not_provided",
            date_status="verified_period",
            period_start=date(2026, 10, 26),
            period_end=date(2026, 10, 26),
            sales_amount=Decimal(amount if k == "a" else "20"),
            source_sales_qty=Decimal("1.5"),
            source_style_code="0007",
        )
        for k in ("a", "b")
    ]


def version(engine, suffix):
    publish_pointer(engine, "jst_shipped_sales", suffix, published=False)
    return "jst_shipped_sales-" + suffix


def seal(c, vid):
    c.execute(
        sa.text(
            "UPDATE dataset_versions SET state='validated',content_fingerprint=:v, "
            "validation_evidence_ref='synthetic:verified',received_at=clock_timestamp() "
            "WHERE id=:v"
        ),
        {"v": vid},
    )
    c.execute(sa.text("UPDATE dataset_versions SET state='published' WHERE id=:v"), {"v": vid})


def test_shared_content_reuses_unchanged_rows_and_preserves_old_version(sales_pg):
    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")
    with sales_pg.begin() as c:
        write_version(c, "product_sales_records", a, rows())
        seal(c, a)
        write_version(c, "product_sales_records", b, rows("101.25"))
        seal(c, b)
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 3
        values = c.execute(
            sa.text(
                "SELECT dataset_version_id,source_row_key,sales_amount, "
                "source_sales_qty,source_product_code FROM read_product_sales_records "
                "ORDER BY 1,2"
            )
        ).all()
        assert values == [
            (a, "a", Decimal("100.01000000000001"), Decimal("1.5"), None),
            (a, "b", Decimal("20"), Decimal("1.5"), None),
            (b, "a", Decimal("101.25"), Decimal("1.5"), None),
            (b, "b", Decimal("20"), Decimal("1.5"), None),
        ]
        assert c.scalar(sa.text("SELECT count(*) FROM product_sales_records")) == 0


def test_repeated_content_and_new_producer_does_not_copy_blocks(sales_pg):
    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")
    with sales_pg.begin() as c:
        write_version(c, "product_sales_records", a, rows())
        seal(c, a)
        c.execute(
            sa.text("UPDATE dataset_versions SET producer_revision='changed-code' WHERE id=:v"),
            {"v": b},
        )
        write_version(c, "product_sales_records", b, list(reversed(rows())))
        write_version(c, "product_sales_records", b, rows())
        seal(c, b)
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 2
        assert c.scalar(sa.text("SELECT count(*) FROM read_product_sales_records")) == 4


def test_shared_sealed_data_and_manifest_cannot_be_changed(sales_pg):
    from app.modules.fact_content import write_version

    a = version(sales_pg, "a")
    with sales_pg.begin() as c:
        write_version(c, "product_sales_records", a, rows())
        seal(c, a)
    for sql in [
        "UPDATE shared_product_sales_records SET sales_amount=0",
        "DELETE FROM shared_product_sales_records",
        "DELETE FROM fact_content_members",
        "UPDATE fact_content_manifests SET row_count=99",
        "DELETE FROM fact_content_blocks",
        "TRUNCATE shared_product_sales_records",
    ]:
        with pytest.raises(sa.exc.DBAPIError):
            with sales_pg.begin() as c:
                c.execute(sa.text(sql))


def test_failed_write_is_atomic_and_duplicate_keys_rejected(sales_pg):
    from app.modules.fact_content import write_version

    a = version(sales_pg, "a")
    with pytest.raises(ValueError, match="CONTENT_DUPLICATE_KEY"):
        with sales_pg.begin() as c:
            write_version(c, "product_sales_records", a, [rows()[0], rows()[0]])
    with pytest.raises(RuntimeError, match="crash"):
        with sales_pg.begin() as c:
            write_version(c, "product_sales_records", a, rows())
            raise RuntimeError("crash")
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM fact_content_manifests")) == 0
        assert c.scalar(sa.text("SELECT count(*) FROM fact_content_blocks")) == 0


def test_deleted_rows_and_empty_snapshot_do_not_change_history(sales_pg):
    from app.modules.fact_content import write_version

    versions = [version(sales_pg, str(n)) for n in range(3)]
    with sales_pg.begin() as c:
        for vid, data in zip(versions, (rows(), rows()[1:], []), strict=True):
            write_version(c, "product_sales_records", vid, data)
            seal(c, vid)
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 2
        for vid, expected in zip(versions, (2, 1, 0), strict=True):
            assert (
                c.scalar(
                    sa.text(
                        "SELECT count(*) FROM read_product_sales_records "
                        "WHERE dataset_version_id=:v"
                    ),
                    {"v": vid},
                )
                == expected
            )


def test_namespaces_and_schema_revision_never_share(sales_pg):
    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")
    publish_pointer(sales_pg, "jst_cancelled_sales", "c", published=False)
    with sales_pg.begin() as c:
        c.execute(
            sa.text("UPDATE dataset_versions SET row_schema_revision='next-semantics' WHERE id=:v"),
            {"v": b},
        )
        for vid in (a, b, "jst_cancelled_sales-c"):
            write_version(c, "product_sales_records", vid, rows())
            seal(c, vid)
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 6


def test_concurrent_versions_publish_one_copy(sales_pg):
    from concurrent.futures import ThreadPoolExecutor

    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")

    def publish(vid):
        with sales_pg.begin() as c:
            c.execute(sa.text("SET LOCAL statement_timeout='10s'"))
            write_version(c, "product_sales_records", vid, rows())
            seal(c, vid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(publish, (a, b)))
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) == 2
        assert c.scalar(sa.text("SELECT count(*) FROM read_product_sales_records")) == 4


def test_partial_manifest_and_semantic_mutation_cannot_publish(sales_pg):
    from app.modules.fact_content import write_version

    a = version(sales_pg, "a")
    with pytest.raises(sa.exc.DBAPIError, match="CONTENT_MANIFEST_INCOMPLETE"):
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "INSERT INTO fact_content_manifests VALUES (:v,'product_sales_records',1,2)"
                ),
                {"v": a},
            )
            seal(c, a)
    with pytest.raises(sa.exc.DBAPIError, match="CONTENT_NAMESPACE"):
        with sales_pg.begin() as c:
            write_version(c, "product_sales_records", a, rows())
            c.execute(
                sa.text("UPDATE dataset_versions SET row_schema_revision='changed' WHERE id=:v"),
                {"v": a},
            )
            seal(c, a)


def test_json_null_precision_and_numeric_identity(sales_pg):
    from app.modules.fact_content import write_version

    for suffix in ("a", "b", "c"):
        publish_pointer(sales_pg, "jst_returns", suffix, published=False)
    with sales_pg.begin() as c:
        for suffix, obj in (
            ("a", {"k": None, "s": "1", "n": "1"}),
            ("b", {"n": "1", "s": "1", "k": None}),
            ("c", None),
        ):
            row = {
                k: v for k, v in rows()[0].items() if k not in ("sales_amount", "source_sales_qty")
            }
            row.update(
                actual_inbound_amount=Decimal("123456789012345678901234567890.001"),
                source_analysis_fields=obj,
            )
            vid = "jst_returns-" + suffix
            write_version(c, "return_records", vid, [row])
            seal(c, vid)
        assert c.scalar(sa.text("SELECT count(*) FROM shared_return_records")) == 2
        assert (
            c.scalar(
                sa.text(
                    "SELECT count(*) FROM read_return_records "
                    "WHERE actual_inbound_amount=123456789012345678901234567890.001"
                )
            )
            == 3
        )
        assert (
            c.scalar(
                sa.text(
                    "SELECT source_analysis_fields IS NULL FROM read_return_records "
                    "WHERE dataset_version_id='jst_returns-c'"
                )
            )
            is True
        )


def test_local_boundary_and_unknown_columns_fail_closed(sales_pg, monkeypatch):
    from types import SimpleNamespace

    from app.modules.fact_content import enabled, write_version

    monkeypatch.setenv("CONTENT_REUSE_ENABLED", "true")
    remote = SimpleNamespace(
        engine=SimpleNamespace(
            url=sa.engine.make_url("postgresql+psycopg://songbing@remote/pdd_operations")
        )
    )
    with pytest.raises(ValueError, match="PRODUCTION_DISABLED"):
        enabled(remote)
    with pytest.raises(ValueError, match="PRODUCTION_DISABLED"):
        write_version(remote, "product_sales_records", "x", [])
    a = version(sales_pg, "a")
    with pytest.raises(ValueError, match="CONTENT_UNKNOWN_COLUMN"):
        with sales_pg.begin() as c:
            write_version(
                c, "product_sales_records", a, [dict(rows()[0], unexpected="must not disappear")]
            )


def test_ingest_role_can_build_and_app_is_read_only(sales_pg):
    from app.modules.fact_content import write_version

    a = version(sales_pg, "a")
    with sales_pg.begin() as c:
        c.execute(sa.text("SET LOCAL ROLE pdd_ingest"))
        write_version(c, "product_sales_records", a, rows())
        seal(c, a)
    with sales_pg.begin() as c:
        c.execute(sa.text("SET LOCAL ROLE pdd_app"))
        assert c.scalar(sa.text("SELECT count(*) FROM read_product_sales_records")) == 2
    for role in ("pdd_app", "pdd_ingest"):
        with pytest.raises(sa.exc.DBAPIError):
            with sales_pg.begin() as c:
                c.execute(sa.text("SET LOCAL ROLE " + role))
                c.execute(sa.text("DELETE FROM shared_product_sales_records"))


def test_legacy_and_shared_cannot_mix_even_in_draft(sales_pg):
    from app.modules.fact_content import write_version
    from migrations_v2.baseline_20261028.schema import build_metadata

    table = build_metadata().tables["public.product_sales_records"]
    a = version(sales_pg, "a")
    for shared_first in (True, False):
        with pytest.raises(sa.exc.DBAPIError, match="CONTENT_MIXED_FORMAT"):
            with sales_pg.begin() as c:
                if shared_first:
                    write_version(c, "product_sales_records", a, rows())
                c.execute(table.insert(), [dict(rows()[0], id="old", dataset_version_id=a)])
                if not shared_first:
                    write_version(c, "product_sales_records", a, rows())


def test_moving_window_does_not_recopy_unaffected_business_days(sales_pg):
    from datetime import timedelta

    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")

    def received(start):
        return [
            dict(rows()[0], source_row_key=f"{day}/{i}", period_start=day, period_end=day)
            for day in [date(2026, 10, 1) + timedelta(days=d) for d in range(start, start + 10)]
            for i in range(400)
        ]

    with sales_pg.begin() as c:
        write_version(c, "product_sales_records", a, received(0))
        seal(c, a)
        write_version(c, "product_sales_records", b, received(1))
        seal(c, b)
        assert c.scalar(sa.text("SELECT count(*) FROM read_product_sales_records")) == 8000
        # 只新增第11天400行，不能重新存其余9天；留一点余量用于分块实现。
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_sales_records")) <= 5200


def test_unchanged_version_uses_batched_database_requests(sales_pg):
    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")
    values = [dict(rows()[0], source_row_key=str(n)) for n in range(300)]
    with sales_pg.begin() as c:
        write_version(c, "product_sales_records", a, values)
        seal(c, a)
    calls = []

    def observe(*args):
        calls.append(1)

    sa.event.listen(sales_pg, "before_cursor_execute", observe)
    try:
        with sales_pg.begin() as c:
            result = write_version(c, "product_sales_records", b, values)
            seal(c, b)
    finally:
        sa.event.remove(sales_pg, "before_cursor_execute", observe)
    assert result["new_blocks"] == 0
    assert len(calls) <= 12


def test_utc_instants_share_without_rounding_numeric_or_losing_null(sales_pg):
    from datetime import UTC, datetime, timedelta, timezone

    from app.modules.fact_content import write_version

    for suffix in ("a", "b"):
        publish_pointer(sales_pg, "product_daily", suffix, published=False)
    with sales_pg.begin() as c:
        for suffix, moment in (
            ("a", datetime(2026, 10, 26, 8, tzinfo=timezone(timedelta(hours=8)))),
            ("b", datetime(2026, 10, 26, 0, tzinfo=UTC)),
        ):
            row = dict(
                source_row_key="0001",
                listing_match_status="not_provided",
                date_status="verified_period",
                business_date=date(2026, 10, 26),
                source_created_at=moment,
                product_revenue=Decimal("123456789012345678901234567890.00100"),
            )
            vid = "product_daily-" + suffix
            write_version(c, "product_daily_facts", vid, [row])
            seal(c, vid)
        assert c.scalar(sa.text("SELECT count(*) FROM shared_product_daily_facts")) == 1
        assert c.scalar(
            sa.text("SELECT source_created_at FROM shared_product_daily_facts")
        ) == datetime(2026, 10, 26, 0, tzinfo=UTC)


def test_members_cannot_combine_same_source_key_from_different_days(sales_pg):
    from datetime import timedelta

    from app.modules.fact_content import write_version

    a, b, target = (version(sales_pg, s) for s in ("a", "b", "target"))
    with sales_pg.begin() as c:
        for vid, shift in ((a, 0), (b, 1)):
            row = dict(
                rows()[0],
                period_start=date(2026, 10, 26) + timedelta(days=shift),
                period_end=date(2026, 10, 26) + timedelta(days=shift),
            )
            write_version(c, "product_sales_records", vid, [row])
            seal(c, vid)
    with pytest.raises(sa.exc.DBAPIError, match="CONTENT_DUPLICATE_KEY"):
        with sales_pg.begin() as c:
            c.execute(
                sa.text(
                    "INSERT INTO fact_content_manifests VALUES(:v,'product_sales_records',1,2)"
                ),
                {"v": target},
            )
            c.execute(
                sa.text(
                    "INSERT INTO fact_content_members SELECT :v,bucket,block_id "
                    "FROM fact_content_members WHERE version_id IN (:a,:b)"
                ),
                {"v": target, "a": a, "b": b},
            )
            seal(c, target)


def test_numeric_scale_is_preserved_for_existing_serialized_roundtrip(sales_pg):
    from app.modules.fact_content import write_version

    a, b = version(sales_pg, "a"), version(sales_pg, "b")
    with sales_pg.begin() as c:
        for vid, amount in ((a, "100.0100"), (b, "100.01")):
            write_version(c, "product_sales_records", vid, rows(amount))
            seal(c, vid)
        actual = (
            c.execute(
                sa.text(
                    "SELECT sales_amount::text FROM read_product_sales_records "
                    "WHERE source_row_key='a' ORDER BY dataset_version_id"
                )
            )
            .scalars()
            .all()
        )
        assert actual == ["100.0100", "100.01"]
