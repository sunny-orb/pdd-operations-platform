"""流式备份摘要使用真实随机PG，验证跨批、物理顺序、修改/删除与事务快照。"""

import importlib.util
from pathlib import Path

import pytest

pytest_plugins = ["tests.sales_pg_support"]

p = Path(__file__).resolve().parents[3] / "scripts/backup-table-digests.py"
spec = importlib.util.spec_from_file_location("backup_digests", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_stream_digest_is_stable_across_batches_and_detects_content_changes(sales_pg):
    raw = sales_pg.raw_connection()
    try:
        c = raw.driver_connection
        c.execute(
            "CREATE TABLE digest_probe(tenant text, seq integer, payload jsonb, "
            "PRIMARY KEY(tenant,seq))"
        )
        c.execute(
            "INSERT INTO digest_probe SELECT '店',i,jsonb_build_object('value',i) "
            "FROM generate_series(1,101) i ORDER BY i DESC"
        )
        before = module.table_digest(c, "digest_probe", batch_size=7)
        assert before["rows"] == 101
        assert before == module.table_digest(c, "digest_probe", batch_size=100)
        c.execute("UPDATE digest_probe SET payload='{}' WHERE seq=50")
        changed = module.table_digest(c, "digest_probe", batch_size=19)
        assert changed["rows"] == 101 and changed["sha256"] != before["sha256"]
        c.execute("DELETE FROM digest_probe WHERE seq=101")
        assert module.table_digest(c, "digest_probe")["rows"] == 100
        c.rollback()
    finally:
        raw.close()


def test_empty_table_and_missing_primary_key_are_explicit(sales_pg):
    raw = sales_pg.raw_connection()
    try:
        c = raw.driver_connection
        c.execute("CREATE TABLE digest_empty(id text PRIMARY KEY)")
        assert module.table_digest(c, "digest_empty") == {
            "rows": 0,
            "sha256": __import__("hashlib").sha256().hexdigest(),
        }
        c.execute("CREATE TABLE digest_no_key(id text)")
        with pytest.raises(ValueError, match="BACKUP_PRIMARY_KEY_REQUIRED"):
            module.table_digest(c, "digest_no_key")
        c.rollback()
    finally:
        raw.close()
