"""Synthetic negative tests for reserved/colliding MPEG-TS PAT/PMT PIDs.

These fixtures check metadata validation only, not authored media playback.
"""
import tempfile
import unittest
from pathlib import Path

from tools.bdmv_audio_check import AudioInspectionError, _crc32_mpeg, inspect


def section(kind: int, body: bytes) -> bytes:
    length = len(body) + 4
    header = bytes((kind, 0xB0 | (length >> 8), length & 0xFF))
    data = header + body
    return data + _crc32_mpeg(data).to_bytes(4, "big")


def pid_bytes(pid: int) -> bytes:
    return bytes((0xE0 | (pid >> 8), pid & 0xFF))


def pat(entries=((1, 0x100),)) -> bytes:
    body = bytes.fromhex("00 01 C1 00 00")
    for program, pid in entries:
        body += program.to_bytes(2, "big") + pid_bytes(pid)
    return section(0, body)


def pmt(stream_pids=(0x101, 0x1100), pcr_pid=0x101) -> bytes:
    body = bytes.fromhex("00 01 C1 00 00") + pid_bytes(pcr_pid) + bytes.fromhex("F0 00")
    for kind, pid in zip((0x1B, 0x81), stream_pids):
        body += bytes((kind,)) + pid_bytes(pid) + bytes.fromhex("F0 00")
    return section(2, body)


def packet(pid: int, payload: bytes) -> bytes:
    transport = bytes((0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10)) + b"\x00" + payload
    assert len(transport) <= 188
    return b"\x00" * 4 + transport.ljust(188, b"\xff")


class PidValidationTests(unittest.TestCase):
    def check(self, pat_bytes: bytes, pmt_bytes: bytes, pmt_pid=0x100):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "00000.m2ts"
            path.write_bytes(packet(0, pat_bytes) + packet(pmt_pid, pmt_bytes))
            return inspect(path, expected_ac3=1)

    def test_normal_pat_pmt_with_audio_passes(self):
        streams = self.check(pat(), pmt())
        self.assertEqual([s.pid for s in streams], [0x101, 0x1100])

    def test_pat_rejects_reserved_and_null_pmt_pids(self):
        for pid in (0x0000, 0x0001, 0x0002, 0x000F, 0x1FFF):
            with self.subTest(pid=pid):
                with self.assertRaisesRegex(AudioInspectionError, "Invalid PMT PID"):
                    self.check(pat(((1, pid),)), pmt())

    def test_pat_rejects_identical_duplicate_programs(self):
        with self.assertRaisesRegex(AudioInspectionError, "Duplicate PAT program"):
            self.check(pat(((1, 0x100), (1, 0x100))), pmt())

    def test_pat_rejects_conflicting_duplicate_programs(self):
        with self.assertRaisesRegex(AudioInspectionError, "Duplicate PAT program"):
            self.check(pat(((1, 0x100), (1, 0x200))), pmt())

    def test_pmt_rejects_reserved_and_null_audio_pids(self):
        for pid in (0x0000, 0x0001, 0x000F, 0x1FFF):
            with self.subTest(pid=pid):
                with self.assertRaisesRegex(AudioInspectionError, "elementary stream PID"):
                    self.check(pat(), pmt(stream_pids=(0x101, pid)))

    def test_pmt_rejects_elementary_pid_equal_to_pmt_pid(self):
        with self.assertRaisesRegex(AudioInspectionError, "elementary stream PID"):
            self.check(pat(), pmt(stream_pids=(0x101, 0x100)))

    def test_pmt_rejects_reserved_pcr_pid(self):
        for pid in (0x0000, 0x0001, 0x000F):
            with self.subTest(pid=pid):
                with self.assertRaisesRegex(AudioInspectionError, "PCR PID"):
                    self.check(pat(), pmt(pcr_pid=pid))

    def test_pmt_allows_null_pcr_pid_for_metadata_only(self):
        # MPEG-TS permits PCR_PID 0x1FFF to signal no PCR in this program.
        streams = self.check(pat(), pmt(pcr_pid=0x1FFF))
        self.assertEqual(sum(s.codec == "AC3" for s in streams), 1)


if __name__ == "__main__":
    unittest.main()
