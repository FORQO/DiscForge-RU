"""Reject corrupt duplicate descriptors in the active UDF VDS.

Synthetic metadata only: this does not establish a mountable Blu-ray ISO.
"""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import descriptor, iso_fixture


class VDSDescriptorIntegrityTests(unittest.TestCase):
    def test_corrupt_descriptor_after_valid_metadata_is_not_skipped(self):
        for tag, name in ((1, "Primary Volume Descriptor"),
                          (5, "Partition Descriptor"),
                          (6, "Logical Volume Descriptor"),
                          (8, "Terminating Descriptor")):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temp:
                image = Path(temp) / "test.iso"
                iso_fixture(image)
                bad = bytearray(descriptor(tag, 35))
                bad[4] ^= 1  # Invalid checksum, descriptor ID remains intact.
                with image.open("r+b") as handle:
                    handle.seek(35 * udf.SECTOR_SIZE)
                    handle.write(bad)
                    handle.seek(36 * udf.SECTOR_SIZE)
                    handle.write(descriptor(8, 36))
                report = udf.inspect(image)
                self.assertFalse(report.valid, report.errors)
                self.assertIn(name, " ".join(report.errors))

    def test_corrupt_duplicate_lvd_crc_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "test.iso"
            iso_fixture(image)
            with image.open("r+b") as handle:
                handle.seek(32 * udf.SECTOR_SIZE)
                original = handle.read(udf.SECTOR_SIZE)
                crc_length = int.from_bytes(original[10:12], "little")
                handle.seek(35 * udf.SECTOR_SIZE)
                handle.write(descriptor(6, 35, original[16:16 + crc_length]))
                handle.seek(35 * udf.SECTOR_SIZE + 240)
                handle.write(bytes([0x60]))  # Alter CRC-covered UDF revision.
                handle.seek(36 * udf.SECTOR_SIZE)
                handle.write(descriptor(8, 36))
            report = udf.inspect(image)
            self.assertFalse(report.valid, report.errors)
            self.assertIn("Logical Volume Descriptor", " ".join(report.errors))

    def test_zero_padding_before_terminator_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "test.iso"
            iso_fixture(image)
            with image.open("r+b") as handle:
                handle.seek(35 * udf.SECTOR_SIZE)
                handle.write(bytes(udf.SECTOR_SIZE))
                handle.seek(36 * udf.SECTOR_SIZE)
                handle.write(descriptor(8, 36))
            report = udf.inspect(image)
            self.assertTrue(report.valid, report.errors)


if __name__ == "__main__":
    unittest.main()
