"""Synthetic M2TS fixtures for bounded PAT/PMT audio diagnostics."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tools.bdmv_audio_check import AudioInspectionError, _crc32_mpeg, inspect, main


def psi(table, body):
    length = len(body) + 4
    header = bytes([table, 0xB0 | (length >> 8), length & 0xFF])
    value = header + body
    return value + _crc32_mpeg(value).to_bytes(4, 'big')


def pat(program_pid=0x100):
    # transport stream ID; version; sections; program 1 and the PMT PID
    body = bytes([0, 1, 0xC1, 0, 0, 0, 1, 0xE0 | (program_pid >> 8), program_pid & 255])
    return psi(0, body)


def pmt(types=(0x1B, 0x81, 0x81), *, descriptor=b''):
    body = bytes([0, 1, 0xC1, 0, 0, 0xE1, 0, 0xF0, len(descriptor)]) + descriptor
    body += b''.join(bytes([type_id, 0xE1, i + 1, 0xF0, 0])
                     for i, type_id in enumerate(types))
    return psi(2, body)


def packet(pid, section=None, *, continuity=0):
    flags = 0x40 if section is not None else 0
    header = bytes([0x47, flags | (pid >> 8), pid & 255, 0x10 | continuity])
    payload = ((b'\x00' + section) if section is not None else b'')
    ts = header + payload
    assert len(ts) <= 188
    return b'\x00' * 4 + ts + b'\xFF' * (188 - len(ts))


class BDMVAudioCheckTests(unittest.TestCase):
    def check(self, stream, expected=None):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '00000.m2ts'
            path.write_bytes(stream)
            return inspect(path, expected)

    def test_detects_two_ac3_with_pat_pmt(self):
        stream = packet(0, pat()) + packet(0x100, pmt())
        result = self.check(stream, 2)
        self.assertEqual([item.codec for item in result], ['other', 'AC3', 'AC3'])
        self.assertEqual([item.pid for item in result], [0x101, 0x102, 0x103])

    def test_fails_if_expected_number_missing(self):
        with self.assertRaisesRegex(AudioInspectionError, 'count'):
            self.check(packet(0, pat()) + packet(0x100, pmt()), 3)

    def test_missing_pmt_is_inconclusive(self):
        with self.assertRaisesRegex(AudioInspectionError, 'PAT/PMT'):
            self.check(packet(0, pat()))

    def test_corrupt_crc_rejected(self):
        section = bytearray(pmt())
        section[-1] ^= 0xFF
        with self.assertRaisesRegex(AudioInspectionError, 'CRC'):
            self.check(packet(0, pat()) + packet(0x100, bytes(section)))

    def test_section_crossing_packet_remains_inconclusive(self):
        # Deliberately long PMT descriptors: this checker must not claim success.
        with self.assertRaisesRegex(AudioInspectionError, 'PAT/PMT'):
            self.check(packet(0, pat()) + packet(0x100, pmt(descriptor=b'x' * 160)[:170]))

    def test_pmt_for_different_program_must_not_pass(self):
        pmt_section = bytearray(pmt())
        body = bytes([0, 2]) + bytes(pmt_section[5:-4])
        with self.assertRaisesRegex(AudioInspectionError, 'program number'):
            self.check(packet(0, pat()) + packet(0x100, psi(2, body)), 2)

    def test_multiple_pat_programs_same_pid_are_ambiguous(self):
        pat_body = bytes([0, 1, 0xC1, 0, 0,
                          0, 1, 0xE1, 0, 0, 2, 0xE1, 0])
        with self.assertRaisesRegex(AudioInspectionError, 'one PAT program'):
            self.check(packet(0, psi(0, pat_body)) + packet(0x100, pmt()))

    def test_rejects_invalid_psi_syntax_even_with_valid_crc(self):
        section = pat()
        bad_section = psi(0, section[3:-4])
        bad_section = bytearray(bad_section)
        bad_section[1] &= 0x7F
        bad_section[-4:] = _crc32_mpeg(bad_section[:-4]).to_bytes(4, 'big')
        with self.assertRaisesRegex(AudioInspectionError, 'syntax'):
            self.check(packet(0, bytes(bad_section)) + packet(0x100, pmt()))

    def test_rejects_invalid_m2ts_sync_before_pat(self):
        frame = bytearray(packet(0x1FFF))
        frame[4] = 0x46
        with self.assertRaisesRegex(AudioInspectionError, 'sync'):
            self.check(bytes(frame) + packet(0, pat()) + packet(0x100, pmt()))

    def test_bad_alignment_and_empty_stream(self):
        for value in (b'', packet(0, pat()) + b'x'):
            with self.subTest(length=len(value)), self.assertRaisesRegex(AudioInspectionError, '192'):
                self.check(value)

    def test_malformed_expected_number(self):
        with self.assertRaisesRegex(AudioInspectionError, '0–8'):
            self.check(packet(0, pat()), True)

    def test_inactive_pat_is_not_treated_as_current(self):
        body = bytes([0, 1, 0xC0, 0, 0, 0, 1, 0xE1, 0])
        with self.assertRaisesRegex(AudioInspectionError, 'Inactive'):
            self.check(packet(0, psi(0, body)) + packet(0x100, pmt()))

    def test_inactive_pmt_is_not_treated_as_current(self):
        section = bytearray(pmt())
        body = bytes([0, 1, 0xC0]) + section[6:-4]
        with self.assertRaisesRegex(AudioInspectionError, 'Inactive'):
            self.check(packet(0, pat()) + packet(0x100, psi(2, body)))

    def test_cli_returns_failure_for_incomplete_media(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / '00000.m2ts'
            p.write_bytes(packet(0, pat()))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main([str(p), '--expected-ac3', '1'])
            self.assertEqual(code, 2)
            self.assertIn('pmt_parsed', output.getvalue())


if __name__ == '__main__':
    unittest.main()
