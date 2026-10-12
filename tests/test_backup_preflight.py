"""Synthetic navigation-backup checks; no claim of playable media."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backup_preflight import check_backup_metadata


class BackupPreflightTests(unittest.TestCase):
    def make_files(self, root: Path, *, backups: bool = True) -> Path:
        bdmv = root / "BDMV"
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir()
        files = {
            "index.bdmv": b"INDX0200",
            "MovieObject.bdmv": b"MOBJ0200",
            "PLAYLIST/00000.mpls": b"MPLS0200\x00\x00",
            "CLIPINF/00000.clpi": b"HDMV0200\x00\x00",
        }
        for name, content in files.items():
            (bdmv / name).write_bytes(content)
        if backups:
            for name, content in files.items():
                dst = bdmv / "BACKUP" / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(content)
        return bdmv

    def inspect(self, bdmv):
        return check_backup_metadata(bdmv, {
            "PLAYLIST": {"00000"}, "CLIPINF": {"00000"},
        })

    def test_matching_navigation_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, warnings = self.inspect(self.make_files(Path(temp)))
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

    def test_same_size_mutated_backup_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "BACKUP/PLAYLIST/00000.mpls").write_bytes(b"MPLS0200\x01\x00")
            errors, warnings = self.inspect(bdmv)
            self.assertEqual(warnings, [])
            self.assertIn("metadata differs", " ".join(errors))

    def test_truncated_backup_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "BACKUP/index.bdmv").write_bytes(b"INDX")
            errors, _ = self.inspect(bdmv)
            self.assertIn("index.bdmv", " ".join(errors))

    def test_missing_backups_warn_not_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp), backups=False)
            self.assertEqual(self.inspect(bdmv)[0], [])
            self.assertIn("Missing BDMV/BACKUP", " ".join(self.inspect(bdmv)[1]))

    def test_missing_single_backup_warns(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "BACKUP/CLIPINF/00000.clpi").unlink()
            errors, warnings = self.inspect(bdmv)
            self.assertEqual(errors, [])
            self.assertTrue(any("00000.clpi" in w for w in warnings))

    def test_symlinked_backup_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            source = bdmv / "BACKUP/PLAYLIST"
            for child in source.iterdir():
                child.unlink()
            source.rmdir()
            try:
                source.symlink_to(bdmv / "PLAYLIST", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not available")
            errors, _ = self.inspect(bdmv)
            self.assertIn("Symlink forbidden", " ".join(errors))

    def test_unrelated_stream_bytes_are_never_read(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "STREAM").mkdir()
            (bdmv / "STREAM/00000.m2ts").write_bytes(b"bad stream")
            errors, warnings = self.inspect(bdmv)
            self.assertEqual((errors, warnings), ([], []))


if __name__ == "__main__":
    unittest.main()
