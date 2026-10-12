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


    def test_backup_regular_file_is_error_not_missing_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp), backups=False)
            (bdmv / "BACKUP").write_bytes(b"not a directory")
            errors, warnings = self.inspect(bdmv)
            self.assertIn("Invalid BDMV/BACKUP directory", errors)
            self.assertEqual(warnings, [])

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


    def test_orphaned_backup_playlist_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "BACKUP/PLAYLIST/99999.mpls").write_bytes(b"stale playlist")
            errors, warnings = self.inspect(bdmv)
            self.assertIn("Orphaned BDMV/BACKUP/PLAYLIST/99999.mpls", errors)
            self.assertEqual(warnings, [])

    def test_orphaned_backup_clipinfo_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "BACKUP/CLIPINF/99999.clpi").write_bytes(b"stale clip")
            errors, _ = self.inspect(bdmv)
            self.assertIn("Orphaned BDMV/BACKUP/CLIPINF/99999.clpi", errors)

    def test_invalid_backup_collection_directory_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            duplicate = bdmv / "BACKUP/CLIPINF"
            for child in duplicate.iterdir():
                child.unlink()
            duplicate.rmdir()
            duplicate.write_text("not a directory", encoding="utf-8")
            errors, _ = self.inspect(bdmv)
            self.assertIn("Invalid BDMV/BACKUP/CLIPINF directory", errors)

    def test_unrelated_backup_asset_is_not_assumed_to_be_navigation(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "BACKUP/PLAYLIST/.DS_Store").write_bytes(b"not an MPLS")
            errors, warnings = self.inspect(bdmv)
            self.assertEqual((errors, warnings), ([], []))

    def test_symlinked_empty_backup_collection_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            backup_dir = bdmv / "BACKUP/PLAYLIST"
            for file in backup_dir.iterdir():
                file.unlink()
            backup_dir.rmdir()
            try:
                backup_dir.symlink_to(bdmv / "PLAYLIST", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not available")
            errors, _ = check_backup_metadata(bdmv, {"PLAYLIST": set(), "CLIPINF": {"00000"}})
            self.assertIn("Symlink forbidden: BDMV/BACKUP/PLAYLIST", errors)

    def test_unrelated_stream_bytes_are_never_read(self):
        with tempfile.TemporaryDirectory() as temp:
            bdmv = self.make_files(Path(temp))
            (bdmv / "STREAM").mkdir()
            (bdmv / "STREAM/00000.m2ts").write_bytes(b"bad stream")
            errors, warnings = self.inspect(bdmv)
            self.assertEqual((errors, warnings), ([], []))


if __name__ == "__main__":
    unittest.main()
