"""Fail-closed BD-J chapter menu input and MPLS marker regressions.

Fixtures are synthetic and deliberately not intended for player compatibility.
"""
import tempfile
import unittest
from pathlib import Path

from bdmv_fixtures import minimal_mpls
from disc_menu import pages, validate_menu_chapters
from mpls_preflight import MAX_PLAYLIST_BYTES


class ChapterTimeValidationTests(unittest.TestCase):
    def test_valid_start_and_order(self):
        self.assertEqual([v for _, v in pages([0.0])[0]], ['play:0'])
        self.assertEqual([v for _, v in pages([0, 0.04, 2.0])[1][:3]],
                         ['play:0', 'play:1', 'play:2'])

    def test_invalid_times_fail_before_rendering(self):
        invalid = ([], list(range(1000)), [1.0], [0, 0],
                   [0, 10, 9], [0, -1], [0, float('nan')],
                   [0, float('inf')], [0, True], [0, '10'],
                   [0, 10 ** 500])
        for times in invalid:
            with self.subTest(times=str(times)[:40]), self.assertRaises(ValueError):
                pages(times)


class MenuMarkBoundaryTests(unittest.TestCase):
    def check_data(self, data, chapters=None):
        with tempfile.TemporaryDirectory() as temp:
            playlist = Path(temp) / '00000.mpls'
            playlist.write_bytes(data)
            validate_menu_chapters(playlist, [0.0] if chapters is None else chapters)

    def test_valid_synthetic_entry_mark(self):
        self.check_data(minimal_mpls())

    def test_mark_record_must_fit_declared_section_not_only_file(self):
        data = bytearray(minimal_mpls())
        offset = int.from_bytes(data[12:16], 'big')
        # One valid-looking record follows, but the section declares only 2 bytes.
        data[offset:offset + 4] = (2).to_bytes(4, 'big')
        with self.assertRaisesRegex(ValueError, 'Количество глав'):
            self.check_data(data)

    def test_mark_section_must_not_exceed_file(self):
        data = bytearray(minimal_mpls())
        offset = int.from_bytes(data[12:16], 'big')
        data[offset:offset + 4] = (999).to_bytes(4, 'big')
        with self.assertRaisesRegex(ValueError, 'границы'):
            self.check_data(data)

    def test_mark_section_must_not_overlap_playlist(self):
        data = bytearray(minimal_mpls())
        data[12:16] = (40).to_bytes(4, 'big')
        with self.assertRaisesRegex(ValueError, 'раздел глав'):
            self.check_data(data)

    def test_chapter_must_reference_an_existing_play_item(self):
        with self.assertRaisesRegex(ValueError, 'PlayItem'):
            self.check_data(minimal_mpls(mark_item=1))

    def test_all_menu_marks_must_be_entry_marks(self):
        data = bytearray(minimal_mpls())
        offset = int.from_bytes(data[12:16], 'big')
        data[offset + 7] = 2
        with self.assertRaisesRegex(ValueError, 'EntryMark'):
            self.check_data(data)

    def test_chapter_count_must_match_playlist(self):
        with self.assertRaisesRegex(ValueError, 'Количество глав'):
            self.check_data(minimal_mpls(), [0, 1])

    def test_reject_malformed_playlist_envelope(self):
        data = bytearray(minimal_mpls())
        data[40:44] = (10000).to_bytes(4, 'big')
        with self.assertRaisesRegex(ValueError, 'PlayList'):
            self.check_data(data)

    def test_reject_excessive_playlist_input(self):
        with self.assertRaisesRegex(ValueError, 'Некорректный плейлист'):
            self.check_data(minimal_mpls() + bytes(MAX_PLAYLIST_BYTES))

    def test_reject_missing_playlist(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'прочитать'):
                validate_menu_chapters(Path(temp) / 'absent.mpls', [0.0])


if __name__ == '__main__':
    unittest.main()
