"""End-to-end BDMV publish-gate tests on synthetic navigation metadata."""
import tempfile
import unittest
from pathlib import Path

from bdmv_preflight import inspect
from test_bdmv_preflight import fixture


class BackupGateTests(unittest.TestCase):
    def test_bad_present_backup_blocks_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            primary = root / "BDMV/index.bdmv"
            backup = root / "BDMV/BACKUP/index.bdmv"
            backup.write_bytes(primary.read_bytes()[:-1] + b"9")
            report = inspect(root)
            self.assertFalse(report.structurally_valid)
            self.assertIn("metadata differs", " ".join(report.errors))

    def test_absent_backup_files_do_not_turn_into_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            report = inspect(root)
            self.assertTrue(report.structurally_valid, report.errors)
            self.assertIn("Missing BDMV/BACKUP/index.bdmv", report.warnings)


if __name__ == "__main__":
    unittest.main()
