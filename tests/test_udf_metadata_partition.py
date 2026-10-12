"""Synthetic, non-mountable Type 2 metadata extent mapping regressions."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from tools import udf_metadata_partition as metadata
from test_iso_udf_preflight import descriptor


def install_metadata(path: Path, *, tag=266, file_type=250, flags=0,
                     extents=((10, 5), (30, 15)), length_blocks=20,
                     crc_bytes=None):
    """Write Metadata File EFE at physical sector 81 (partition starts at 80)."""
    with path.open("wb") as handle:
        handle.truncate(300 * udf.SECTOR_SIZE)
    payload = bytearray(200 + 8 * len(extents))  # 216-byte EFE, short-ad list
    payload[11] = file_type  # ICBTag.FileType: byte 27 in full descriptor
    payload[18:20] = flags.to_bytes(2, "little")
    payload[40:48] = (length_blocks * udf.SECTOR_SIZE).to_bytes(8, "little")
    payload[196:200] = (8 * len(extents)).to_bytes(4, "little")
    for i, (lbn, blocks) in enumerate(extents):
        pos = 200 + i * 8
        payload[pos:pos + 4] = (blocks * udf.SECTOR_SIZE).to_bytes(4, "little")
        payload[pos + 4:pos + 8] = lbn.to_bytes(4, "little")
    if crc_bytes is not None:
        payload = payload[:crc_bytes]
    with path.open("r+b") as handle:
        handle.seek(81 * udf.SECTOR_SIZE)
        handle.write(descriptor(tag, 1, bytes(payload)))


class MetadataPartitionResolverTests(unittest.TestCase):
    def resolve(self, image, *, lbn=0, length=2048):
        with image.open("rb") as handle:
            return metadata.resolve_metadata_lba(
                handle, metadata_icb_lbn=1, physical_start=80,
                physical_sectors=150, metadata_lbn=lbn,
                extent_bytes=length, image_sectors=300)

    def test_first_and_second_contiguous_extent(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image)
            self.assertEqual(self.resolve(image, lbn=0), 90)
            self.assertEqual(self.resolve(image, lbn=4), 94)
            self.assertEqual(self.resolve(image, lbn=5), 110)
            self.assertEqual(self.resolve(image, lbn=19), 124)

    def test_extent_may_not_cross_allocation_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image)
            with self.assertRaisesRegex(ValueError, "crosses an allocation boundary"):
                self.resolve(image, lbn=4, length=4096)

    def test_outside_metadata_file_information_length(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image)
            with self.assertRaisesRegex(ValueError, "exceeds Metadata File length"):
                self.resolve(image, lbn=20)

    def test_wrong_metadata_file_type(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image, file_type=251)
            with self.assertRaisesRegex(ValueError, "type 250"):
                self.resolve(image)

    def test_requires_extended_file_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image, tag=261)
            with self.assertRaisesRegex(ValueError, "Extended File Entry"):
                self.resolve(image)

    def test_long_allocation_descriptors_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image, flags=1)
            with self.assertRaisesRegex(ValueError, "short allocation"):
                self.resolve(image)

    def test_outside_physical_partition(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image, extents=((149, 5),))
            with self.assertRaisesRegex(ValueError, "physical partition"):
                self.resolve(image)

    def test_short_crc_cannot_hide_allocation_descriptors(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image, crc_bytes=200)
            with self.assertRaisesRegex(ValueError, "CRC does not cover"):
                self.resolve(image)

    def test_sparse_and_unrecorded_extent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image)
            with image.open("r+b") as handle:
                handle.seek(81 * udf.SECTOR_SIZE)
                block = handle.read(udf.SECTOR_SIZE)
                count = int.from_bytes(block[10:12], "little")
                payload = bytearray(block[16:16 + count])
                payload[200:204] = (0x40000000 | 5 * udf.SECTOR_SIZE).to_bytes(4, "little")
                handle.seek(81 * udf.SECTOR_SIZE)
                handle.write(descriptor(266, 1, bytes(payload)))
            with self.assertRaisesRegex(ValueError, "unsupported short-ad"):
                self.resolve(image)

    def test_reject_truncated_allocation_table(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "synthetic.iso"
            install_metadata(image, extents=())
            with self.assertRaisesRegex(ValueError, "invalid short-ad"):
                self.resolve(image)


if __name__ == "__main__":
    unittest.main()
