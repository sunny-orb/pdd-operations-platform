"""真实PG：上线登记、发布事件与离线保护，不连接生产。"""

import pytest
import sqlalchemy as sa

from tests.test_published_versions import publish_pointer

pytest_plugins = ["tests.sales_pg_support"]


def test_registration_and_a_b_a_publication_history(sales_pg):
    from app.modules.database_residency import ensure_online

    publish_pointer(sales_pg, "product_daily", "a")
    publish_pointer(sales_pg, "product_daily", "b")
    with sales_pg.begin() as c:
        c.execute(
            sa.text(
                "UPDATE current_datasets SET version_id='product_daily-a' "
                "WHERE dataset_code='product_daily'"
            )
        )
        ensure_online(c, ["product_daily-a", "product_daily-b"])
        events = c.execute(
            sa.text(
                "SELECT version_id,previous_version_id FROM dataset_publications ORDER BY revision"
            )
        ).all()
        assert events == [
            ("product_daily-a", None),
            ("product_daily-b", "product_daily-a"),
            ("product_daily-a", "product_daily-b"),
        ]
        with pytest.raises(ValueError, match="DATASET_RESTORE_REQUIRED"):
            ensure_online(c, ["missing"])


def test_normal_clients_cannot_forge_offline_state(sales_pg):
    publish_pointer(sales_pg, "product_daily")
    with pytest.raises(sa.exc.DBAPIError):
        with sales_pg.begin() as c:
            c.execute(sa.text("UPDATE dataset_storage SET state='offline'"))
