"""Fallback to backup anchor when primary AVDP is valid but its VDS is not.

Synthetic metadata fixtures only; these are not mountable Blu-ray ISO images.
"""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import descriptor, iso_fixture


class BackupAnchorFallbackTests(unittest.TestCase):
    def test_primary_anchor_with_bad_extent_uses_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'test.iso'
            iso_fixture(image)
            length = (16 * udf.SECTOR_SIZE).to_bytes(4, 'little')
            with image.open('r+b') as handle:
                handle.seek(256 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 256, length + (400).to_bytes(4, 'little')))
                handle.seek(299 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 299, length + (32).to_bytes(4, 'little')))
            report = udf.inspect(image)
            self.assertTrue(report.valid, report.errors)
            self.assertTrue(any('backup anchor' in w for w in report.warnings))

    def test_corrupt_primary_lvd_uses_valid_backup_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'test.iso'
            iso_fixture(image)
            length = (16 * udf.SECTOR_SIZE).to_bytes(4, 'little')
            with image.open('r+b') as handle:
                handle.seek(32 * udf.SECTOR_SIZE)
                original = handle.read(udf.SECTOR_SIZE)
                payload_size = int.from_bytes(original[10:12], 'little')
                handle.seek(64 * udf.SECTOR_SIZE)
                handle.write(descriptor(6, 64, original[16:16 + payload_size]))
                handle.seek(65 * udf.SECTOR_SIZE)
                handle.write(descriptor(8, 65))
                handle.seek(32 * udf.SECTOR_SIZE + 240)
                handle.write(b'\x60')  # corrupt primary LVD CRC
                handle.seek(299 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 299, length + (64).to_bytes(4, 'little')))
            report = udf.inspect(image)
            self.assertTrue(report.valid, report.errors)
            self.assertTrue(any('backup anchor' in w for w in report.warnings))

    def test_valid_primary_wrong_revision_is_not_hidden_by_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'test.iso'
            iso_fixture(image, revision=0x0260)
            length = (16 * udf.SECTOR_SIZE).to_bytes(4, 'little')
            with image.open('r+b') as handle:
                handle.seek(64 * udf.SECTOR_SIZE)
                payload = bytearray(226)
                payload[196:200] = udf.SECTOR_SIZE.to_bytes(4, 'little')
                payload[201:201 + len(udf.UDF_DOMAIN)] = udf.UDF_DOMAIN
                payload[224:226] = (0x0250).to_bytes(2, 'little')
                handle.write(descriptor(6, 64, bytes(payload)))
                handle.seek(65 * udf.SECTOR_SIZE)
                handle.write(descriptor(8, 65))
                handle.seek(299 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 299, length + (64).to_bytes(4, 'little')))
            report = udf.inspect(image)
            self.assertFalse(report.valid)
            self.assertEqual(report.udf_revision, '0x0260')

    def test_rejects_when_both_anchors_point_out_of_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'test.iso'
            iso_fixture(image)
            length = (16 * udf.SECTOR_SIZE).to_bytes(4, 'little')
            with image.open('r+b') as handle:
                handle.seek(256 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 256, length + (400).to_bytes(4, 'little')))
                handle.seek(299 * udf.SECTOR_SIZE)
                handle.write(descriptor(2, 299, length + (401).to_bytes(4, 'little')))
            report = udf.inspect(image)
            self.assertFalse(report.valid)
            self.assertTrue(any('anchor' in e for e in report.errors), report.errors)


if __name__ == '__main__':
    unittest.main()
