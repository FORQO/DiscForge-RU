"""Synthetic MPLS SubPath envelope regression tests, not a playable BD."""

import tempfile
import unittest
from pathlib import Path

from bdmv_fixtures import minimal_mpls
from mpls_preflight import validate_playlist


def with_subpaths(lengths, declared=None):
    data = bytearray(minimal_mpls())
    section = int.from_bytes(data[8:12], "big")
    marks = int.from_bytes(data[12:16], "big")
    count = len(lengths) if declared is None else declared
    data[section + 8:section + 10] = count.to_bytes(2, "big")
    records = b"".join(length.to_bytes(4, "big") + bytes(4) for length in lengths)
    data[marks:marks] = records
    original = int.from_bytes(data[section:section + 4], "big")
    data[section:section + 4] = (original + len(records)).to_bytes(4, "big")
    data[12:16] = (marks + len(records)).to_bytes(4, "big")
    return bytes(data)


class MPLSSubPathTests(unittest.TestCase):
    def inspect(self, data):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "00000.mpls"
            path.write_bytes(data)
            return validate_playlist(path, {"00000"}, {"00000"})

    def test_no_subpath_still_valid(self):
        self.assertEqual(self.inspect(minimal_mpls())[0], [])

    def test_one_complete_subpath(self):
        errors, warnings = self.inspect(with_subpaths([4]))
        self.assertEqual(errors, [])
        self.assertTrue(any("SubPaths" in item for item in warnings))

    def test_two_complete_subpaths(self):
        errors, warnings = self.inspect(with_subpaths([4, 4]))
        self.assertEqual(errors, [])
        self.assertTrue(any("2" in item for item in warnings))

    def test_missing_declared_subpath(self):
        errors, _ = self.inspect(with_subpaths([], 1))
        self.assertTrue(any("missing SubPath 0" in item for item in errors))

    def test_missing_second_subpath(self):
        errors, _ = self.inspect(with_subpaths([4], 2))
        self.assertTrue(any("missing SubPath 1" in item for item in errors))

    def test_zero_length_subpath(self):
        errors, _ = self.inspect(with_subpaths([0]))
        self.assertTrue(any("truncated SubPath 0" in item for item in errors))

    def test_overflowing_subpath(self):
        errors, _ = self.inspect(with_subpaths([65535]))
        self.assertTrue(any("truncated SubPath 0" in item for item in errors))
