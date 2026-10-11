"""Regression tests for UDF partition bounds (ECMA-167 3/10.5)."""
import tempfile
import unittest
from pathlib import Path
from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import descriptor, iso_fixture

def write_pd(path, lba, start, length, crc_length=180):
    data = bytearray(180)
    data[9:15] = b'+NSR03'  # Valid Partition Contents Identifier (ECMA-167).
    data[172:176] = start.to_bytes(4, "little")
    data[176:180] = length.to_bytes(4, "little")
    with path.open("r+b") as handle:
        handle.seek(lba * udf.SECTOR_SIZE)
        handle.write(descriptor(5, lba, bytes(data[:crc_length])))

class PartitionDescriptorBoundsTests(unittest.TestCase):
    def test_bounds(self):
        for start, length, expected in ((80, 150, True), (80, 0, False), (280, 30, False), (299, 2, False), (300, 1, False)):
            with self.subTest(start=start, length=length), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "test.iso"
                iso_fixture(path)
                write_pd(path, 34, start, length)
                report = udf.inspect(path)
                self.assertEqual(report.valid, expected, report.errors)
                if not expected:
                    self.assertIn("Partition Descriptor", " ".join(report.errors))

    def test_crc_covers_partition_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.iso"
            iso_fixture(path)
            write_pd(path, 34, 80, 150, crc_length=172)
            report = udf.inspect(path)
            self.assertFalse(report.valid)
            self.assertIn("CRC does not cover partition bounds", " ".join(report.errors))

    def test_reserve_vds_fallback_on_invalid_partition(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.iso"
            iso_fixture(path)
            with path.open("r+b") as handle:
                handle.seek(32 * udf.SECTOR_SIZE)
                lvd = handle.read(udf.SECTOR_SIZE)
                crc_length = int.from_bytes(lvd[10:12], "little")
                handle.seek(64 * udf.SECTOR_SIZE)
                handle.write(descriptor(6, 64, lvd[16:16 + crc_length]))
                for lba, tag in ((65, 1), (66, 5), (67, 8)):
                    handle.seek(lba * udf.SECTOR_SIZE)
                    handle.write(descriptor(tag, lba))
                extent = (16 * udf.SECTOR_SIZE).to_bytes(4, "little")
                main = extent + (32).to_bytes(4, "little")
                reserve = extent + (64).to_bytes(4, "little")
                handle.seek(256 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 256, main + reserve))
            write_pd(path, 34, 290, 20)
            report = udf.inspect(path)
            self.assertTrue(report.valid, report.errors)
            self.assertTrue(any("Reserve VDS" in w for w in report.warnings))
