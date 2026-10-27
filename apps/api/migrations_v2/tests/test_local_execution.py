"""受控本地入口的边界测试；不读取凭据、不连接数据库。"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url  # noqa: E402


class LocalExecutionTests(unittest.TestCase):
    def runner(self):
        spec = importlib.util.spec_from_file_location("v2_runner_test", ROOT / "scripts/db-v2.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_verify_refuses_evidence_if_files_change_during_tests(self):
        runner = self.runner()
        with (
            tempfile.TemporaryDirectory(dir=ROOT / "var") as directory,
            patch.object(runner, "PRIVATE", Path(directory)),
            patch.object(runner, "EVIDENCE", Path(directory) / "verified.json"),
            patch.object(runner.sa, "create_engine", return_value=MagicMock()),
            patch.object(runner, "snapshot", return_value={"synthetic": "unchanged"}),
            patch.object(runner, "fingerprint", side_effect=["before", "after"]),
            patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "文件.*变化"):
                runner.verify()
            self.assertFalse(runner.EVIDENCE.exists())

    def test_backup_rejects_other_cluster_before_pg_dump(self):
        runner = self.runner()
        self.assertTrue(hasattr(runner, "schema_backup"), "备份必须验证本地Docker实际集群身份")
        with patch.object(
            runner.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"pdd_operations|other-cluster\n"
            ),
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "集群"):
                runner.schema_backup("expected-cluster")
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn("--host", command)
            self.assertTrue(command[command.index("--host") + 1].startswith("unix://"))
            self.assertNotIn("pg_dump", command)

    def test_unapproved_database_is_rejected_before_reading_credentials(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("不得读取凭据")):
            for name in (
                "production",
                "pdd_operations_test",
                "postgres; DROP DATABASE x",
                "pdd_operations_v2_test_wrong",
                "pdd_operations_v2_test_123456789abc_extra",
            ):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "授权"):
                    local_url(name)

    def test_local_url_never_reads_general_environment_or_rounds_identifiers(self):
        config = "POSTGRES_PORT=55432\nPOSTGRES_PASSWORD=synthetic-secret\n"
        with patch.object(Path, "read_text", return_value=config):
            url = local_url("pdd_operations_v2_test_000123456789")
        self.assertEqual(url.host, "127.0.0.1")
        self.assertEqual(url.database, "pdd_operations_v2_test_000123456789")
        self.assertEqual(url.port, 55432)
        self.assertEqual(url.username, "pdd_admin")
        self.assertNotIn("synthetic-secret", str(url))

    def test_changed_port_is_rejected(self):
        with patch.object(
            Path, "read_text", return_value="POSTGRES_PORT=5432\nPOSTGRES_PASSWORD=unused\n"
        ):
            with self.assertRaisesRegex(RuntimeError, "55432"):
                local_url("pdd_operations")
