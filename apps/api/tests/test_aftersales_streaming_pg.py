"""真实PG验证分批校验仍逐字段核对、历史版本不变及失败整批回滚。"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.modules.aftersales.ingestion import normalize
from app.modules.aftersales.publisher import publish_source
from app.modules.archive_payload import read_payload_text
from tests.test_aftersales_memory import synthetic_rows

pytest_plugins = ["tests.sales_pg_support"]


def test_streamed_publication_preserves_archive_history_and_rejects_corrupt_pg_rows(
    sales_pg, tmp_path
):
    raw, rows = synthetic_rows(2101)
    # 流式校验须按Python字符串顺序，比数字顺序/数据库默认locale明确。
    raw[-1]["__id"] = "é-记录"
    rows = normalize("jst_shipped_sales", raw, date(2026, 10, 27))
    first = publish_source(
        sales_pg, "jst_shipped_sales", lambda hb: (date(2026, 10, 27), raw, rows), tmp_path
    )
    with sales_pg.connect() as c:
        archive = c.scalar(
            sa.text("SELECT retained_input_ref FROM dataset_versions WHERE id=:v"),
            {"v": first["version_id"]},
        )
        assert c.scalar(sa.text("SELECT COUNT(*) FROM product_sales_records")) == 2101
    original_archive = read_payload_text(archive)
    assert len(original_archive.splitlines()) == 2101
    with sales_pg.begin() as c:
        c.exec_driver_sql(
            "CREATE FUNCTION corrupt_test_qty() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.source_row_key='12' THEN NEW.source_sales_qty=999; END IF; "
            "RETURN NEW; END $$"
        )
        c.exec_driver_sql(
            "CREATE TRIGGER corrupt_qty BEFORE INSERT ON product_sales_records "
            "FOR EACH ROW EXECUTE FUNCTION corrupt_test_qty()"
        )
    raw[0]["outstock_num"] = "2"
    changed_rows = normalize("jst_shipped_sales", raw, date(2026, 10, 27))
    with pytest.raises(ValueError, match="SOURCE_PG_CONTENT_MISMATCH"):
        publish_source(
            sales_pg,
            "jst_shipped_sales",
            lambda hb: (date(2026, 10, 27), raw, changed_rows),
            tmp_path,
        )
    with sales_pg.connect() as c:
        assert c.scalar(sa.text("SELECT COUNT(*) FROM dataset_versions")) == 1
        assert c.scalar(sa.text("SELECT COUNT(*) FROM product_sales_records")) == 2101
        assert c.scalar(sa.text("SELECT version_id FROM current_datasets")) == first["version_id"]
        assert c.scalar(
            sa.text("SELECT SUM(source_sales_qty) FROM product_sales_records")
        ) == Decimal(2101)
        assert (
            c.scalar(sa.text("SELECT status FROM task_runs ORDER BY queued_at DESC LIMIT 1"))
            == "failed"
        )
    assert read_payload_text(archive) == original_archive
