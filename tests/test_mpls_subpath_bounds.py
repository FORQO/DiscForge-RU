"""Test truncated MPLS SubPath records using synthetic metadata."""

import tempfile
import unittest
from pathlib import Path
from bdmv_fixtures import minimal_mpls
from mpls_preflight import validate_playlist

class MPLSSubPathTests(unittest.TestCase):
    def test_missing_declared_subpath(self):
        data = bytearray(minimal_mpls())
        section = int.from_bytes(data[8:12], "big")
        data[section + 8:section + 10] = (1).to_bytes(2, "big")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "00000.mpls"
            path.write_bytes(data)
            errors, _ = validate_playlist(path, {"00000"}, {"00000"})
        self.assertTrue(any("SubPath" in item for item in errors))
