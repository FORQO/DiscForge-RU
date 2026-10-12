"""Sampled M2TS TP_extra_header copy permissions for clear Full HD authoring."""
import tempfile
import unittest
from pathlib import Path

from bdmv_preflight import inspect
from bdmv_fixtures import minimal_clpi
from test_bdmv_preflight import fixture


class M2TSCopyPermissionTests(unittest.TestCase):
    def check(self, copy_bits: int, *, packets: int = 1, marked_index: int = 0,
              arrival_timestamp: int = 0):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            clip = root / "BDMV/CLIPINF/00000.clpi"
            stream = root / "BDMV/STREAM/00000.m2ts"
            standard = stream.read_bytes()
            payload = bytearray(standard * packets)
            word = (copy_bits << 30) | arrival_timestamp
            start = 192 * marked_index
            payload[start:start + 4] = word.to_bytes(4, "big")
            stream.write_bytes(payload)
            clip.write_bytes(minimal_clpi(packets=packets))
            return inspect(root)

    def test_clear_source_packet_passes(self):
        result = self.check(0)
        self.assertTrue(result.structurally_valid, result.errors)

    def test_thirty_bit_arrival_timestamp_is_not_copy_control(self):
        result = self.check(0, arrival_timestamp=(1 << 30) - 1)
        self.assertTrue(result.structurally_valid, result.errors)

    def test_nonzero_copy_indicators_rejected(self):
        for copy_bits in (1, 2, 3):
            with self.subTest(copy_bits=copy_bits):
                result = self.check(copy_bits)
                self.assertFalse(result.structurally_valid)
                self.assertIn("copy_permission_indicator", " ".join(result.errors))
                self.assertIn("packet 0", " ".join(result.errors))

    def test_nonzero_copy_indicator_at_end_of_sampled_stream(self):
        result = self.check(3, packets=45, marked_index=44)
        self.assertFalse(result.structurally_valid)
        self.assertIn("packet 44", " ".join(result.errors))


if __name__ == "__main__":
    unittest.main()
