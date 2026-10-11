"""Synthetic LVD partition-map checks; fixtures are not mountable discs."""
import tempfile
import unittest
from pathlib import Path
from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import iso_fixture, descriptor


def replace_map(image, map_bytes, count=1, crc_length=None):
    with image.open("r+b") as stream:
        stream.seek(32 * udf.SECTOR_SIZE)
        old = stream.read(udf.SECTOR_SIZE)
        n = int.from_bytes(old[10:12], "little")
        payload = bytearray(max(n, 424 + len(map_bytes)))
        payload[:n] = old[16:16 + n]
        payload[248:252] = len(map_bytes).to_bytes(4, "little")
        payload[252:256] = count.to_bytes(4, "little")
        payload[424:424 + len(map_bytes)] = map_bytes
        covered = len(payload) if crc_length is None else crc_length
        result = bytearray(descriptor(6, 32, bytes(payload[:covered])))
        result[16:16 + len(payload)] = payload
        stream.seek(32 * udf.SECTOR_SIZE)
        stream.write(result)


class LVDMapTests(unittest.TestCase):
    def test_partition_maps(self):
        valid = bytes((1, 6, 1, 0, 0, 0))
        cases = (
            (valid, 1, None, True),
            (b"", 0, None, False),
            (valid, 2, None, False),
            (bytes((1, 6, 1, 0, 9, 0)), 1, None, False),
            (bytes((3, 6, 1, 0, 0, 0)), 1, None, False),
            (valid, 1, 424, False),
        )
        for maps, count, crc, expected in cases:
            with self.subTest(maps=maps, count=count, crc=crc):
                with tempfile.TemporaryDirectory() as tmp:
                    image = Path(tmp) / "sample.iso"
                    iso_fixture(image)
                    replace_map(image, maps, count, crc)
                    result = udf.inspect(image)
                    self.assertEqual(result.valid, expected, result.errors)


if __name__ == "__main__":
    unittest.main()
