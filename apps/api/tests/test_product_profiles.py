"""资料解析必须保持字段共识、主键关联及发布原子性。"""

import importlib
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_m1_ingestion import pg, repo, run, sample  # noqa: F401


def module():
    assert importlib.util.find_spec("app.modules.products.profiles"), "资料解析尚未实现"
    return importlib.import_module("app.modules.products.profiles")


def test_strict_lists_do_not_guess_ids_from_text_or_commas():
    parse = module().parse_ids
    assert parse(" 1001 / 1002\n1001；1003;1004 ") == ["1001", "1002", "1003", "1004"]
    for value in (None, "", "下架1001", "1001,1002", "1001/铺货1002", 1001, "１２３"):
        assert parse(value) == []


def test_field_consensus_does_not_choose_largest_primary_key():
    resolve = module().resolve_fields
    result = resolve(
        [
            dict(
                source_profile_id="9",
                product_name="甲",
                category="帽子",
                level="新品",
                operator="",
                source_listed_date="2026-10-01",
            ),
            dict(
                source_profile_id="10",
                product_name="乙",
                category="帽子",
                level="A级",
                operator="运营一",
                source_listed_date="非法日期",
            ),
        ]
    )
    assert result["category"] == "帽子" and result["operator"] == "运营一"
    assert result["product_name"] is None and result["level"] is None
    assert result["listed_date"] is None
    assert result["field_statuses"]["level"] == "conflict"
    assert result["field_statuses"]["listed_date"] == "invalid"
    assert result["field_candidates"]["level"] == ["A级", "新品"]
    assert resolve_fields_date(resolve) == date(2026, 10, 1)


def resolve_fields_date(resolve):
    return resolve([dict(source_profile_id="1", source_listed_date="2026-10-01 00:00:00")])[
        "listed_date"
    ]


def test_publish_strict_links_profiles_and_idempotent_version(repo):  # noqa: F811
    repository, engine = repo
    run(repository)
    resolver = module().ProfileRepository(engine)
    first = resolver.run()
    second = resolver.run()
    assert first["publication_id"] == second["publication_id"]
    assert second["changed"] is False
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM product_profile_links")).scalar_one() == 4
        assert c.execute(text("SELECT count(*) FROM product_profiles")).scalar_one() == 3
        row = c.execute(
            text("SELECT category,level,operator FROM product_profiles WHERE shop_id='663599807'")
        ).one()
        assert tuple(row) == ("帽子", None, None)
        assert c.execute(text("SELECT count(*) FROM product_days")).scalar_one() == 1
        assert (
            c.execute(
                text("SELECT count(*) FROM publication_datasets WHERE publication_id=:p"),
                {"p": first["publication_id"]},
            ).scalar_one()
            == 5
        )


def test_resolution_failure_rolls_back_and_keeps_previous_head(repo):  # noqa: F811
    repository, engine = repo
    old = run(repository)["publication_id"]
    resolver = module().ProfileRepository(engine)
    with engine.begin() as c:
        c.execute(
            text(
                "CREATE FUNCTION reject_profile_test() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END; $$; "
                "CREATE TRIGGER reject_profile_test BEFORE INSERT ON product_profiles "
                "FOR EACH ROW EXECUTE FUNCTION reject_profile_test();"
            )
        )
    try:
        with pytest.raises(DBAPIError):
            resolver.run()
        with engine.connect() as c:
            assert (
                str(c.execute(text("SELECT publication_id FROM publication_heads")).scalar_one())
                == old
            )
            assert c.execute(text("SELECT count(*) FROM product_profiles")).scalar_one() == 0
            assert c.execute(text("SELECT count(*) FROM product_profile_links")).scalar_one() == 4
    finally:
        with engine.begin() as c:
            c.execute(
                text(
                    "DROP TRIGGER reject_profile_test ON product_profiles; "
                    "DROP FUNCTION reject_profile_test();"
                )
            )


def test_raw_revision_invalidates_resolution_until_republished(repo):  # noqa: F811
    repository, engine = repo
    run(repository)
    first = module().ProfileRepository(engine).run()
    data = sample()
    data["product_profile_source"][0]["pl"] = "围巾"
    changed = run(repository, data)
    assert changed["publication_id"] != first["publication_id"]
    last = module().ProfileRepository(engine).run()
    with engine.connect() as c:
        row = c.execute(
            text("SELECT category,field_statuses FROM product_profiles WHERE shop_id='663599807'")
        ).one()
        assert row[0] is None and row[1]["category"] == "conflict"
    assert last["publication_id"] != changed["publication_id"]
