from datetime import datetime

import pytest

from app.connectors.product_board import ProductBoardReader


@pytest.mark.parametrize("product_day,ad_day", [(27, 26), (26, 27)])
def test_latest_product_day_is_not_capped_by_lagging_ads(product_day, ad_day):
    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, parameters):
            self.connection.table = sql

        def fetchone(self):
            return dict(
                latest=datetime(
                    2026, 10, product_day if "product_detail_f" in self.connection.table else ad_day
                )
            )

    class Connection:
        def cursor(self):
            return Cursor(self)

    reader = ProductBoardReader.__new__(ProductBoardReader)
    reader.connection, reader.heartbeat = Connection(), lambda: None
    assert reader.latest().isoformat() == "2026-10-27"
