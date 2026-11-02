"""商品目录只读所选期间身份，保持完整候选、最新原名和已选回显。"""

from datetime import date, timedelta

from app.modules.product_board.query import BoardQuery, catalog, options


def test_options_uses_period_identity_rows_without_loading_operating_facts():
    end = date(2026, 10, 29)

    class Repo:
        start = end - timedelta(days=59)

        def rows(self, start, stop):
            raise AssertionError("目录不应读取全量经营指标行")

        def identity_rows(self, start, stop):
            assert (start, stop) == (end - timedelta(days=1), end)
            return [
                dict(
                    listing_id="x",
                    business_date=day,
                    source_shop_id="s",
                    source_shop_name="店",
                    source_product_id="p",
                    source_product_name=name,
                )
                for day, name in ((start, "旧名"), (stop, "期间新名"))
            ]

        def profiles(self):
            return {}

    repo = Repo()
    repo.end = end
    result = options(repo, BoardQuery(end - timedelta(days=1), end), selected_ids=("x",))
    assert result == dict(
        selection=dict(operator="", category="", level="", products=["x"]),
        selected_products=[dict(value="x", label="期间新名 · 店 · p")],
        shops=[dict(value="s", label="店")],
        operators=[],
        categories=[],
        levels=[],
        products=[dict(value="x", label="期间新名 · 店 · p")],
        total=1,
    )
    directory = catalog(repo, BoardQuery(end - timedelta(days=1), end))
    assert directory["total"] == 1
    assert directory["items"][0]["label"] == "期间新名 · 店 · p"
    assert directory["items"][0]["search_text"] == "p 期间新名"
