"""离线渲染v2基线审阅SQL；不读取.env、不构造engine、不访问数据库。"""

import argparse
import hashlib
import importlib.util
import io
import sys
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/api"
sys.path.insert(0, str(API))
REVISION = API / "migrations_v2/versions/20261028_01_initial_30_tables.py"
OUTPUT = ROOT / "docs/database-schema/schema.sql"


def render_sql() -> str:
    """只返回确定性DDL文本；摘要固定迁移及全部冻结实现，不包含凭据。"""
    files = [REVISION, *sorted((API / "migrations_v2/baseline_20261028").glob("*.py"))]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(API)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    spec = importlib.util.spec_from_file_location("initial_30_tables", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    output.write(
        "-- 30表494字段离线审阅稿；实际执行状态见docs/validation.md。\n"
        "-- 权威：apps/api/migrations_v2/versions/20261028_01_initial_30_tables.py\n"
        "-- schema.sql由scripts/render-db-schema.py派生，请勿手改或单独执行。\n"
        f"-- 冻结迁移及实现SHA-256：{digest.hexdigest()}\n"
        "-- 不含Alembic技术版本表；实际执行只通过独立v2迁移链管理。\n\n"
    )
    context = MigrationContext.configure(
        dialect_name="postgresql",
        dialect_opts={"paramstyle": "named"},
        opts={
            "as_sql": True,
            "literal_binds": True,
            "transactional_ddl": True,
            "output_buffer": output,
        },
    )
    with Operations.context(context), context.begin_transaction():
        module.upgrade()
    return "\n".join(line.rstrip() for line in output.getvalue().splitlines()).rstrip() + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="只检查派生SQL是否与迁移一致"
    )
    args = parser.parse_args()
    sql = render_sql()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != sql:
            raise SystemExit("schema.sql缺失或与冻结迁移不同，请离线重新生成")
        print("schema.sql与冻结迁移一致；未连接或操作数据库")
    else:
        OUTPUT.write_text(sql, encoding="utf-8")
        print(f"已离线生成 {OUTPUT.relative_to(ROOT)}；未连接或操作数据库")
