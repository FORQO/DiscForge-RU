"""Bounded M2TS header preflight regressions (not a Blu-ray playback test)."""
import tempfile
import unittest
from pathlib import Path

from bdmv_preflight import inspect
from test_bdmv_preflight import fixture


def packet(*, tei=False, control=1, adaptation_length=0, scrambling=0):
    ts = bytearray(188)
    ts[0:4] = bytes((0x47, 0x9f if tei else 0x1f, 0xff,
                      (control << 4) | (scrambling << 6)))
    ts[4] = adaptation_length
    return bytes(4) + bytes(ts)


class M2TSHeaderTests(unittest.TestCase):
    def check(self, stream: bytes):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            (root / "BDMV/STREAM/00000.m2ts").write_bytes(stream)
            return inspect(root)

    def test_valid_ts_header(self):
        result = self.check(packet() * 40)
        self.assertTrue(result.structurally_valid, result.errors)

    def test_transport_error_flag_is_rejected(self):
        result = self.check(packet() * 10 + packet(tei=True) + packet() * 29)
        self.assertFalse(result.structurally_valid)
        self.assertIn("transport_error_indicator", " ".join(result.errors))

    def test_reserved_adaptation_control_is_rejected(self):
        result = self.check(packet(control=0))
        self.assertFalse(result.structurally_valid)
        self.assertIn("adaptation_field_control", " ".join(result.errors))

    def test_adaptation_only_length_limit(self):
        self.assertTrue(self.check(packet(control=2, adaptation_length=183)).structurally_valid)
        result = self.check(packet(control=2, adaptation_length=184))
        self.assertFalse(result.structurally_valid)
        self.assertIn("adaptation_field_length", " ".join(result.errors))

    def test_adaptation_only_must_fill_remaining_packet(self):
        # AFC=2 leaves no payload; exactly 183 adaptation bytes are required.
        for length in (0, 1, 182, 184):
            with self.subTest(length=length):
                result = self.check(packet(control=2, adaptation_length=length))
                self.assertFalse(result.structurally_valid)
                self.assertIn("adaptation_field_length", " ".join(result.errors))

    def test_clear_authored_stream_rejects_scrambled_headers(self):
        for scrambling in (1, 2, 3):
            with self.subTest(scrambling=scrambling):
                result = self.check(packet(scrambling=scrambling))
                self.assertFalse(result.structurally_valid)
                self.assertIn("transport_scrambling_control", " ".join(result.errors))

    def test_clear_adaptation_only_packet_supported(self):
        result = self.check(packet(control=2, adaptation_length=183))
        self.assertTrue(result.structurally_valid, result.errors)

    def test_scrambled_packet_near_end_rejected(self):
        result = self.check(packet() * 39 + packet(scrambling=2))
        self.assertFalse(result.structurally_valid)
        self.assertIn("packet 39", " ".join(result.errors))

    def test_adaptation_with_payload_length_limit(self):
        self.assertTrue(self.check(packet(control=3, adaptation_length=182)).structurally_valid)
        result = self.check(packet(control=3, adaptation_length=183))
        self.assertFalse(result.structurally_valid)
        self.assertIn("adaptation_field_length", " ".join(result.errors))

    def test_corrupt_near_quarter_not_just_middle(self):
        stream = packet() * 10 + (bytes(4) + bytes(188)) + packet() * 29
        result = self.check(stream)
        self.assertFalse(result.structurally_valid)
        self.assertIn("packet 10", " ".join(result.errors))

    def test_last_packet_header(self):
        stream = packet() * 39 + packet(tei=True)
        result = self.check(stream)
        self.assertFalse(result.structurally_valid)
        self.assertIn("packet 39", " ".join(result.errors))

    def test_non_192_byte_aligned_is_rejected(self):
        result = self.check(packet() + b"\x00")
        self.assertFalse(result.structurally_valid)
        self.assertIn("aligned", " ".join(result.errors))


if __name__ == "__main__":
    unittest.main()
