from dataclasses import replace
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import MediaInfo, make_video_command
from quality_trial import (TrialPlan, FPS, suggested_rates, reference_command, variant_command,
    comparison_command, source_stamp, ensure_source, capture, run_trial, video_peak)


def movie(path='movie.mkv'):
    return MediaInfo(str(Path(path).resolve()), 100, 4, 'h264', 640, 360,
                     '25/1', '', '', 0, 0, 0)


class QualityTrialTests(unittest.TestCase):
    def test_bounds_and_short_tail(self):
        info = movie()
        self.assertEqual(TrialPlan(99.5, 10, (5_000_000,)).validate(info), math.floor(.5*FPS))
        for start, duration in ((-1, 5), (100, 5), (0, 31), (0, 0), (float('nan'), 10)):
            with self.assertRaises(ValueError):
                TrialPlan(start, duration, (5_000_000,)).validate(info)
        for rates in ((), (5_000_000,)*2, (4_000_000,), (28_000_001,), (True,)):
            with self.assertRaises(ValueError):
                TrialPlan(0, 5, rates).validate(info)
        with self.assertRaisesRegex(ValueError, 'HDR'):
            TrialPlan(0, 5, (5_000_000,)).validate(replace(info, video_color_transfer='smpte2084'))

    def test_three_distinct_rates_even_at_limits(self):
        for center in (0, 5_000_000, 6_500_000, 12_000_000, 28_000_000, 90_000_000):
            rates = suggested_rates(center)
            self.assertEqual(len(set(rates)), 3)
            self.assertTrue(all(5_000_000 <= rate <= 28_000_000 for rate in rates))

    def test_production_encoder_parameters_are_shared(self):
        for gpu in (True, False):
            for fast in (True, False):
                original = make_video_command('ffmpeg', 'source', 'output', 8_000_000, gpu, fast)
                trial = variant_command('ffmpeg', 'reference', 'output', 8_000_000, gpu, fast, 48, 25_000_000)
                for option in ('-c:v', '-preset', '-profile:v', '-level:v', '-b:v', '-bufsize', '-g', '-bf', '-r', '-pix_fmt'):
                    self.assertEqual(trial[trial.index(option)+1], original[original.index(option)+1])
                self.assertEqual(trial[trial.index('-maxrate')+1], '25000000')
                self.assertNotIn('-vf', trial)

    def test_reference_keeps_scaler_and_frame_clock_without_lossy_encoder(self):
        plan = TrialPlan(12, 2, (5_000_000,))
        original = make_video_command('ffmpeg', 'source', 'out', 5_000_000, False)
        ref = reference_command('ffmpeg', movie(), 'reference', plan, 47)
        self.assertEqual(ref[ref.index('-vf')+1], original[original.index('-vf')+1])
        self.assertEqual(ref[ref.index('-c:v')+1], 'ffv1')
        self.assertLess(ref.index('-ss'), ref.index('-i'))
        self.assertNotIn('-b:v', ref)
        cuda = reference_command('ffmpeg', movie(), 'reference', plan, 47, True)
        self.assertIn('hwdownload,format=yuv420p', cuda[cuda.index('-vf')+1])
        self.assertIn('-hwaccel', cuda)

    def test_comparison_selects_same_decoded_frame_not_independent_seeks(self):
        cmd = comparison_command('ffmpeg', 'a', 'b', 23)
        self.assertNotIn('-ss', cmd)
        self.assertIn('hstack=shortest=1,select=gte(n\\,23)', cmd[cmd.index('-filter_complex')+1])
        self.assertEqual(cmd[cmd.index('-frames:v')+1], '1')
        self.assertNotIn('-frames:v', comparison_command('ffmpeg', 'a', 'b', 23, True))

    def test_changed_source_and_missing_source_are_not_applied(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'source.mkv'
            p.write_bytes(b'film')
            stamp = source_stamp(p)
            ensure_source(p, stamp)
            p.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'изменился'):
                ensure_source(p, stamp)
            p.unlink()
            with self.assertRaises(OSError):
                ensure_source(p, stamp)

    def test_cancel_kills_probe_child(self):
        cancel = threading.Event()
        timer = threading.Timer(.15, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(RuntimeError, 'отменена'):
                capture([sys.executable, '-c', 'import time; time.sleep(30)'], cancel)
        finally:
            timer.cancel()

    def test_insufficient_space_never_starts_encoder(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'source.mkv'
            p.write_bytes(b'film')
            with patch('quality_trial.shutil.disk_usage') as disk, patch('quality_trial.run_command') as command:
                disk.return_value.free = 0
                with self.assertRaisesRegex(ValueError, 'свободного места'):
                    run_trial(movie(p), TrialPlan(0, 5, (5_000_000,)), {}, Path(temp)/'trial',
                              threading.Event(), lambda _: None, lambda _: None)
                command.assert_not_called()

    def test_truehd_peak_reserved_for_video_like_production(self):
        track = dict(index=1, codec_name='truehd', sample_rate='48000', channels=8)
        info = replace(movie(), audio_tracks=1, audio_streams=(track,), selected_audio=(1,))
        self.assertEqual(video_peak(info), 24_720_000)
        self.assertEqual(video_peak(replace(info, audio_modes={'1': 'ac3'})), 30_000_000)


class QualityWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_targets_only_selected_film_and_persists(self):
        from main import Window
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem
        from unittest.mock import MagicMock
        with patch('main.load_settings', return_value={}):
            window = Window()
        try:
            for name in ('a.mkv', 'b.mkv'):
                info = movie(name)
                item = QListWidgetItem(info.name)
                item.setData(Qt.UserRole, info.path)
                window.movies.addItem(item)
                window._scanned(info.path, info, '')
            window.movies.setCurrentRow(1)
            mode = window.processing_mode.currentIndex()
            with patch('quality_dialog.QualityDialog') as dialog:
                dialog.Accepted = 1
                instance = MagicMock(selected_rate=6_500_000)
                instance.exec.return_value = 1
                dialog.return_value = instance
                window._quality_trial()
            self.assertEqual(window.bitrate_overrides, {movie('b.mkv').path: 6_500_000})
            self.assertEqual(window._video_rates()[movie('b.mkv').path], 6_500_000)
            self.assertEqual(window._project_data()['bitrate_overrides'], window.bitrate_overrides)
            self.assertEqual(window.processing_mode.currentIndex(), mode)
        finally:
            window.capacity_clock.stop()
            window.deleteLater()

    def test_editing_trial_settings_disables_apply(self):
        from quality_dialog import QualityDialog
        dialog = QualityDialog(movie(), {'output': tempfile.gettempdir()}, 8_000_000, encoder=0)
        try:
            dialog.result_plan = dialog._plan()
            dialog.result = {'variants': []}
            dialog.table.blockSignals(True)
            dialog.table.setRowCount(1)
            dialog.table.setCurrentCell(0, 0)
            dialog.table.blockSignals(False)
            dialog._refresh_apply()
            self.assertTrue(dialog.apply_button.isEnabled())
            dialog.rates[0].setValue(7)
            self.assertFalse(dialog.apply_button.isEnabled())
        finally:
            dialog.result = None
            dialog.reject()
            dialog.deleteLater()

    def test_parent_close_waits_for_trial_shutdown(self):
        from main import Window
        from PySide6.QtGui import QCloseEvent
        from quality_dialog import QualityDialog
        from types import SimpleNamespace
        with patch('main.load_settings', return_value={}):
            window = Window()
        dialog = QualityDialog(movie(), {'output': tempfile.gettempdir()}, 8_000_000, parent=window)
        running = [True]
        dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: running[0])
        window.quality_dialog = dialog
        try:
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertTrue(dialog.worker.cancel.is_set())
            self.assertTrue(dialog.close_pending)
        finally:
            window.quality_dialog = None
            running[0] = False
            dialog.finished.disconnect()
            dialog.reject()
            window.capacity_clock.stop()
            window.deleteLater()


if __name__ == '__main__':
    unittest.main()
