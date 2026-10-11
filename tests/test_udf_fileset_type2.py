"""Synthetic Type 2 Metadata Partition integration tests (non-mountable ISO)."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from tools import udf_fileset_preflight as fileset
from test_iso_udf_preflight import descriptor, iso_fixture
from test_iso_partition_maps import replace_map
from test_iso_partition_map_type2 import metadata_map
from test_udf_fileset_preflight import install_fileset, long_ad


def fixture(path, *, identifier=b"*UDF Metadata Partition",
            corrupt_crc=False, truncated=False, physical_first=False):
    iso_fixture(path)
    index = 1 if physical_first else 0
    install_fileset(path, fsd_ad=long_ad(reference=index),
                    root_ad=long_ad(lbn=6, reference=index))
    mapping = bytearray(metadata_map(identifier))
    mapping[40:44] = (1).to_bytes(4, "little")
    if physical_first:
        replace_map(path, bytes((1, 6, 1, 0, 0, 0)) + bytes(mapping), count=2)
    else:
        replace_map(path, bytes(mapping))
    with path.open("r+b") as stream:
        for virtual, physical in ((85, 95), (86, 96)):
            stream.seek(virtual * udf.SECTOR_SIZE)
            block = stream.read(udf.SECTOR_SIZE)
            stream.seek(physical * udf.SECTOR_SIZE)
            stream.write(block)
        payload = bytearray(208)
        payload[11] = 250
        payload[40:48] = (20 * udf.SECTOR_SIZE).to_bytes(8, "little")
        payload[196:200] = (8).to_bytes(4, "little")
        payload[200:204] = ((7 if truncated else 20) * udf.SECTOR_SIZE).to_bytes(4, "little")
        payload[204:208] = (10).to_bytes(4, "little")
        entry = bytearray(descriptor(266, 1, bytes(payload)))
        if corrupt_crc:
            entry[56] ^= 1
        stream.seek(81 * udf.SECTOR_SIZE)
        stream.write(entry)


class Type2IntegrationTests(unittest.TestCase):
    def check(self, **args):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "test.iso"
            fixture(image, **args)
            self.assertTrue(udf.inspect(image).valid)
            return fileset.inspect(image)

    def test_metadata_file_maps_fsd_and_root(self):
        report = self.check()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual((report.fsd_lba, report.root_icb_lba), (95, 96))

    def test_partition_map_indices_are_not_physical_partition_numbers(self):
        report = self.check(physical_first=True)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual((report.fsd_lba, report.root_icb_lba), (95, 96))

    def test_virtual_partition_stays_unsupported(self):
        report = self.check(identifier=b"*UDF Virtual Partition")
        self.assertFalse(report.valid)
        self.assertIn("virtual/sparable", " ".join(report.errors))

    def test_metadata_file_crc_is_checked(self):
        report = self.check(corrupt_crc=True)
        self.assertFalse(report.valid)
        self.assertIn("CRC mismatch", " ".join(report.errors))

    def test_incomplete_allocation_is_rejected(self):
        report = self.check(truncated=True)
        self.assertFalse(report.valid)
        self.assertIn("allocation is incomplete", " ".join(report.errors))


if __name__ == "__main__":
    unittest.main()
