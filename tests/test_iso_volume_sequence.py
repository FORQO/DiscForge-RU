"""Standalone Blu-ray ISO partition maps cannot reference another volume.

Synthetic metadata fixtures only: no mountability or player compatibility claim.
"""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from test_iso_partition_map_type2 import metadata_map
from test_iso_partition_maps import replace_map
from test_iso_udf_preflight import iso_fixture


def type1_map(volume_sequence: int) -> bytes:
    return bytes((1, 6)) + volume_sequence.to_bytes(2, "little") + bytes(2)


def type2_map(volume_sequence: int) -> bytes:
    mapping = bytearray(metadata_map())
    mapping[36:38] = volume_sequence.to_bytes(2, "little")
    return bytes(mapping)


class SingleVolumePartitionMapTests(unittest.TestCase):
    def test_type1_and_type2_reject_external_volume_references(self):
        for make_map in (type1_map, type2_map):
            for sequence in (0, 1, 2, 65535):
                with self.subTest(map_type=make_map.__name__, sequence=sequence):
                    with tempfile.TemporaryDirectory() as temp:
                        path = Path(temp) / "test.iso"
                        iso_fixture(path)
                        replace_map(path, make_map(sequence))
                        report = udf.inspect(path)
                        self.assertEqual(report.valid, sequence == 1, report.errors)
                        if sequence != 1:
                            self.assertIn("volume sequence", " ".join(report.errors))

    def test_multiple_maps_cannot_hide_external_volume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.iso"
            iso_fixture(path)
            replace_map(path, type1_map(1) + type2_map(2), count=2)
            report = udf.inspect(path)
            self.assertFalse(report.valid)
            self.assertIn("volume sequence", " ".join(report.errors))


if __name__ == "__main__":
    unittest.main()
