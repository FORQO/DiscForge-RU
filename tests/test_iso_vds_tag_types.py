"""Reject unsupported descriptor kinds in UDF VDS metadata (synthetic ISO fixtures)."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import descriptor, iso_fixture


class VDSDescriptorTypeTests(unittest.TestCase):
    def test_rejects_unexpected_descriptor_types(self):
        for tag in (2, 9, 256):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as tmp:
                image = Path(tmp) / "disc.iso"
                iso_fixture(image)
                with image.open("r+b") as stream:
                    stream.seek(35 * udf.SECTOR_SIZE)
                    payload = ((16 * udf.SECTOR_SIZE).to_bytes(4, "little")
                               + (32).to_bytes(4, "little")) if tag == 2 else b"checked payload"
                    stream.write(descriptor(tag, 35, payload))
                    stream.seek(36 * udf.SECTOR_SIZE)
                    stream.write(descriptor(8, 36))
                report = udf.inspect(image)
                self.assertFalse(report.valid, report.errors)
                self.assertIn("Unexpected descriptor type", " ".join(report.errors))

    def test_rejects_unsupported_vds_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disc.iso"
            iso_fixture(image)
            with image.open("r+b") as stream:
                stream.seek(35 * udf.SECTOR_SIZE)
                stream.write(descriptor(3, 35, b"pointer payload"))
                stream.seek(36 * udf.SECTOR_SIZE)
                stream.write(descriptor(8, 36))
            report = udf.inspect(image)
            self.assertFalse(report.valid, report.errors)
            self.assertIn("Volume Descriptor Pointer", " ".join(report.errors))

    def test_accepts_optional_vds_descriptors(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disc.iso"
            iso_fixture(image)
            with image.open("r+b") as stream:
                for lba, tag in ((35, 4), (36, 7), (37, 8)):
                    stream.seek(lba * udf.SECTOR_SIZE)
                    stream.write(descriptor(tag, lba, b"verified payload"))
            report = udf.inspect(image)
            self.assertTrue(report.valid, report.errors)


if __name__ == "__main__":
    unittest.main()
