"""Regression: contradictory UDF revisions in one VDS must be rejected."""
import tempfile
import unittest
from pathlib import Path

from tools import iso_udf_preflight as udf
from test_iso_udf_preflight import descriptor, iso_fixture


class LVDConsistencyTests(unittest.TestCase):
    def test_conflicting_revisions(self):
        for first, second, accepted in (
            (0x0260, 0x0250, False),
            (0x0250, 0x0260, False),
            (0x0250, 0x0250, True),
        ):
            with self.subTest(first=first, second=second):
                with tempfile.TemporaryDirectory() as temp:
                    image = Path(temp) / "test.iso"
                    iso_fixture(image, revision=first)
                    with image.open("r+b") as handle:
                        handle.seek(32 * udf.SECTOR_SIZE)
                        original = handle.read(udf.SECTOR_SIZE)
                        length = int.from_bytes(original[10:12], "little")
                        payload = bytearray(original[16:16 + length])
                        payload[224:226] = second.to_bytes(2, "little")
                        handle.seek(35 * udf.SECTOR_SIZE)
                        handle.write(descriptor(6, 35, bytes(payload)))
                        handle.seek(36 * udf.SECTOR_SIZE)
                        handle.write(descriptor(8, 36))
                    report = udf.inspect(image)
                    self.assertEqual(report.valid, accepted, report.errors)
                    if not accepted:
                        self.assertIn("Conflicting UDF revisions", " ".join(report.errors))
