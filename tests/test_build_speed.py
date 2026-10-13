import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import unittest
from unittest.mock import patch
from core import MediaInfo, DiscForgeError, make_encode_command, nvenc_available
from build_timing import BuildEstimate, stage_plan


class TimingTests(unittest.TestCase):
    def test_eta_includes_later_stages_and_queue(self):
        now = [0]
        plans = [{'encode': 100, 'mux': 20, 'iso': 10, 'verify': 2}]*2
        estimate = BuildEstimate(plans, clock=lambda: now[0])
        self.assertEqual(estimate.update(0, 'encode', 0), -1)
        now[0] = 25
        # Half the first encode has finished in 25s: second encode must use this rate too.
        self.assertAlmostEqual(estimate.update(0, 'encode', .5), 25+32+50+32)
        self.assertAlmostEqual(estimate.update(0, 'mux', 0), 32+50+32)
        self.assertGreater(estimate.update(1, 'verify', 1), 0)

    def test_each_stage_uses_its_own_rate(self):
        now = [0]
        e = BuildEstimate([{'encode': 100, 'mux': 20}], clock=lambda: now[0])
        e.update(0, 'encode', 0)
        now[0] = 10
        e.update(0, 'encode', 1)
        e.update(0, 'mux', 0)
        now[0] = 20
        self.assertAlmostEqual(e.update(0, 'mux', .5), 10)

    def test_folder_plan_excludes_iso(self):
        self.assertNotIn('iso', stage_plan(100, 1000, False, True, True))
        self.assertIn('iso', stage_plan(100, 1000, True, True, True))


class EncodingTests(unittest.TestCase):
    def test_one_input_two_explicit_outputs(self):
        info = MediaInfo('movie.mkv', 10, 100, 'h264', 1920, 1080, '24/1', '', 'aac', 6, 2, 0)
        cmd = make_encode_command('ffmpeg', info, 'video.h264', 'audio.ac3', 20000000, True, True)
        self.assertEqual(cmd.count('-i'), 1)
        self.assertIn('p3', cmd)
        self.assertIn('0:a:0', cmd)
        self.assertIn('video.h264', cmd)
        self.assertEqual(cmd[-1], 'audio.ac3')
        self.assertNotIn('copy', cmd)
        silent = make_encode_command('ffmpeg', info, 'video.h264', None, 20000000, False, False)
        self.assertEqual(silent[-1], 'video.h264')
        self.assertIn('medium', silent)

    def test_unavailable_gpu_falls_back_but_cancellation_propagates(self):
        cancel = threading.Event()
        with patch('core.run_command', side_effect=DiscForgeError('driver missing')):
            self.assertFalse(nvenc_available('ffmpeg', cancel, lambda _: None))
            cancel.set()
            with self.assertRaisesRegex(DiscForgeError, 'отменена'):
                nvenc_available('ffmpeg', cancel, lambda _: None)


try:
    from PySide6.QtWidgets import QApplication
    from main import Window, BuildThread
except ImportError:
    Window = None


@unittest.skipIf(Window is None, 'PySide6 not installed')
class BuildUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = patch('main.save_settings')
        self.settings.start()
        self.window = Window()

    def tearDown(self):
        self.window.close()
        self.settings.stop()

    def test_folder_fields_sync_and_project_persists(self):
        w = self.window
        w.output_folder.setText('D:/ISO')
        self.assertEqual(w._config()['output'], 'D:/ISO')
        w.path_fields['output'].setText('E:/Films')
        self.assertEqual(w.output_folder.text(), 'E:/Films')
        self.assertEqual(w._project_data()['output'], 'E:/Films')
        self.assertEqual(w.encoder.currentIndex(), 2)

    def test_time_freezes_on_completion_and_cancel_has_no_false_zero(self):
        w = self.window
        with patch('main.time.monotonic', return_value=110):
            w.build_started = 100
            w._timing_update('Создание ISO', 20)
            self.assertIn('00:00:10', w.elapsed_label.text())
            self.assertIn('00:00:20', w.remaining_label.text())
            w._build_finished(False)
            self.assertFalse(w.build_clock.isActive())
            self.assertEqual(w.remaining_label.text(), 'Осталось: —')
            w._build_finished(True)
            self.assertEqual(w.remaining_label.text(), 'Осталось: 00:00:00')

    def test_queue_progress_does_not_reset_at_second_movie(self):
        info = MediaInfo('movie.mkv', 10, 100, 'h264', 1920, 1080, '24/1', '', 'aac', 2, 1, 0)
        worker = BuildThread([(info, [0]), (info, [0])], {'output': 'out', 'ffmpeg': 'ffmpeg', 'tsmuxer': 'tsmuxer'}, 'BD25', True, False)
        values, success = [], []
        worker.progress_changed.connect(values.append)
        worker.finished_all.connect(success.append)
        def build(*args, **kwargs):
            for value in (0, 45, 90, 100):
                args[9](value)
            return 'out/movie.iso'
        with patch('main.create_bluray', side_effect=build):
            worker.run()
        self.assertEqual(success, [True])
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)
