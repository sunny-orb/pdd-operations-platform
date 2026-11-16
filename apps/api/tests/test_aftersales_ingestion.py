from datetime import date
from decimal import Decimal

import pytest


def test_source_projection_keeps_actual_quantities_and_blocks_unverified_money():
    from app.modules.aftersales.ingestion import normalize

    rows = normalize(
        "jst_returns",
        [
            dict(
                __id="12",
                shop_name="kk树-kocotree官方旗舰店-配饰",
                shop_code="17",
                product_code="0007",
                product_name="帽子",
                spec_name="蓝",
                confirm_date="2026-10-27 10:20:00",
                aftersale_num="1.5",
                actual_receive_amount="38.4",
                platform_subsidy_amount="0",
                reason_type="质量",
                aftersale_status="已确认",
                product_aftersale_type="退货",
            )
        ],
        date(2026, 10, 27),
    )
    assert rows[0]["actual_returned_qty"] == Decimal("1.5")
    assert rows[0]["actual_inbound_amount"] is None
    assert rows[0]["source_style_code"] == "0007"
    assert rows[0]["source_product_code"] is None
    assert rows[0]["period_start"] == date(2026, 10, 27)
    assert rows[0]["source_product_category"] is None


def test_shipped_never_falls_back_to_sale_amount_and_duplicate_id_rejected():
    from app.modules.aftersales.ingestion import normalize

    row = dict(
        __id="1",
        shop_name="kk树-kocotree官方旗舰店-配饰",
        shop_code="17",
        product_code="07",
        product_name="帽子",
        spec_name="蓝",
        pay_time="2026-10-27 11:00:00",
        sfje=None,
        sale_amount="100",
        sale_num="2",
        outstock_num="1",
    )
    assert normalize("jst_shipped_sales", [row], date(2026, 10, 27))[0]["sales_amount"] is None
    assert normalize("jst_cancelled_sales", [row], date(2026, 10, 27))[0][
        "sales_amount"
    ] == Decimal("100")
    with pytest.raises(ValueError, match="DUPLICATE"):
        normalize("jst_shipped_sales", [row, row], date(2026, 10, 27))


pytest_plugins = ["tests.sales_pg_support"]


def sample(end):
    from app.modules.aftersales.ingestion import normalize

    def receive(code):
        row = dict(
            __id="1",
            shop_name="kk树-kocotree官方旗舰店-配饰",
            shop_code="17",
            product_code="07",
            product_name="帽子",
            spec_name="蓝",
            confirm_date=f"{end} 10:00:00",
            pay_time=f"{end} 10:00:00",
            aftersale_num="2",
            actual_receive_amount="40",
            platform_subsidy_amount="0",
            reason_type="尺码",
            aftersale_status="已确认",
            product_aftersale_type="退货",
            sfje=None,
            sale_amount="100",
            sale_num="2",
            outstock_num="1",
        )
        return end, [row], normalize(code, [row], end)

    return receive


def test_real_pg_source_report_idempotence_and_failure_preserves_pointer(sales_pg, tmp_path):
    import sqlalchemy as sa

    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source

    end = date(2026, 10, 27)
    receive = sample(end)
    heads = {}
    for code in FACTS:
        first = publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)
        assert (
            publish_source(sales_pg, code, lambda hb, code=code: receive(code), tmp_path)[
                "version_id"
            ]
            == first["version_id"]
        )
        heads[code] = first["version_id"]
    report = publish_reports(sales_pg, tmp_path)
    assert publish_reports(sales_pg, tmp_path)["version_id"] == report["version_id"]

    def fail(hb):
        raise ConnectionError("not logged")

    with pytest.raises(ConnectionError):
        publish_source(sales_pg, "jst_returns", fail, tmp_path)
    with sales_pg.connect() as c:
        assert dict(
            c.execute(sa.text("SELECT dataset_code,version_id FROM current_datasets")).all()
        ) == dict(heads, aftersales_report=report["version_id"])
        assert c.execute(sa.text("SELECT COUNT(*) FROM aftersales_reports")).scalar_one() == 3
        assert (
            c.execute(
                sa.text(
                    "SELECT status FROM task_runs WHERE job_code='sync_jst_returns' "
                    "ORDER BY queued_at DESC LIMIT 1"
                )
            ).scalar_one()
            == "failed"
        )


def test_report_rejects_missing_roles_and_writer_permissions(sales_pg, tmp_path):
    from app.modules.aftersales.publisher import publish_reports

    with pytest.raises(ValueError, match="M5_INPUTS_MISSING"):
        publish_reports(sales_pg, tmp_path)
    import sqlalchemy as sa

    with sales_pg.connect() as c:
        for t in ("return_records", "product_sales_records", "aftersales_reports"):
            assert c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest',:t,'INSERT')"), dict(t=t)
            ).scalar_one()
            assert not c.execute(
                sa.text("SELECT has_table_privilege('pdd_ingest',:t,'UPDATE')"), dict(t=t)
            ).scalar_one()


def test_real_writer_can_publish_sources_reports_and_cannot_update_input(sales_pg, tmp_path):
    import sqlalchemy as sa

    from app.jobs.sync_shop_daily import runtime_engine
    from app.modules.aftersales.publisher import FACTS, publish_reports, publish_source

    configured = runtime_engine()
    writer = sa.create_engine(
        configured.url.set(database=sales_pg.url.database), hide_parameters=True
    )
    configured.dispose()
    receive = sample(date(2026, 10, 27))
    try:
        for code in FACTS:
            publish_source(writer, code, lambda hb, code=code: receive(code), tmp_path)
        assert publish_reports(writer, tmp_path)["status"] == "published"
        with writer.begin() as c, pytest.raises(sa.exc.DBAPIError):
            c.execute(sa.text("UPDATE return_records SET actual_returned_qty=0"))
    finally:
        writer.dispose()


def test_calendar_month_is_not_labeled_rolling_thirty_days():
    from app.modules.aftersales.repository import public_report

    row = dict(
        id="m",
        report_type="monthly",
        source_label=None,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        sealed_at=None,
        document_url=None,
        sheet_url=None,
    )
    assert public_report(row)["label"] == "2026年09月售后月报"


def identity_fact(**changes):
    """缺额、申请两件实退一件及拒收原类型均为有意的反例。"""
    return (
        dict(
            __id="1",
            shop_name="kk树-kocotree官方旗舰店-配饰",
            shop_code="17",
            product_code="KQ24001",
            spec_code="06941716542009",
            product_name="帽子",
            spec_name="蓝",
            confirm_date="2026-10-27 11:00:00",
            pay_time="2026-10-27 11:00:00",
            aftersale_num="1",
            apply_aftersale_num="2",
            actual_receive_amount="38.4",
            platform_subsidy_amount="0",
            reason_type=None,
            aftersale_status="已确认",
            aftersale_type="拒收退货",
            product_aftersale_type="退货",
            sfje=None,
            sale_amount="100",
            sale_num="2",
            outstock_num="1",
        )
        | changes
    )


def current_master(**changes):
    return (
        dict(
            __id="91",
            data_source="聚水潭标准API",
            product_code="KQ24001",
            spec_code="06941716542009",
            product_type="普通商品",
            suite_code=None,
            first_category="童帽秋冬",
            second_category="护耳帽",
            dt="2026-10-27",
            start_date=None,
            end_date=None,
        )
        | changes
    )


@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_current_jst_sku_identity_is_shared_without_changing_quantities_or_money(code):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    fact = identity_fact()
    raw = enrich_current_master([fact], [current_master()], "2026-10-27")
    rows = normalize(code, raw, date(2026, 10, 27))
    assert len(rows) == 1
    assert rows[0]["source_style_code"] == "KQ24001"
    assert rows[0]["source_product_code"] == "06941716542009"
    assert rows[0]["source_product_category"] == "童帽秋冬"
    assert rows[0]["source_subcategory"] == "护耳帽"
    assert rows[0]["source_virtual_category"] is None
    assert "jst_current_master_record" not in fact
    assert raw[0]["jst_current_master_record"]["__id"] == "91"
    assert raw[0]["jst_current_snapshot_date"] == "2026-10-27"
    if code == "jst_returns":
        assert rows[0]["source_aftersale_type"] == "拒收退货"
        assert rows[0]["actual_returned_qty"] == Decimal("1")
        assert rows[0]["actual_inbound_amount"] is None
        assert raw[0]["apply_aftersale_num"] == "2"
        assert raw[0]["product_aftersale_type"] == "退货"
    else:
        assert rows[0]["sales_amount"] == (None if code == "jst_shipped_sales" else Decimal("100"))


@pytest.mark.parametrize(
    "fact,master,want_status",
    [
        (identity_fact(spec_code=None), current_master(), "source_key_missing"),
        (identity_fact(), current_master(product_type="组合商品", suite_code="组合1"), "not_found"),
        (identity_fact(), current_master(spec_code="另一个SKU"), "not_found"),
        (identity_fact(), current_master(product_code="另一个款"), "identity_mismatch"),
    ],
)
def test_missing_combination_and_wrong_style_keep_fact_without_guessed_categories(
    fact, master, want_status
):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master([fact], [master], "2026-10-27")
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["actual_returned_qty"] == Decimal("1")
    assert row["source_style_code"] == "KQ24001"
    assert row["source_product_code"] == fact["spec_code"]
    assert row["source_product_category"] is None
    assert row["source_subcategory"] is None
    assert raw[0]["jst_current_mapping_status"] == want_status


@pytest.mark.parametrize(
    "master,error",
    [
        ([current_master(), current_master(__id="92")], "MASTER_DUPLICATE"),
        ([current_master(), current_master(__id="92", product_code="别款")], "MASTER_SKU_CONFLICT"),
        ([current_master(dt="2026-10-26")], "MASTER_SNAPSHOT"),
    ],
)
def test_current_master_ambiguity_and_stale_snapshot_cannot_publish(master, error):
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match=error):
        enrich_current_master([identity_fact()], master, "2026-10-27")


@pytest.mark.parametrize("changed", ["fact", "master", "snapshot"])
def test_reader_checks_both_fact_and_current_master_for_stability(changed):
    from app.connectors.aftersales import AftersalesReader

    reader = AftersalesReader.__new__(AftersalesReader)
    calls = dict(fact=0, master=0)

    def read(code, end):
        calls["fact"] += 1
        return [
            identity_fact(aftersale_num="2" if changed == "fact" and calls["fact"] > 1 else "1")
        ]

    def read_master(raw):
        calls["master"] += 1
        snapshot = "2026-10-28" if changed == "snapshot" and calls["master"] > 1 else "2026-10-27"
        return snapshot, [
            current_master(
                dt=snapshot,
                second_category="围脖" if changed == "master" and calls["master"] > 1 else "护耳帽",
            )
        ]

    reader.read = read
    reader.read_current_master = read_master
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_READ"):
        reader.read_stable("jst_returns", date(2026, 10, 27))


def test_reader_stable_result_retains_master_evidence_in_raw():
    from app.connectors.aftersales import AftersalesReader

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.read = lambda code, end: [identity_fact()]
    reader.read_current_master = lambda raw: ("2026-10-27", [current_master()])
    raw, rows = reader.read_stable("jst_returns", date(2026, 10, 27))
    assert raw[0]["jst_current_master_source"] == "cubeappdata.std_api_jstbz_product_spec_info_f"
    assert raw[0]["jst_current_mapping_status"] == "matched"
    assert rows[0]["source_subcategory"] == "护耳帽"


def test_real_pg_current_master_identity_and_refusal_evidence_are_immutable(sales_pg, tmp_path):
    import json
    from pathlib import Path

    import sqlalchemy as sa

    from app.modules.aftersales.ingestion import enrich_current_master, normalize
    from app.modules.aftersales.publisher import publish_source

    end = date(2026, 10, 27)
    raw = enrich_current_master([identity_fact()], [current_master()], "2026-10-27")
    normalized = normalize("jst_returns", raw, end)
    result = publish_source(sales_pg, "jst_returns", lambda hb: (end, raw, normalized), tmp_path)
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM return_records")).mappings().one()
        assert row["source_style_code"] == "KQ24001"
        assert row["source_product_code"] == "06941716542009"
        assert row["source_product_category"] == "童帽秋冬"
        assert row["source_subcategory"] == "护耳帽"
        assert row["source_virtual_category"] is None
        assert row["source_aftersale_type"] == "拒收退货"
        assert row["actual_inbound_amount"] is None
        archive = c.execute(
            sa.text("SELECT retained_input_ref FROM dataset_versions WHERE id=:id"),
            dict(id=result["version_id"]),
        ).scalar_one()
    evidence = json.loads(Path(archive).read_text().strip())
    assert evidence["apply_aftersale_num"] == "2"
    assert evidence["jst_current_master_record"]["first_category"] == "童帽秋冬"
    assert evidence["jst_current_snapshot_date"] == "2026-10-27"
    with sales_pg.begin() as c, pytest.raises(sa.exc.DBAPIError):
        c.execute(sa.text("UPDATE return_records SET source_style_code='改写'"))


def full_style_master():
    return [
        current_master(__id="91", spec_code="其他SKU1"),
        current_master(__id="92", spec_code="其他SKU2"),
    ]


@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_complete_current_style_consensus_only_enriches_categories_not_sku_identity(code):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master(
        [identity_fact()], [], "2026-10-27", {"KQ24001": full_style_master()}
    )
    row = normalize(code, raw, date(2026, 10, 27))[0]
    assert row["source_product_category"] == "童帽秋冬"
    assert row["source_subcategory"] == "护耳帽"
    assert row["source_style_code"] == "KQ24001"
    assert row["source_product_code"] == "06941716542009"
    assert row["source_product_name"] == "帽子"
    assert row["source_virtual_category"] is None
    assert raw[0]["jst_current_mapping_status"] == "not_found"
    assert raw[0]["jst_current_master_record"] is None
    assert raw[0]["category_match_method"] == "style_consensus"
    evidence = raw[0]["jst_current_style_category_evidence"]
    assert len(evidence["candidates"]) == 2
    assert evidence["scope"] == "all_current_ordinary_skus_for_style"
    assert len(evidence["fingerprint"]) == 64
    if code == "jst_returns":
        assert row["actual_returned_qty"] == Decimal("1")
        assert row["actual_inbound_amount"] is None
    else:
        assert row["source_sales_qty"] == Decimal("1" if code == "jst_shipped_sales" else "2")
        assert row["sales_amount"] == (None if code == "jst_shipped_sales" else Decimal("100"))


@pytest.mark.parametrize("value", [None, "", "  ", "不同分类", 123, False])
@pytest.mark.parametrize("field", ["first_category", "second_category"])
def test_style_consensus_is_independent_per_field_and_requires_every_candidate(field, value):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    candidates = full_style_master()
    candidates[1][field] = value
    raw = enrich_current_master([identity_fact()], [], "2026-10-27", {"KQ24001": candidates})
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["source_product_category"] == (None if field == "first_category" else "童帽秋冬")
    assert row["source_subcategory"] == (None if field == "second_category" else "护耳帽")


@pytest.mark.parametrize(
    "change,error",
    [
        ({"dt": "2026-10-26"}, "STYLE_CATEGORY_IDENTITY"),
        ({"product_code": "别款"}, "STYLE_CATEGORY_IDENTITY"),
        ({"product_type": "组合商品"}, "STYLE_CATEGORY_IDENTITY"),
        ({"suite_code": "组合1"}, "STYLE_CATEGORY_IDENTITY"),
        ({"spec_code": None}, "STYLE_CATEGORY_KEY"),
        ({"spec_code": "其他SKU1"}, "MASTER_DUPLICATE"),
    ],
)
def test_wrong_or_duplicate_style_candidates_cannot_infer(change, error):
    from app.modules.aftersales.ingestion import enrich_current_master

    candidates = full_style_master()
    candidates[1].update(change)
    with pytest.raises(ValueError, match=error):
        enrich_current_master([identity_fact()], [], "2026-10-27", {"KQ24001": candidates})


@pytest.mark.parametrize("sku", ["   ", "\t", "\n"])
def test_style_consensus_rejects_whitespace_only_candidate_sku(sku):
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match="STYLE_CATEGORY_KEY"):
        enrich_current_master(
            [identity_fact()],
            [],
            "2026-10-27",
            {"KQ24001": [current_master(spec_code=sku)]},
        )


@pytest.mark.parametrize("field", ["product_code", "spec_code"])
def test_whitespace_fact_keys_are_ineligible_for_style_category_evidence(field):
    from app.modules.aftersales.ingestion import enrich_current_master

    fact = identity_fact(**{field: "   "})
    style = fact["product_code"]
    with pytest.raises(ValueError, match="STYLE_CATEGORY_SCOPE"):
        enrich_current_master(
            [fact],
            [],
            "2026-10-27",
            {style: [current_master(product_code=style, spec_code="其他SKU")]},
        )


def test_style_category_replay_rejects_whitespace_only_fact_sku():
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master(
        [identity_fact()], [], "2026-10-27", {"KQ24001": full_style_master()}
    )
    raw[0]["spec_code"] = "   "
    with pytest.raises(ValueError, match="STYLE_CATEGORY_SCOPE"):
        normalize("jst_returns", raw, date(2026, 10, 27))


def test_style_consensus_preserves_nonblank_codes_and_leading_zeroes():
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master(
        [identity_fact(product_code="0007", spec_code="0009")],
        [],
        "2026-10-27",
        {"0007": [current_master(product_code="0007", spec_code="0011")]},
    )
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["source_style_code"] == "0007"
    assert row["source_product_code"] == "0009"
    assert row["source_product_category"] == "童帽秋冬"
    assert raw[0]["jst_current_style_category_evidence"]["candidates"][0]["spec_code"] == "0011"


def test_same_style_valid_fact_does_not_make_whitespace_sku_eligible():
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master(
        [identity_fact(), identity_fact(__id="2", spec_code="   ")],
        [],
        "2026-10-27",
        {"KQ24001": full_style_master()},
    )
    rows = normalize("jst_returns", raw, date(2026, 10, 27))
    assert rows[0]["source_product_category"] == "童帽秋冬"
    assert rows[1]["source_product_category"] is None
    assert rows[1]["source_subcategory"] is None
    assert rows[1]["source_product_code"] == "   "
    assert raw[1]["jst_current_mapping_status"] == "not_found"
    assert raw[1]["category_match_method"] == "unmatched"
    assert raw[1]["jst_current_style_category_evidence"] is None


def test_partial_requested_sku_master_cannot_be_used_as_a_complete_style_scope():
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    facts = [identity_fact(), identity_fact(__id="2", spec_code="其他SKU1")]
    raw = enrich_current_master(facts, [full_style_master()[0]], "2026-10-27")
    rows = normalize("jst_returns", raw, date(2026, 10, 27))
    assert rows[0]["source_product_category"] is None
    assert raw[0]["category_match_method"] == "unmatched"
    assert rows[1]["source_product_category"] == "童帽秋冬"
    assert raw[1]["category_match_method"] == "exact_sku"


@pytest.mark.parametrize(
    "fact,master",
    [
        (identity_fact(spec_code=None), []),
        (identity_fact(), [current_master(product_code="别款")]),
        (identity_fact(), [current_master()]),
    ],
)
def test_style_evidence_cannot_override_missing_conflicting_or_exact_sku_identity(fact, master):
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match="STYLE_CATEGORY_SCOPE"):
        enrich_current_master([fact], master, "2026-10-27", {"KQ24001": full_style_master()})


def test_style_evidence_is_bounded_and_cannot_be_tampered_during_replay():
    from copy import deepcopy

    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    with pytest.raises(ValueError, match="STYLE_CATEGORY_ROW_LIMIT"):
        enrich_current_master(
            [identity_fact()], [], "2026-10-27", {"KQ24001": [current_master()] * 5001}
        )
    raw = enrich_current_master(
        [identity_fact()], [], "2026-10-27", {"KQ24001": full_style_master()}
    )
    changed = deepcopy(raw)
    changed[0]["jst_current_style_category_evidence"]["candidates"][0]["first_category"] = "篡改"
    with pytest.raises(ValueError, match="STYLE_CATEGORY_FINGERPRINT"):
        normalize("jst_returns", changed, date(2026, 10, 27))


def test_full_style_and_exact_sku_reads_must_agree_on_overlapping_records():
    from app.modules.aftersales.ingestion import enrich_current_master

    facts = [identity_fact(), identity_fact(__id="2", spec_code="其他SKU1")]
    with pytest.raises(ValueError, match="MASTER_INCONSISTENT"):
        enrich_current_master(
            facts,
            [full_style_master()[0]],
            "2026-10-27",
            {"KQ24001": [dict(item, first_category="新分类") for item in full_style_master()]},
        )


def test_style_candidates_cannot_reveal_a_sku_missing_from_the_exact_read():
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match="MASTER_INCONSISTENT"):
        enrich_current_master([identity_fact()], [], "2026-10-27", {"KQ24001": [current_master()]})


def test_styles_with_no_current_master_keep_empty_categories_and_replayable_empty_scope():
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master([identity_fact()], [], "2026-10-27", {"KQ24001": []})
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["source_product_category"] is None and row["source_subcategory"] is None
    assert raw[0]["category_match_method"] == "unmatched"
    assert raw[0]["jst_current_mapping_status"] == "not_found"
    assert raw[0]["jst_current_style_category_evidence"]["candidates"] == []


def test_style_candidates_cannot_reuse_the_same_sku_in_different_styles():
    from app.modules.aftersales.ingestion import enrich_current_master

    facts = [identity_fact(), identity_fact(__id="2", product_code="别款", spec_code="缺失SKU2")]
    with pytest.raises(ValueError, match="MASTER_SKU_CONFLICT"):
        enrich_current_master(
            facts,
            [],
            "2026-10-27",
            {
                "KQ24001": full_style_master(),
                "别款": [current_master(product_code="别款", spec_code="其他SKU1")],
            },
        )


def test_reader_includes_unrequested_style_skus_and_checks_style_evidence_stability():
    from app.connectors.aftersales import AftersalesReader

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.read = lambda code, end: [identity_fact()]
    reader.read_current_master = lambda raw: ("2026-10-27", [])
    calls = 0

    def read_styles(raw, master, snapshot):
        nonlocal calls
        calls += 1
        candidates = full_style_master()
        if calls > 1:
            candidates[1]["second_category"] = "其他分类"
        return {"KQ24001": candidates}

    reader.read_current_style_categories = read_styles
    reader.read_current_bundle_categories = lambda raw, master, snapshot: {}
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_READ"):
        reader.read_stable("jst_returns", date(2026, 10, 27))


def style_query_reader(candidates, selected_style="KQ24001"):
    """仅替换外部连接，真实执行连接器的有界SQL构造和分类输入交付。"""
    from types import SimpleNamespace

    from app.connectors.aftersales import AftersalesReader

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params):
            assert "product_code IN (%s)" in query
            assert "spec_code IN" not in query
            assert "dt=%s" in query and "LIMIT 5001" in query
            assert "product_type=%s" in query and "suite_code IS NULL" in query
            assert "product_name" not in query
            assert params == ("普通商品", "2026-10-27", selected_style)

        def fetchall(self):
            return candidates

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.connection = SimpleNamespace(cursor=Cursor)
    reader.retry_budget = None
    reader.heartbeat = lambda: None
    return reader


@pytest.mark.parametrize("field", ["product_code", "spec_code"])
def test_style_reader_excludes_whitespace_fact_keys_from_requested_styles(field):
    fact = identity_fact(**{field: "   "})
    reader = style_query_reader([], selected_style=fact["product_code"])
    assert reader.read_current_style_categories([fact], [], "2026-10-27") == {}


def test_style_reader_fetches_complete_unrequested_skus_for_only_unmatched_styles():
    reader = style_query_reader(full_style_master())
    result = reader.read_current_style_categories([identity_fact()], [], "2026-10-27")
    assert {row["spec_code"] for row in result["KQ24001"]} == {"其他SKU1", "其他SKU2"}
    # 无缺失SKU时不访问连接；缺键或身份矛盾也不申请全款分类。
    reader.connection = None
    assert (
        reader.read_current_style_categories([identity_fact()], [current_master()], "2026-10-27")
        == {}
    )
    assert (
        reader.read_current_style_categories([identity_fact(spec_code=None)], [], "2026-10-27")
        == {}
    )
    assert (
        reader.read_current_style_categories(
            [identity_fact()], [current_master(product_code="别款")], "2026-10-27"
        )
        == {}
    )


def test_style_reader_rejects_5001_rows_instead_of_accepting_a_truncated_consensus():
    reader = style_query_reader([current_master()] * 5001)
    with pytest.raises(ValueError, match="STYLE_CATEGORY_ROW_LIMIT"):
        reader.read_current_style_categories([identity_fact()], [], "2026-10-27")


def numeric_source_reader(ids, scopes=None, reverse_page=False):
    """模拟源数值列与CHAR投影别名的不同排序，真实运行连接器分页及完整性门禁。"""
    from types import SimpleNamespace

    from app.connectors.aftersales import AftersalesReader

    ids = list(ids)
    scope = dict(
        row_count=len(ids), min_id=min(ids) if ids else None, max_id=max(ids) if ids else None
    )
    scope_reads = 0
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params):
            nonlocal scope_reads
            queries.append((query, params))
            if "COUNT(*)" in query:
                self.scope = scope if scopes is None else scopes[min(scope_reads, len(scopes) - 1)]
                scope_reads += 1
                return
            after = params[-1]
            selected = [value for value in ids if value > after]
            # 源数值列限定名绕开SELECT别名；未限定的__id沿CHAR别名按文本排序。
            numeric_order = "ORDER BY s.__id" in query or "ORDER BY s.`__id`" in query
            selected.sort(key=(int if numeric_order else str), reverse=reverse_page)
            self.page = [identity_fact(__id=str(value)) for value in selected[:5000]]

        def fetchone(self):
            return self.scope

        def fetchall(self):
            return self.page

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.connection = SimpleNamespace(cursor=Cursor)
    reader.retry_budget = None
    reader.heartbeat = lambda: None
    return reader, queries


@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_numeric_keyset_pagination_preserves_shorter_ids_and_string_projection(code):
    ids = [*range(10000000, 10005000), 900001, 9000001]
    reader, queries = numeric_source_reader(ids)
    rows = reader.read(code, date(2026, 10, 27))
    assert len(rows) == 5002
    assert [row["__id"] for row in rows] == [str(value) for value in sorted(ids)]
    scope_queries = [(query, params) for query, params in queries if "COUNT(*)" in query]
    assert len(scope_queries) == 2
    assert scope_queries[0] == scope_queries[1]
    assert scope_queries[0][1][-2:] == (date(2026, 8, 29), date(2026, 10, 28))
    for query, params in queries:
        assert "s.channel=%s" in query and "s.shop_name IN (%s,%s,%s,%s,%s,%s,%s)" in query
        assert query.count("%s") == len(params)
        assert ("s.order_status=%s" in query) == (code != "jst_returns")
        if "COUNT(*)" not in query:
            assert "AND s.__id>%s ORDER BY s.__id" in query
            assert params[:-1] == scope_queries[0][1]


def test_source_count_prevents_two_stable_reads_from_silently_losing_rows():
    reader, _ = numeric_source_reader([10, 20], scopes=[dict(row_count=3, min_id=10, max_id=20)])
    with pytest.raises(ValueError, match="SOURCE_ROW_COUNT_MISMATCH"):
        reader.read("jst_shipped_sales", date(2026, 10, 27))


def test_source_scope_change_while_reading_cannot_publish_partial_scope():
    reader, _ = numeric_source_reader(
        [10, 20],
        scopes=[dict(row_count=2, min_id=10, max_id=20), dict(row_count=3, min_id=10, max_id=30)],
    )
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_READ"):
        reader.read("jst_cancelled_sales", date(2026, 10, 27))


def test_source_id_extrema_and_strict_numeric_page_order_are_required():
    reader, _ = numeric_source_reader([10, 20], scopes=[dict(row_count=2, min_id=1, max_id=20)])
    with pytest.raises(ValueError, match="SOURCE_ID_COVERAGE_MISMATCH"):
        reader.read("jst_returns", date(2026, 10, 27))
    reader, _ = numeric_source_reader([10, 20], reverse_page=True)
    with pytest.raises(ValueError, match="SOURCE_ID_ORDER_INVALID"):
        reader.read("jst_returns", date(2026, 10, 27))


def test_real_pg_style_consensus_archive_replay_and_failure_keep_previous_version(
    sales_pg, tmp_path
):
    import json
    from pathlib import Path

    import sqlalchemy as sa

    from app.modules.aftersales.ingestion import enrich_current_master, normalize
    from app.modules.aftersales.publisher import publish_source

    end = date(2026, 10, 27)
    raw = enrich_current_master(
        [identity_fact()], [], "2026-10-27", {"KQ24001": full_style_master()}
    )
    normalized = normalize("jst_returns", raw, end)

    def receive(hb):
        return end, raw, normalized

    first = publish_source(sales_pg, "jst_returns", receive, tmp_path)
    assert (
        publish_source(sales_pg, "jst_returns", receive, tmp_path)["version_id"]
        == first["version_id"]
    )
    with sales_pg.connect() as c:
        row = c.execute(sa.text("SELECT * FROM return_records")).mappings().one()
        assert row["source_product_category"] == "童帽秋冬"
        assert row["source_subcategory"] == "护耳帽"
        assert row["source_product_code"] == "06941716542009"
        assert row["actual_returned_qty"] == Decimal("1")
        assert row["actual_inbound_amount"] is None
        version = (
            c.execute(
                sa.text("SELECT * FROM dataset_versions WHERE id=:id"), dict(id=first["version_id"])
            )
            .mappings()
            .one()
        )
        assert version["row_schema_revision"] == "m5-jst-current-bundle-consensus-v4"
    archived = [
        json.loads(line) for line in Path(version["retained_input_ref"]).read_text().splitlines()
    ]
    assert archived[0]["category_match_method"] == "style_consensus"
    assert len(archived[0]["jst_current_style_category_evidence"]["candidates"]) == 2
    assert normalize("jst_returns", archived, end) == normalized

    def invalid(hb):
        enrich_current_master(
            [identity_fact()], [], "2026-10-27", {"KQ24001": full_style_master() * 2}
        )

    with pytest.raises(ValueError, match="MASTER_DUPLICATE"):
        publish_source(sales_pg, "jst_returns", invalid, tmp_path)
    with sales_pg.connect() as c:
        assert (
            c.execute(sa.text("SELECT version_id FROM current_datasets")).scalar_one()
            == first["version_id"]
        )
        assert c.execute(sa.text("SELECT COUNT(*) FROM return_records")).scalar_one() == 1


def bundle_master(**changes):
    """组合资料原生一级未知；子件真实身份不等于父组合事实身份。"""
    return (
        dict(
            __id="bundle-1",
            data_source="JST",
            product_code="组件款1",
            spec_code="组件SKU1",
            suite_code="KQ24001",
            suite_spec_code="06941716542009",
            product_type="组合商品",
            first_category=None,
            second_category="护耳帽",
            dt="2026-10-27",
        )
        | changes
    )


def bundle_candidates():
    return [bundle_master(), bundle_master(__id="bundle-2", spec_code="组件SKU2")]


@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_bundle_native_category_consensus_only_adds_verified_fields_preserving_fact(code):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    fact = identity_fact()
    end = date(2026, 10, 27)
    baseline = normalize(code, enrich_current_master([fact], [], "2026-10-27"), end)[0]
    raw = enrich_current_master(
        [fact],
        [],
        "2026-10-27",
        bundle_candidates={("KQ24001", "06941716542009"): bundle_candidates()},
    )
    row = normalize(code, raw, end)[0]
    assert row == baseline | {"source_subcategory": "护耳帽"}
    assert raw[0]["category_match_method"] == "current_bundle_category_consensus"
    assert raw[0]["jst_current_mapping_status"] == "not_found"
    assert raw[0]["jst_current_master_record"] is None
    assert len(raw[0]["jst_current_bundle_category_evidence"]["candidates"]) == 2
    assert "jst_current_bundle_category_evidence" not in fact


@pytest.mark.parametrize("field", ["first_category", "second_category"])
@pytest.mark.parametrize("bad_value", [None, "", "   ", 17, "不一致分类"])
def test_bundle_native_categories_require_independent_complete_nonempty_consensus(field, bad_value):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    candidates = [
        bundle_master(first_category="童帽秋冬"),
        bundle_master(__id="bundle-2", spec_code="组件SKU2", first_category="童帽秋冬")
        | {field: bad_value},
    ]
    raw = enrich_current_master(
        [identity_fact()],
        [],
        "2026-10-27",
        {"KQ24001": full_style_master()},
        bundle_candidates={("KQ24001", "06941716542009"): candidates},
    )
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["source_product_category"] == (None if field == "first_category" else "童帽秋冬")
    assert row["source_subcategory"] == (None if field == "second_category" else "护耳帽")


@pytest.mark.parametrize(
    "changes",
    [
        {"suite_code": "别款"},
        {"suite_spec_code": "别父SKU"},
        {"dt": "2026-10-26"},
        {"product_type": "普通商品"},
        {"product_code": None},
        {"spec_code": "   "},
        {"suite_code": " "},
        {"suite_spec_code": False},
        {"__id": ""},
    ],
)
def test_bundle_candidates_reject_wrong_parent_scope_type_snapshot_and_invalid_keys(changes):
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match="BUNDLE_CATEGORY"):
        enrich_current_master(
            [identity_fact()],
            [],
            "2026-10-27",
            bundle_candidates={("KQ24001", "06941716542009"): [bundle_master(**changes)]},
        )


@pytest.mark.parametrize(
    "extra",
    [
        bundle_master(__id="bundle-2"),
        bundle_master(spec_code="组件SKU2"),
    ],
)
def test_bundle_duplicate_component_or_record_cannot_be_arbitrarily_selected(extra):
    from app.modules.aftersales.ingestion import enrich_current_master

    with pytest.raises(ValueError, match="BUNDLE_CATEGORY_DUPLICATE"):
        enrich_current_master(
            [identity_fact()],
            [],
            "2026-10-27",
            bundle_candidates={("KQ24001", "06941716542009"): [bundle_master(), extra]},
        )


def test_bundle_evidence_replay_rejects_partial_scope_count_and_tampering():
    from copy import deepcopy

    from app.modules.aftersales.ingestion import enrich_current_master, normalize
    from app.modules.sales.normalization import fingerprint

    raw = enrich_current_master(
        [identity_fact()],
        [],
        "2026-10-27",
        bundle_candidates={("KQ24001", "06941716542009"): bundle_candidates()},
    )
    for changes in [
        {"complete": False},
        {"candidate_count": 3},
        {"candidates": bundle_candidates()[:1]},
    ]:
        changed = deepcopy(raw)
        proof = changed[0]["jst_current_bundle_category_evidence"]
        proof.update(changes)
        proof["fingerprint"] = fingerprint({k: v for k, v in proof.items() if k != "fingerprint"})
        with pytest.raises(ValueError, match="BUNDLE_CATEGORY"):
            normalize("jst_returns", changed, date(2026, 10, 27))
    changed = deepcopy(raw)
    changed[0]["jst_current_bundle_category_evidence"]["candidates"][0]["second_category"] = "篡改"
    with pytest.raises(ValueError, match="BUNDLE_CATEGORY_FINGERPRINT"):
        normalize("jst_returns", changed, date(2026, 10, 27))


def test_bundle_scope_only_applies_to_unmatched_nonblank_parent_keys_and_stays_bounded():
    from app.modules.aftersales.ingestion import enrich_current_master

    for fact, master in [
        (identity_fact(), [current_master()]),
        (identity_fact(), [current_master(product_code="别款")]),
        (identity_fact(spec_code="  "), []),
    ]:
        with pytest.raises(ValueError, match="BUNDLE_CATEGORY_SCOPE"):
            enrich_current_master(
                [fact],
                master,
                "2026-10-27",
                bundle_candidates={("KQ24001", "06941716542009"): bundle_candidates()},
            )
    with pytest.raises(ValueError, match="BUNDLE_CATEGORY_ROW_LIMIT"):
        enrich_current_master(
            [identity_fact()],
            [],
            "2026-10-27",
            bundle_candidates={("KQ24001", "06941716542009"): [bundle_master()] * 5001},
        )


@pytest.mark.parametrize("code", ["jst_returns", "jst_shipped_sales", "jst_cancelled_sales"])
def test_exact_bundle_native_fields_replace_style_inference_without_filling_nulls(code):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    end = date(2026, 10, 27)
    baseline = normalize(code, enrich_current_master([identity_fact()], [], "2026-10-27"), end)
    styles = [
        dict(row, first_category="童配秋冬", second_category="软壳裤")
        for row in full_style_master()
    ]
    raw = enrich_current_master(
        [identity_fact()],
        [],
        "2026-10-27",
        {"KQ24001": styles},
        bundle_candidates={
            ("KQ24001", "06941716542009"): [bundle_master(second_category="软壳衣")]
        },
    )
    assert normalize(code, raw, end) == [baseline[0] | {"source_subcategory": "软壳衣"}]
    assert raw[0]["jst_current_mapping_status"] == "not_found"
    assert raw[0]["category_match_method"] == "current_bundle_category_consensus"


@pytest.mark.parametrize(
    "candidates, expected, method",
    [
        ([bundle_master(second_category=None)], (None, None), "unmatched"),
        ([], ("童帽秋冬", "护耳帽"), "style_consensus"),
    ],
)
def test_bundle_scope_presence_controls_fallback_and_method_even_when_native_fields_empty(
    candidates, expected, method
):
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master(
        [identity_fact()],
        [],
        "2026-10-27",
        {"KQ24001": full_style_master()},
        bundle_candidates={("KQ24001", "06941716542009"): candidates},
    )
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert (row["source_product_category"], row["source_subcategory"]) == expected
    assert raw[0]["category_match_method"] == method


def bundle_query_reader(candidates, expected_count=None, selected_keys=None):
    from types import SimpleNamespace

    from pymysql.err import ProgrammingError

    from app.connectors.aftersales import AftersalesReader

    selected_keys = sorted(selected_keys or [("KQ24001", "06941716542009")])
    selected_rows = [
        row
        for row in candidates
        if (row["suite_code"], row["suite_spec_code"]) in selected_keys
        and row["product_type"] == "组合商品"
        and row["dt"] == "2026-10-27"
    ]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params):
            if "(suite_code,suite_spec_code) IN" in query:
                # 本次StarRocks实测的方言边界，不能用接受任意SQL的替身掩盖1064。
                raise ProgrammingError(
                    1064,
                    "Getting syntax error at line 1, column 116. "
                    "Detail message: Unexpected input 'AND', "
                    "the most similar input is {<EOF>, ';'}.",
                )
            # COUNT/SELECT都必须把每个完整父键括起，再把整个OR集合括起，避免AND优先级放大范围。
            pairs = " OR ".join(["(suite_code=%s AND suite_spec_code=%s)"] * len(selected_keys))
            assert f"WHERE product_type=%s AND dt=%s AND ({pairs})" in query
            assert params == ("组合商品", "2026-10-27", *(v for key in selected_keys for v in key))
            assert all(value not in query for key in selected_keys for value in key)
            assert "product_name" not in query
            if "COUNT(*)" not in query:
                assert "LIMIT 5001" in query

        def fetchone(self):
            return {"row_count": len(selected_rows) if expected_count is None else expected_count}

        def fetchall(self):
            return selected_rows

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.connection = SimpleNamespace(cursor=Cursor)
    reader.retry_budget = None
    reader.heartbeat = lambda: None
    return reader


def test_bundle_reader_reads_whole_exact_parent_scope_and_checks_source_count():
    reader = bundle_query_reader(bundle_candidates())
    result = reader.read_current_bundle_categories([identity_fact()], [], "2026-10-27")
    assert len(result[("KQ24001", "06941716542009")]) == 2
    reader.connection = None
    assert (
        reader.read_current_bundle_categories([identity_fact()], [current_master()], "2026-10-27")
        == {}
    )
    for candidates, count in [(bundle_candidates()[:1], 2), ([bundle_master()] * 5001, 5001)]:
        reader = bundle_query_reader(candidates, count)
        with pytest.raises(ValueError, match="BUNDLE_CATEGORY"):
            reader.read_current_bundle_categories([identity_fact()], [], "2026-10-27")


def test_bundle_parent_or_scope_preserves_exact_pairs_and_keeps_values_parameterized():
    keys = [("A' OR 1=1 --", "共享父SKU"), ("Z父款", "共享父SKU")]
    candidates = [
        bundle_master(__id="1", suite_code=keys[0][0], suite_spec_code=keys[0][1]),
        bundle_master(__id="2", suite_code=keys[1][0], suite_spec_code=keys[1][1]),
        bundle_master(__id="3", suite_code=keys[0][0], suite_spec_code="未申请父SKU"),
        bundle_master(__id="4", suite_code="未申请父款", suite_spec_code=keys[1][1]),
        bundle_master(__id="5", suite_code=keys[1][0], suite_spec_code=keys[1][1], dt="2026-10-26"),
        bundle_master(
            __id="6", suite_code=keys[1][0], suite_spec_code=keys[1][1], product_type="普通商品"
        ),
    ]
    reader = bundle_query_reader(candidates, selected_keys=keys)
    facts = [
        identity_fact(__id=str(index), product_code=style, spec_code=sku)
        for index, (style, sku) in enumerate(keys)
    ]
    result = reader.read_current_bundle_categories(facts, [], "2026-10-27")
    assert set(result) == set(keys)
    assert {key: [row["__id"] for row in rows] for key, rows in result.items()} == {
        keys[0]: ["1"],
        keys[1]: ["2"],
    }


def test_reader_checks_bundle_evidence_changes_before_normalizing():
    from app.connectors.aftersales import AftersalesReader

    reader = AftersalesReader.__new__(AftersalesReader)
    reader.read = lambda code, end: [identity_fact()]
    reader.read_current_master = lambda raw: ("2026-10-27", [])
    reader.read_current_style_categories = lambda raw, master, snapshot: {}
    calls = 0

    def read_bundle(raw, master, snapshot):
        nonlocal calls
        calls += 1
        return {
            ("KQ24001", "06941716542009"): [
                bundle_master(second_category="其他类" if calls > 1 else "护耳帽")
            ]
        }

    reader.read_current_bundle_categories = read_bundle
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_READ"):
        reader.read_stable("jst_returns", date(2026, 10, 27))


def test_real_pg_bundle_category_archive_preserves_all_fact_fields_and_old_v3_replay(
    sales_pg, tmp_path
):
    import json
    from pathlib import Path

    import sqlalchemy as sa

    from app.modules.aftersales.ingestion import enrich_current_master, normalize
    from app.modules.aftersales.publisher import FACTS, publish_source

    end = date(2026, 10, 27)
    old_raw = enrich_current_master(
        [identity_fact()], [], "2026-10-27", {"KQ24001": full_style_master()}
    )
    old_raw[0].pop("jst_current_bundle_category_evidence")  # 旧v3归档没有v4证据字段。
    old_rows = normalize("jst_returns", old_raw, end)
    assert old_rows[0]["source_product_category"] == "童帽秋冬"
    assert old_rows[0]["source_subcategory"] == "护耳帽"
    for code, table in FACTS.items():
        raw = enrich_current_master(
            [identity_fact()],
            [],
            "2026-10-27",
            bundle_candidates={("KQ24001", "06941716542009"): bundle_candidates()},
        )
        rows = normalize(code, raw, end)

        def receive(hb, raw=raw, rows=rows):
            return end, raw, rows

        first = publish_source(sales_pg, code, receive, tmp_path)
        assert (
            publish_source(sales_pg, code, receive, tmp_path)["version_id"] == first["version_id"]
        )
        with sales_pg.connect() as c:
            db_rows = (
                c.execute(
                    sa.text(f"SELECT * FROM {table} WHERE dataset_version_id=:v"),
                    {"v": first["version_id"]},
                )
                .mappings()
                .all()
            )
            assert len(db_rows) == 1
            assert {key: db_rows[0][key] for key in rows[0]} == rows[0]
            version = (
                c.execute(
                    sa.text("SELECT * FROM dataset_versions WHERE id=:v"),
                    {"v": first["version_id"]},
                )
                .mappings()
                .one()
            )
        archived = [
            json.loads(line)
            for line in Path(version["retained_input_ref"]).read_text().splitlines()
        ]
        assert normalize(code, archived, end) == rows
        assert archived[0]["jst_current_mapping_status"] == "not_found"
        assert version["row_schema_revision"] == "m5-jst-current-bundle-consensus-v4"

        def invalid(hb):
            enrich_current_master(
                [identity_fact()],
                [],
                "2026-10-27",
                bundle_candidates={("KQ24001", "06941716542009"): bundle_candidates() * 2},
            )

        with pytest.raises(ValueError, match="BUNDLE_CATEGORY_DUPLICATE"):
            publish_source(sales_pg, code, invalid, tmp_path)
        with sales_pg.connect() as c:
            assert (
                str(
                    c.execute(
                        sa.text("SELECT version_id FROM current_datasets WHERE dataset_code=:code"),
                        {"code": code},
                    ).scalar_one()
                )
                == first["version_id"]
            )
            assert (
                c.execute(
                    sa.text(f"SELECT COUNT(*) FROM {table} WHERE dataset_version_id=:v"),
                    {"v": first["version_id"]},
                ).scalar_one()
                == 1
            )
    assert normalize("jst_returns", old_raw, end) == old_rows


def test_empty_bundle_scope_keeps_fact_unclassified_and_archive_replayable():
    from app.modules.aftersales.ingestion import enrich_current_master, normalize

    raw = enrich_current_master(
        [identity_fact()], [], "2026-10-27", bundle_candidates={("KQ24001", "06941716542009"): []}
    )
    row = normalize("jst_returns", raw, date(2026, 10, 27))[0]
    assert row["source_product_category"] is None and row["source_subcategory"] is None
    assert raw[0]["category_match_method"] == "unmatched"
    assert raw[0]["jst_current_mapping_status"] == "not_found"
    assert raw[0]["jst_current_bundle_category_evidence"]["candidate_count"] == 0
