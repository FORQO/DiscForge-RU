"""Synthetic MPLS section and clip reference tests; not hardware validation."""
import tempfile
import unittest
from pathlib import Path

from mpls_preflight import validate_playlist
from bdmv_fixtures import minimal_mpls


class PlaylistPreflightTests(unittest.TestCase):
    def inspect(self, payload: bytes, *, clips=None, streams=None):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / '00000.mpls'
            file.write_bytes(payload)
            return validate_playlist(file, {'00000'} if clips is None else clips,
                                     {'00000'} if streams is None else streams)

    def test_valid_single_play_item(self):
        errors, warnings = self.inspect(minimal_mpls())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_truncated_header_rejected(self):
        self.assertIn('Truncated MPLS header', ' '.join(self.inspect(b'MPLS0200')[0]))

    def test_play_list_address_out_of_file(self):
        data = bytearray(minimal_mpls())
        data[8:12] = (4096).to_bytes(4, 'big')
        self.assertIn('start address', ' '.join(self.inspect(bytes(data))[0]))

    def test_truncated_play_item_rejected(self):
        data = bytearray(minimal_mpls())
        data[50:52] = (0xFFFF).to_bytes(2, 'big')
        self.assertIn('truncated PlayItem', ' '.join(self.inspect(bytes(data))[0]))

    def test_missing_clip_and_stream_references(self):
        errs, _ = self.inspect(minimal_mpls(clip='00001'))
        self.assertIn('missing CLIPINF/00001.clpi', ' '.join(errs))
        self.assertIn('missing STREAM/00001.m2ts', ' '.join(errs))

    def test_invalid_clip_id_rejected_without_filesystem_access(self):
        errs, _ = self.inspect(minimal_mpls(clip='abcde'))
        self.assertIn('invalid five-digit clip ID', ' '.join(errs))

    def test_in_out_must_increase(self):
        data = bytearray(minimal_mpls())
        data[68:72] = bytes(4)
        self.assertIn('non-positive IN/OUT', ' '.join(self.inspect(bytes(data))[0]))

    def test_mark_reference_bounds(self):
        errs, _ = self.inspect(minimal_mpls(mark_item=7))
        self.assertIn('references invalid PlayItem', ' '.join(errs))

    def test_truncated_marks(self):
        data = bytearray(minimal_mpls())
        mark_start = int.from_bytes(data[12:16], 'big')
        data[mark_start + 4:mark_start + 6] = (2).to_bytes(2, 'big')
        self.assertIn('truncated PlayListMark', ' '.join(self.inspect(bytes(data))[0]))

    def test_overlapping_sections(self):
        data = bytearray(minimal_mpls())
        data[12:16] = (70).to_bytes(4, 'big')
        errs, _ = self.inspect(bytes(data))
        self.assertTrue(any('overlapping' in e or 'truncated' in e for e in errs), errs)

    def test_oversized_file_is_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '00000.mpls'
            with path.open('wb') as out:
                out.truncate(16 * 1024 * 1024 + 1)
            errs, _ = validate_playlist(path, {'00000'}, {'00000'})
            self.assertIn('too large', ' '.join(errs))

    def test_bdmv_publish_gate_checks_playlist_references(self):
        from bdmv_preflight import inspect
        from test_bdmv_preflight import fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture(root)
            (root / 'BDMV/PLAYLIST/00000.mpls').write_bytes(minimal_mpls('00001'))
            result = inspect(root)
            self.assertFalse(result.structurally_valid, result.errors)
            self.assertIn('missing CLIPINF/00001', ' '.join(result.errors))


if __name__ == '__main__':
    unittest.main()
