"""Synthetic multi-packet PAT/PMT fixtures; not actual tsMuxer media."""
import tempfile
import unittest
from pathlib import Path
from tools.bdmv_audio_check import AudioInspectionError, _crc32_mpeg, inspect


def psi(kind, body):
    length = len(body) + 4
    head = bytes((kind, 0xB0 | (length >> 8), length & 255)) + body
    return head + _crc32_mpeg(head).to_bytes(4, 'big')


def transport(pid, data, start=False, cc=0):
    payload = (b'\x00' if start else b'') + data
    assert len(payload) <= 184
    head = bytes((0x47, (0x40 if start else 0) | (pid >> 8), pid & 255, 0x10 | cc))
    return b'\x00' * 4 + head + payload.ljust(184, b'\xff')


def pat():
    return psi(0, bytes.fromhex('00 01 C1 00 00 00 01 E1 00'))


def pmt():
    desc = b'\x80\x00' * 170
    body = bytes.fromhex('00 01 C1 00 00 E1 01') + bytes((0xF1, 0x54))
    body += desc + bytes.fromhex('1B E1 01 F0 00 81 E1 02 F0 00 81 E1 03 F0 00')
    return psi(2, body)


def fragments(section):
    chunks = [section[:183]]
    chunks += [section[i:i + 184] for i in range(183, len(section), 184)]
    return [transport(0x100, chunk, i == 0, i) for i, chunk in enumerate(chunks)]


class ReassemblyTests(unittest.TestCase):
    def check(self, parts, expected=2):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / '00000.m2ts'
            file.write_bytes(transport(0, pat(), True) + b''.join(parts))
            return inspect(file, expected)

    def test_spanning_pmt_has_two_ac3_tracks(self):
        chunks = fragments(pmt())
        self.assertEqual(len(chunks), 3)
        self.assertEqual(sum(s.codec == 'AC3' for s in self.check(chunks)), 2)

    def test_incomplete_section_remains_inconclusive(self):
        with self.assertRaisesRegex(AudioInspectionError, 'PAT/PMT'):
            self.check(fragments(pmt())[:-1])

    def test_continuity_gap_rejected(self):
        chunks = fragments(pmt())
        broken = bytearray(chunks[1])
        broken[7] = (broken[7] & 0xF0) | 7
        chunks[1] = bytes(broken)
        with self.assertRaisesRegex(AudioInspectionError, 'continuity'):
            self.check(chunks)

    def test_crc_mismatch_in_last_packet(self):
        section = bytearray(pmt())
        section[-1] ^= 1
        with self.assertRaisesRegex(AudioInspectionError, 'CRC'):
            self.check(fragments(bytes(section)))

    def test_unfinished_pmt_cannot_be_silently_restarted(self):
        with self.assertRaisesRegex(AudioInspectionError, 'interrupted'):
            self.check([fragments(pmt())[0], transport(0x100, pmt()[:10], True, 1)])

    def test_invalid_pointer_and_adaptation(self):
        cases = [
            (bytes.fromhex('47 41 00 10') + b'\xb8' + b'\xff' * 183, 'pointer'),
            (bytes.fromhex('47 41 00 30') + b'\xff' + b'\x00' * 183, 'adaptation'),
        ]
        for ts, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(AudioInspectionError, error):
                self.check([b'\x00' * 4 + ts])
