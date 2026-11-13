"""数据库保留本地工具：默认只读计划；写入限定本机项目管理员和随机测试库。"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

import sqlalchemy as sa
from app.modules import database_archive as archive
from app.modules import database_retention as retention
from app.modules.archive_store import _safe
from migrations_v2.local import local_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="plan",
        choices=("plan", "export", "verify", "drill", "apply", "restore"),
    )
    parser.add_argument("--database", default="pdd_operations")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--version", help="export时明确选择清单中的一个版本")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    engine = sa.create_engine(local_url(args.database), hide_parameters=True)
    try:
        if args.action == "plan":
            print(json.dumps(retention.plan(engine), ensure_ascii=False, indent=2))
            return
        if not args.bundle or not args.key_file:
            parser.error("非plan操作要求--bundle和--key-file")
        bundle = _safe(args.bundle.absolute())
        base = _safe(ROOT / "var/production-db-archives")
        if not bundle.is_relative_to(base) or bundle == base:
            raise ValueError("ARCHIVE_PATH_OUTSIDE_PROJECT")
        key_path = _safe(args.key_file)
        info = key_path.stat()
        if (
            info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not stat.S_ISREG(info.st_mode)
            or key_path.is_relative_to(bundle)
            or info.st_size != 32
        ):
            raise ValueError("ARCHIVE_KEY_FILE")
        key = key_path.read_bytes()
        if args.action == "verify":
            archive.verify(bundle, key)
            result = {"verified": True}
        elif args.action == "drill":
            result = archive.drill(bundle, key)
        elif args.action == "restore":
            result = retention.restore(engine, bundle, key)
        else:
            if not args.plan:
                parser.error("export/apply要求显式--plan清单")
            selected = json.loads(_safe(args.plan).read_text())
            if args.action == "export":
                result = {
                    "bundle": str(
                        archive.export(
                            engine, selected, bundle, key, version_id=args.version
                        )
                    )
                }
            else:
                result = retention.apply(engine, selected, bundle, key)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — 入口统一脱敏，不回显数据库语句
        # 不输出DBAPI含SQL/参数异常及归档明文。
        print(
            "维护未完成："
            + (str(exc) if type(exc) is ValueError else type(exc).__name__),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
