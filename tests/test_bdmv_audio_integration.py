"""Integration checks for optional AC-3 PAT/PMT BDMV gate."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bdmv_preflight import inspect, main
from tools.bdmv_audio_check import _crc32_mpeg


def psi(table_id, body):
    size = len(body) + 4
    section = bytes((table_id, 0xB0 | (size >> 8), size & 255)) + body
    return section + _crc32_mpeg(section).to_bytes(4, "big")


def packet(pid, section):
    ts = bytes((0x47, 0x40 | (pid >> 8), pid & 255, 0x10, 0)) + section
    if len(ts) > 188:
        raise AssertionError("Synthetic PSI must fit one TS packet")
    return b"\x00" * 4 + ts.ljust(188, b"\xff")


def authored_stream(has_ac3=True):
    pat = psi(0, bytes.fromhex("00 01 C1 00 00 00 01 E1 00"))
    elementary = bytes.fromhex("1B E1 01 F0 00")
    if has_ac3:
        elementary += bytes.fromhex("81 E1 02 F0 00")
    pmt = psi(2, bytes.fromhex("00 01 C1 00 00 E1 01 F0 00") + elementary)
    return packet(0, pat) + packet(0x100, pmt)


def make_bdmv(root, stream):
    bdmv = root / "BDMV"
    for dirname in ("PLAYLIST", "CLIPINF", "STREAM", "BACKUP"):
        (bdmv / dirname).mkdir(parents=True)
    (root / "CERTIFICATE").mkdir()
    for name, header in (("index.bdmv", b"INDX0200"),
                         ("MovieObject.bdmv", b"MOBJ0200"),
                         ("PLAYLIST/00000.mpls", b"MPLS0200"),
                         ("CLIPINF/00000.clpi", b"HDMV0200")):
        (bdmv / name).write_bytes(header)
    (bdmv / "STREAM/00000.m2ts").write_bytes(stream)


class BDMVAudioGateTests(unittest.TestCase):
    def test_expected_one_ac3_passes_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stream = authored_stream()
            make_bdmv(root, stream)
            result = inspect(root, "BD25", expected_ac3=1)
            self.assertTrue(result.structurally_valid, result.errors)
            self.assertEqual((root / "BDMV/STREAM/00000.m2ts").read_bytes(), stream)

    def test_mismatch_fails_closed_but_default_remains_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bdmv(root, authored_stream())
            self.assertTrue(inspect(root).structurally_valid)
            result = inspect(root, expected_ac3=2)
            self.assertFalse(result.structurally_valid)
            self.assertIn("PAT/PMT", " ".join(result.errors))

    def test_silent_m2ts_accepts_zero_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bdmv(root, authored_stream(False))
            self.assertTrue(inspect(root, expected_ac3=0).structurally_valid)
            self.assertFalse(inspect(root, expected_ac3=1).structurally_valid)

    def test_missing_pat_is_inconclusive_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bdmv(root, packet(0x100, psi(2, bytes.fromhex(
                "00 01 C1 00 00 E1 01 F0 00 81 E1 02 F0 00"))))
            result = inspect(root, expected_ac3=1)
            self.assertFalse(result.structurally_valid)
            self.assertIn("PAT/PMT", " ".join(result.errors))

    def test_invalid_count_fails_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bdmv(root, authored_stream())
            for value in (-1, 9, True, "1"):
                with self.subTest(value=value):
                    result = inspect(root, expected_ac3=value)
                    self.assertFalse(result.structurally_valid)
                    self.assertIn("count", " ".join(result.errors))

    def test_cli_count_reflected_in_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_bdmv(root, authored_stream())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = main([str(root), "--expected-ac3", "2"])
            self.assertEqual(status, 2)
            self.assertFalse(json.loads(output.getvalue())["structurally_valid"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(root), "--expected-ac3", "1"]), 0)


if __name__ == "__main__":
    unittest.main()
