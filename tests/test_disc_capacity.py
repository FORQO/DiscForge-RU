import unittest
from dataclasses import replace
from unittest.mock import patch
from core import MediaInfo
from smart_copy import CopyReport
from disc_capacity import estimate, require_fit


def film(size=8_000_000_000):
    tracks = ({'index': 1, 'codec_name': 'ac3', 'sample_rate': '48000', 'channels': 2, 'bit_rate': '384000'},
              {'index': 2, 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2})
    report = CopyReport(video_ok=True, audio_ok=True, video_bytes=size, video_max_bps=10_000_000,
                        audio_results={1: {'ok': True, 'bytes': 345600000}, 2: {'ok': False, 'bytes': 100000000, 'reason': 'AAC'}})
    return MediaInfo('film.mkv', 7200, size+445600000, 'h264', 1920, 1080, '24/1', '', 'ac3', 2, 2, 0,
                     copy_report=report, audio_streams=tracks, selected_audio=(1,))


class CapacityTests(unittest.TestCase):
    def test_playlist_rebinding_is_bounded_and_leaves_original_untouched(self):
        from disc_collection import rebind_playlist
        from bdmv_fixtures import minimal_mpls
        original = minimal_mpls()
        changed = rebind_playlist(original, '00001')
        self.assertIn(b'00001M2TS', changed)
        self.assertIn(b'00000M2TS', original)
        for bad in (b'', original[:50], original.replace(b'00000M2TS', b'00002M2TS')):
            with self.assertRaises(ValueError):
                rebind_playlist(bad, '00001')

    def test_common_budget_adds_titles_and_blocks_overflow(self):
        one = (film(), [0])
        two = estimate([one, one], 'BD25', collection=True)
        self.assertEqual(two.video, 16_000_000_000)
        require_fit(two)
        three = estimate([one]*3, 'BD25', collection=True)
        with self.assertRaisesRegex(ValueError, 'Сборка запрещена'):
            require_fit(three)
        require_fit(estimate([one]*3, 'BD50', collection=True))

    def test_selected_tracks_and_rendered_subtitles_count(self):
        first = estimate([(film(), [0])], 'BD25', collection=True)
        media = replace(film(), selected_audio=(1, 2), selected_subtitles=({'source': 'embedded', 'codec': 'subrip'},))
        second = estimate([(media, [0])], 'BD25', collection=True)
        self.assertEqual(second.audio-first.audio, 7200*640000//8)
        self.assertEqual(second.subtitles, 7200*300000//8)
        self.assertGreater(second.overhead, first.overhead)

    def test_truehd_companion_and_unchecked_copy_reserved(self):
        media = replace(film(), audio_streams=({'index': 1, 'codec_name': 'truehd', 'channels': 8, 'sample_rate': '48000'},))
        budget = estimate([(media, [0])], 'BD25', collection=True)
        self.assertEqual(budget.audio, 345600000 + 7200*640000//8)

    def test_collection_never_silently_reduces_bitrate(self):
        media = film(40_000_000_000)
        auto = estimate([(media, [0])], 'BD25', collection=True)
        self.assertEqual(auto.video, 40_000_000_000)
        low = estimate([(media, [0])], 'BD25', 'encode', collection=True, bitrate=8_000_000)
        self.assertEqual(low.video, 7_200_000_000)

    def test_window_refuses_before_starting_worker(self):
        from PySide6.QtWidgets import QApplication, QListWidgetItem
        from PySide6.QtCore import Qt
        from main import Window
        app = QApplication.instance() or QApplication([])
        with patch('main.load_settings', return_value={}):
            window = Window()
        media = film(30_000_000_000)
        window.media[media.path] = media
        window.chapters[media.path] = [0]
        item = QListWidgetItem(media.name)
        item.setData(Qt.UserRole, media.path)
        window.movies.addItem(item)
        window._update_capacity()
        self.assertTrue(window._project_data()['collection'])
        window.collection_bitrate.setValue(8)
        self.assertEqual(window._project_data()['collection_bitrate'], 8)
        self.assertEqual(window.capacity_bar.value(), 25000)
        self.assertIn('Не хватает', window.capacity_label.text())
        with patch('main.QMessageBox.warning') as warning, patch('main.BuildThread') as worker:
            window.start_build()
        warning.assert_called_once()
        worker.assert_not_called()
        window.capacity_clock.stop()
        window.deleteLater()
