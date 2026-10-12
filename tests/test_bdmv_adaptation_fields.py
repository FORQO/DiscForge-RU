"""Synthetic, sample-only MPEG-TS adaptation field validation regressions."""
import tempfile
import unittest
from pathlib import Path
from bdmv_preflight import inspect
from bdmv_fixtures import minimal_mpls


def m2ts_packet(*, control=3, length=0, flags=0, data=b''):
    ts = bytearray(188)
    ts[:4] = bytes((0x47, 0x1f, 0xff, control << 4))
    ts[4] = length
    ts[5] = flags
    ts[6:6 + len(data)] = data
    return bytes(4) + bytes(ts)


class AdaptationFieldTests(unittest.TestCase):
    def inspect_packet(self, packet):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bdmv = root / 'BDMV'
            for folder in ('PLAYLIST', 'CLIPINF', 'STREAM', 'BACKUP'):
                (bdmv / folder).mkdir(parents=True)
            (root / 'CERTIFICATE').mkdir()
            for name, content in (
                ('index.bdmv', b'INDX0200'),
                ('MovieObject.bdmv', b'MOBJ0200'),
                ('PLAYLIST/00000.mpls', minimal_mpls()),
                ('CLIPINF/00000.clpi', b'HDMV0200'),
                ('STREAM/00000.m2ts', packet),
            ):
                (bdmv / name).write_bytes(content)
            return inspect(root)

    def check_failure(self, packet, message):
        result = self.inspect_packet(packet)
        self.assertFalse(result.structurally_valid, result.errors)
        self.assertIn(message, ' '.join(result.errors))

    def test_pcr_requires_six_bytes(self):
        self.check_failure(m2ts_packet(length=6, flags=0x10), 'PCR')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=7, flags=0x10)).structurally_valid)

    def test_pcr_plus_opcr_requires_twelve_bytes(self):
        self.check_failure(m2ts_packet(length=12, flags=0x18), 'PCR/OPCR')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=13, flags=0x18)).structurally_valid)

    def test_splice_countdown_fits(self):
        self.check_failure(m2ts_packet(length=1, flags=0x04), 'splice')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=2, flags=0x04)).structurally_valid)

    def test_private_data_length_bound(self):
        self.check_failure(m2ts_packet(length=4, flags=0x02, data=b'\x03abc'), 'private data')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=5, flags=0x02, data=b'\x03abc')).structurally_valid)

    def test_extension_length_bound(self):
        self.check_failure(m2ts_packet(length=2, flags=0x01, data=b'\x02\x00\x00'), 'truncated extension')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=4, flags=0x01, data=b'\x02\x00\x00')).structurally_valid)

    def test_extension_ltw_length_bound(self):
        self.check_failure(m2ts_packet(length=4, flags=0x01, data=b'\x02\x80\x00'), 'extension payload')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=5, flags=0x01, data=b'\x03\x80\x00\x00')).structurally_valid)

    def test_extension_piecewise_rate_bound(self):
        self.check_failure(m2ts_packet(length=4, flags=0x01, data=b'\x02\x40\x00'), 'extension payload')
        self.assertTrue(self.inspect_packet(m2ts_packet(length=6, flags=0x01, data=b'\x04\x40\x00\x00\x00')).structurally_valid)

    def test_adaptation_only_still_requires_183_bytes(self):
        self.check_failure(m2ts_packet(control=2, length=182), '183')
        self.assertTrue(self.inspect_packet(m2ts_packet(control=2, length=183)).structurally_valid)

    def test_zero_length_adaptation_with_payload_is_valid(self):
        self.assertTrue(self.inspect_packet(m2ts_packet(length=0)).structurally_valid)


if __name__ == '__main__':
    unittest.main()
