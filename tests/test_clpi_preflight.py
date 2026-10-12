"""Synthetic (non-playable) CLPI boundaries and packet-count regression tests."""
import tempfile
import unittest
from pathlib import Path

from clpi_preflight import MAX_CLPI_BYTES, validate_clip
from bdmv_fixtures import minimal_clpi



class ClipPreflightTests(unittest.TestCase):
    def validate(self, data, stream_packets=1):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clpi = root / "00000.clpi"
            stream = root / "00000.m2ts"
            clpi.write_bytes(data)
            stream.write_bytes(b"\0" * (192 * stream_packets))
            return validate_clip(clpi, stream)

    def test_valid_header_and_matching_count(self):
        self.assertEqual(self.validate(minimal_clpi())[0], [])

    def test_valid_0100_header(self):
        self.assertEqual(self.validate(minimal_clpi(version=b"0100"))[0], [])

    def test_optional_cpi_section(self):
        self.assertEqual(self.validate(minimal_clpi(optional=True))[0], [])

    def test_truncated_header(self):
        self.assertIn("Truncated CLPI", ' '.join(self.validate(b"HDMV0200")[0]))

    def test_wrong_signature(self):
        data = bytearray(minimal_clpi()); data[:4] = b"BAD!"
        self.assertIn("signature/version", ' '.join(self.validate(bytes(data))[0]))

    def test_uhd_version_rejected(self):
        self.assertIn("signature/version", ' '.join(self.validate(minimal_clpi(version=b"0300"))[0]))

    def test_zero_sequence_address(self):
        data = bytearray(minimal_clpi()); data[8:12] = bytes(4)
        self.assertIn("missing SequenceInfo", ' '.join(self.validate(bytes(data))[0]))

    def test_program_address_out_of_file(self):
        data = bytearray(minimal_clpi()); data[12:16] = (4096).to_bytes(4, 'big')
        self.assertIn("invalid ProgramInfo", ' '.join(self.validate(bytes(data))[0]))

    def test_truncated_program_body(self):
        data = bytearray(minimal_clpi()); data[66:70] = (100).to_bytes(4, 'big')
        self.assertIn("truncated ProgramInfo", ' '.join(self.validate(bytes(data))[0]))

    def test_overlapping_sections(self):
        data = bytearray(minimal_clpi()); data[12:16] = (60).to_bytes(4, 'big')
        self.assertIn("overlapping", ' '.join(self.validate(bytes(data))[0]))

    def test_truncated_clipinfo_section(self):
        data = bytearray(minimal_clpi()); data[40:44] = (100).to_bytes(4, 'big')
        self.assertIn("truncated ClipInfo", ' '.join(self.validate(bytes(data))[0]))

    def test_zero_packet_count(self):
        self.assertIn("zero source packet count", ' '.join(self.validate(minimal_clpi(packets=0))[0]))

    def test_packet_count_larger_than_stream(self):
        self.assertIn("source packet count mismatch", ' '.join(self.validate(minimal_clpi(packets=2))[0]))

    def test_packet_count_smaller_than_stream(self):
        self.assertIn("source packet count mismatch", ' '.join(self.validate(minimal_clpi(), stream_packets=2)[0]))

    def test_metadata_size_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            clpi=root/'00000.clpi'; stream=root/'00000.m2ts'
            with clpi.open('wb') as out:
                out.truncate(MAX_CLPI_BYTES+1)
            stream.write_bytes(b'\0'*192)
            self.assertIn('too large', ' '.join(validate_clip(clpi,stream)[0]))

    def test_symlinked_stream_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            clpi=root/'00000.clpi'; stream=root/'00000.m2ts'; target=root/'real.m2ts'
            clpi.write_bytes(minimal_clpi()); target.write_bytes(b'\0'*192)
            try:
                stream.symlink_to(target)
            except (OSError,NotImplementedError):
                self.skipTest('Symlinks not available')
            self.assertIn('symlink', ' '.join(validate_clip(clpi,stream)[0]))

    def test_symlinked_clpi_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'real.clpi'; clip = root / '00000.clpi'
            stream = root / '00000.m2ts'
            target.write_bytes(minimal_clpi()); stream.write_bytes(b'\0' * 192)
            try:
                clip.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest('Symlinks not available')
            self.assertIn('symlink', ' '.join(validate_clip(clip, stream)[0]))

    def test_bdmv_gate_rejects_mismatched_clpi_packets(self):
        from bdmv_preflight import inspect
        from test_bdmv_preflight import fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            (root/'BDMV/CLIPINF/00000.clpi').write_bytes(minimal_clpi(packets=2))
            result=inspect(root)
            self.assertFalse(result.structurally_valid)
            self.assertIn('source packet count mismatch', ' '.join(result.errors))


if __name__ == '__main__':
    unittest.main()
