"""Additional bounded-mapping checks using synthetic, non-mountable data."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from tools.udf_metadata_partition import resolve_metadata_lba
from test_udf_metadata_partition import install_metadata


class MetadataBoundaryRegressions(unittest.TestCase):
    def check(self, *, extents=((10, 5), (30, 15)), length_blocks=20,
              expected=None, error=None, **overrides):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "fixture.iso"
            install_metadata(image, extents=extents, length_blocks=length_blocks)
            args = dict(metadata_icb_lbn=1, physical_start=80,
                        physical_sectors=150, metadata_lbn=0,
                        extent_bytes=udf.SECTOR_SIZE, image_sectors=300)
            args.update(overrides)
            with image.open("rb") as handle:
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        resolve_metadata_lba(handle, **args)
                else:
                    self.assertEqual(resolve_metadata_lba(handle, **args), expected)

    def test_zero_or_negative_extents_rejected(self):
        for value in (0, -1):
            with self.subTest(extent_bytes=value):
                self.check(extent_bytes=value, error="invalid requested logical extent")

    def test_negative_metadata_lbn_rejected(self):
        self.check(metadata_lbn=-1, error="invalid requested logical extent")

    def test_invalid_physical_partition_sizes_rejected(self):
        for size in (0, 221):
            with self.subTest(physical_sectors=size):
                self.check(physical_sectors=size, error="invalid underlying physical partition")

    def test_metadata_icb_at_partition_end_rejected(self):
        self.check(metadata_icb_lbn=150, error="outside the physical partition")

    def test_incomplete_metadata_file_allocations_rejected(self):
        self.check(extents=((10, 5),), length_blocks=20,
                   error="Metadata File allocation is incomplete")

    def test_last_valid_sector_of_second_extent(self):
        self.check(metadata_lbn=19, expected=124)


if __name__ == "__main__":
    unittest.main()
