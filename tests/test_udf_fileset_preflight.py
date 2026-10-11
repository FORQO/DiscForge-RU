"""Synthetic Type 1 FSD/root-ICB tests; these are not mountable Blu-ray discs."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from tools import udf_fileset_preflight as fileset
from test_iso_udf_preflight import descriptor, iso_fixture
from test_iso_partition_maps import replace_map
from test_iso_partition_map_type2 import metadata_map


def long_ad(length=udf.SECTOR_SIZE, lbn=5, reference=0):
    return (length.to_bytes(4, "little") + lbn.to_bytes(4, "little")
            + reference.to_bytes(2, "little") + bytes(6))


def install_fileset(path: Path, *, fsd_ad=None, root_ad=None, root_type=4,
                    fsd_tag=256, fsd_revision=0x0250):
    """PD 0 maps 80..229, FSD LBN 5 => physical 85, root LBN 6 => 86."""
    if fsd_ad is None:
        fsd_ad = long_ad()
    if root_ad is None:
        root_ad = long_ad(lbn=6)
    with path.open("r+b") as handle:
        handle.seek(32 * udf.SECTOR_SIZE)
        lvd = handle.read(udf.SECTOR_SIZE)
        payload = bytearray(lvd[16:16 + int.from_bytes(lvd[10:12], "little")])
        payload[232:248] = fsd_ad
        handle.seek(32 * udf.SECTOR_SIZE)
        handle.write(descriptor(6, 32, bytes(payload)))
        fsd_data = bytearray(496)
        fsd_data[384:400] = root_ad
        fsd_data[401:401 + len(udf.UDF_DOMAIN)] = udf.UDF_DOMAIN
        fsd_data[424:426] = fsd_revision.to_bytes(2, "little")
        handle.seek(85 * udf.SECTOR_SIZE)
        handle.write(descriptor(fsd_tag, 5, bytes(fsd_data)))
        entry_data = bytearray(64)
        entry_data[11] = root_type  # ICBTag.FileType, descriptor byte 27.
        handle.seek(86 * udf.SECTOR_SIZE)
        handle.write(descriptor(261, 6, bytes(entry_data)))


class Type1FileSetTests(unittest.TestCase):
    def make(self, path, **kwargs):
        iso_fixture(path)
        install_fileset(path, **kwargs)

    def test_valid_fileset_and_root_directory_icb(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image)
            report = fileset.inspect(image)
            self.assertTrue(report.valid, report.errors)
            self.assertEqual((report.fsd_lba, report.root_icb_lba), (85, 86))
            self.assertIn("NOT verified", report.warnings[0])
            self.assertTrue(udf.inspect(image).valid)

    def test_invalid_fsd_descriptor_id(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image, fsd_tag=257)
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("tag ID", " ".join(report.errors))

    def test_corrupted_fsd_crc(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image)
            with image.open("r+b") as handle:
                handle.seek(85 * udf.SECTOR_SIZE + 440)
                handle.write(b"\x00")
            self.assertIn("CRC mismatch", " ".join(fileset.inspect(image).errors))

    def test_missing_root_directory_icb(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image)
            with image.open("r+b") as handle:
                handle.seek(86 * udf.SECTOR_SIZE)
                handle.write(bytes(udf.SECTOR_SIZE))
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("descriptor tag", " ".join(report.errors))

    def test_root_icb_must_be_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image, root_type=5)
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("not a directory", " ".join(report.errors))

    def test_fsd_extent_outside_partition(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image, fsd_ad=long_ad(length=4096, lbn=149))
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("exceeds the partition", " ".join(report.errors))

    def test_invalid_partition_map_index(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image, fsd_ad=long_ad(reference=1))
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("map index", " ".join(report.errors))

    def test_type2_mapping_is_not_guessed_as_physical(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image)
            replace_map(image, metadata_map())
            self.assertTrue(udf.inspect(image).valid)
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("Type 2 partition map", " ".join(report.errors))

    def test_wrong_fsd_domain_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            self.make(image, fsd_revision=0x0260)
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("not 2.50", " ".join(report.errors))

    def test_missing_fileset_remains_metadata_only_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.iso"
            iso_fixture(image)
            self.assertTrue(udf.inspect(image).valid)
            report = fileset.inspect(image)
            self.assertFalse(report.valid)
            self.assertIn("missing, short", " ".join(report.errors))


if __name__ == "__main__":
    unittest.main()
