"""Type 2 map and reserve VDS fallback tests; synthetic ISO metadata only."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import ISOUDFFullHDTests, iso_fixture
from test_iso_partition_maps import replace_map


def metadata_map(identifier=b"*UDF Metadata Partition"):
    data = bytearray(64)
    data[0:2] = bytes((2, 64))
    data[5:5 + len(identifier)] = identifier
    data[36:38] = (1).to_bytes(2, "little")
    return bytes(data)


class MetadataPartitionMapTests(unittest.TestCase):
    def test_type2_metadata_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "test.iso"
            iso_fixture(image)
            replace_map(image, metadata_map())
            report = udf.inspect(image)
            self.assertTrue(report.valid, report.errors)

    def test_type2_without_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "test.iso"
            iso_fixture(image)
            replace_map(image, metadata_map(b""))
            self.assertFalse(udf.inspect(image).valid)

    def test_backup_vds_when_main_map_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "test.iso"
            iso_fixture(image)
            ISOUDFFullHDTests()._install_reserve_vds(image)
            replace_map(image, bytes((1, 6, 1, 0, 9, 0)))
            report = udf.inspect(image)
            self.assertTrue(report.valid, report.errors)
            self.assertTrue(any("Reserve VDS" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
