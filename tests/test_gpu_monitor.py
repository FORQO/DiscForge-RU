import os
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QProcess, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from gpu_monitor import GpuMonitor, GpuReading, parse_readings


class ParserTests(unittest.TestCase):
    def test_multiple_cards_and_partial_unsupported_metrics(self):
        readings = parse_readings('0, NVIDIA GeForce RTX 4060, 62, 14, 97\r\n'
                                  '1, "NVIDIA, other", [N/A], 0, Not Supported\n')
        self.assertEqual(len(readings), 2)
        self.assertEqual((readings[0].temperature, readings[0].gpu, readings[0].encoder), (62, 14, 97))
        self.assertEqual((readings[1].temperature, readings[1].gpu, readings[1].encoder), (None, 0, None))

    def test_invalid_rows_and_values_never_become_false_zero(self):
        readings = parse_readings('error\nN/A, GPU, 50, 2, 3\n0, RTX, NaN, 101, -1\n0, duplicate, 44, 3, 4\n')
        self.assertEqual(len(readings), 1)
        self.assertEqual((readings[0].temperature, readings[0].gpu, readings[0].encoder), (None, None, None))
        self.assertFalse(parse_readings(''))


class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.monitor = GpuMonitor()

    def tearDown(self):
        self.monitor.shutdown()
        self.monitor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait_until(self, condition, timeout=4000):
        end = time.monotonic() + timeout / 1000
        while not condition() and time.monotonic() < end:
            QTest.qWait(20)
        self.assertTrue(condition(), self.monitor.status.text())

    def launch(self, script):
        self.patch_program = patch('gpu_monitor.find_nvidia_smi', return_value=sys.executable)
        self.patch_query = patch('gpu_monitor.QUERY', ['-c', script])
        self.patch_program.start()
        self.patch_query.start()
        self.addCleanup(self.patch_program.stop)
        self.addCleanup(self.patch_query.stop)
        self.monitor.start()

    def test_idle_never_polls_and_missing_driver_is_explicit(self):
        self.assertFalse(self.monitor.timer.isActive())
        with patch('gpu_monitor.find_nvidia_smi', return_value=None):
            self.monitor.start()
        self.assertIn('недоступен', self.monitor.status.text())
        self.assertNotIn('0%', self.monitor.values.text())
        self.assertFalse(self.monitor.timer.isActive())

    def test_async_reading_and_stop_retains_clearly_marked_last_sample(self):
        self.launch("print('0, NVIDIA GeForce RTX 4060, 63, 12, 98')")
        self.wait_until(lambda: bool(self.monitor.readings))
        self.assertIn('63 °C', self.monitor.values.text())
        self.assertIn('NVENC: 98%', self.monitor.values.text())
        self.monitor.stop()
        self.assertFalse(self.monitor.timer.isActive())
        self.assertIn('опрос остановлен', self.monitor.status.text())
        self.assertIn('63 °C', self.monitor.values.text())

    def test_timeout_does_not_block_ui_and_clears_stale_measurement(self):
        self.monitor.deadline.setInterval(50)
        self.launch('import time; time.sleep(10)')
        self.wait_until(lambda: self.monitor.timed_out)
        self.wait_until(lambda: self.monitor.process.state() == QProcess.NotRunning)
        self.assertIn('не ответил', self.monitor.status.text())
        self.assertFalse(self.monitor.readings)

    def test_driver_sample_triggers_visible_heat_warning(self):
        with patch('gpu_monitor.QApplication.beep') as beep, patch('gpu_monitor.QApplication.alert'):
            self.launch("print('0, NVIDIA GeForce RTX 4060, 85, 12, 98')")
            self.wait_until(lambda: bool(self.monitor.hot_cards))
            self.assertFalse(self.monitor.heat_warning.isHidden())
            self.assertIn('85 °C', self.monitor.heat_warning.text())
            beep.assert_called_once()

    def test_failure_clears_previous_readings_and_can_recover(self):
        self.launch("print('0, RTX 4060, 55, 2, 78')")
        self.wait_until(lambda: bool(self.monitor.readings))
        with patch('gpu_monitor.QUERY', ['-c', 'import sys; sys.exit(1)']):
            self.monitor.poll()
            self.wait_until(lambda: not self.monitor.readings)
        self.assertIn('недоступны', self.monitor.status.text())
        self.assertNotIn('55', self.monitor.values.text())
        self.monitor.poll()
        self.wait_until(lambda: bool(self.monitor.readings))

    def test_cancel_and_immediate_restart_ignore_old_process(self):
        self.launch("import time; time.sleep(10); print('0, Old GPU, 99, 99, 99')")
        self.wait_until(lambda: self.monitor.process.state() == QProcess.Running)
        with patch('gpu_monitor.QUERY', ['-c', "print('0, RTX 4060, 44, 5, 6')"]):
            self.monitor.start()
            self.wait_until(lambda: self.monitor.process.state() == QProcess.NotRunning)
            self.monitor.poll()
            self.wait_until(lambda: bool(self.monitor.readings))
        self.assertEqual(self.monitor.readings[0].temperature, 44)
        self.assertNotIn('Old GPU', self.monitor.values.text())

    def test_failed_to_start_is_nonfatal(self):
        with patch('gpu_monitor.find_nvidia_smi', return_value='/missing/nvidia-smi'):
            self.monitor.start()
            self.wait_until(lambda: 'не удалось' in self.monitor.status.text())
        self.assertFalse(self.monitor.deadline.isActive())

    def test_window_completion_and_close_stop_monitor(self):
        from main import Window
        with patch('main.load_settings', return_value={}), patch('main.save_settings'):
            window = Window()
            with patch.object(window.gpu_monitor, 'stop') as stop:
                window._build_finished(False)
                stop.assert_called_once()
            with patch.object(window.gpu_monitor, 'shutdown') as shutdown:
                window.close()
                shutdown.assert_called_once()
            window.deleteLater()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)


class HeatWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.monitor = GpuMonitor()
        self.monitor.active = True
        self.messages = []
        self.monitor.temperature_warning.connect(self.messages.append)
        beep = patch('gpu_monitor.QApplication.beep')
        alert = patch('gpu_monitor.QApplication.alert')
        self.beep = beep.start()
        self.alert = alert.start()
        self.addCleanup(beep.stop)
        self.addCleanup(alert.stop)

    def tearDown(self):
        self.monitor.shutdown()
        self.monitor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def sample(self, temperature, index=0):
        return GpuReading(index, 'RTX 4060', temperature, 10, 90)

    def test_threshold_hysteresis_and_one_sound_per_hot_episode(self):
        for temperature in (50, 84):
            self.monitor._update_heat([self.sample(temperature)])
            self.assertTrue(self.monitor.heat_warning.isHidden())
        for temperature in (85, 89, 84, 81):
            self.monitor._update_heat([self.sample(temperature)])
            self.assertFalse(self.monitor.heat_warning.isHidden())
        self.beep.assert_called_once()
        self.alert.assert_called_once()
        self.assertEqual(len(self.messages), 1)
        self.monitor._update_heat([self.sample(80)])
        self.assertTrue(self.monitor.heat_warning.isHidden())
        self.monitor._update_heat([self.sample(85)])
        self.assertEqual(self.beep.call_count, 2)

    def test_missing_sensor_never_means_cooling_and_does_not_retrigger(self):
        self.monitor._update_heat([self.sample(None)])
        self.assertTrue(self.monitor.heat_warning.isHidden())
        self.monitor._update_heat([self.sample(86)])
        self.monitor._unavailable('Driver timeout')
        self.assertIn('датчик недоступен', self.monitor.heat_warning.text())
        self.assertFalse(self.monitor.heat_warning.isHidden())
        self.monitor._update_heat([self.sample(None)])
        self.monitor._update_heat([self.sample(87)])
        self.beep.assert_called_once()
        self.assertNotIn('датчик недоступен', self.monitor.heat_warning.text())

    def test_multiple_cards_have_independent_warnings(self):
        self.monitor._update_heat([self.sample(86), self.sample(60, 1)])
        self.monitor._update_heat([self.sample(80), self.sample(85, 1)])
        self.assertEqual(set(self.monitor.hot_cards), {(1, 'RTX 4060')})
        self.assertIn('GPU 1', self.messages[-1])
        self.assertEqual(self.beep.call_count, 2)
        self.monitor._update_heat([self.sample(70)])
        self.assertIn('датчик недоступен', self.monitor.heat_warning.text())

    def test_stop_marks_warning_as_historical_and_ignores_late_results(self):
        self.monitor._update_heat([self.sample(85)])
        self.monitor.stop()
        self.assertIn('Опрос остановлен', self.monitor.heat_warning.text())
        self.monitor._update_heat([self.sample(90, 1)])
        self.beep.assert_called_once()
        with patch('gpu_monitor.find_nvidia_smi', return_value=None):
            self.monitor.start()
        self.assertFalse(self.monitor.hot_cards)
        self.assertTrue(self.monitor.heat_warning.isHidden())

    def test_window_logs_alert_without_cancelling_build(self):
        from main import Window
        with patch('main.load_settings', return_value={}), patch('main.save_settings'):
            window = Window()
            window.gpu_monitor.active = True
            with patch.object(window, 'cancel_build') as cancel:
                window.gpu_monitor._update_heat([self.sample(85)])
                self.assertIn('Высокая температура GPU', window.log.toPlainText())
                cancel.assert_not_called()
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_build_starts_monitor_automatically(self):
        from main import Window, BuildThread, QMessageBox
        from core import parse_probe
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt
        with patch('main.load_settings', return_value={}), patch('main.save_settings'):
            window = Window()
            item = QListWidgetItem('Film')
            item.setData(Qt.UserRole, 'film.mkv')
            window.movies.addItem(item)
            window.movies.setCurrentRow(0)
            window._scanned('film.mkv', parse_probe('film.mkv', {
                'format': {'duration': '60', 'size': '100'},
                'streams': [{'codec_type': 'video', 'codec_name': 'h264'}]}), '')
            window.output_folder.setText('output')
            with patch('main.QMessageBox.question', return_value=QMessageBox.Yes), patch.object(BuildThread, 'start'), patch.object(window.gpu_monitor, 'start') as start:
                window.start_build()
                start.assert_called_once()
                window.build_thread.finished_all.emit(True)
                self.assertFalse(window.gpu_monitor.timer.isActive())
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
