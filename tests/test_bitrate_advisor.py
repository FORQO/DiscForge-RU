import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem
from bitrate_advisor import recommend, source_rate, analyze_bitrate, normalize_rates, planned_rate
from core import MediaInfo
from smart_copy import CopyReport
from disc_capacity import estimate, require_fit


def movie(name='small.mkv', width=1024, height=426, bps=1800000, codec='h264'):
    return MediaInfo(str(Path(name).resolve()), 9079, 2359583036, codec, width, height, '24000/1001', '', '', 0, 0, 0,
                     video_details={'index': 0, 'bit_rate': str(bps)},
                     copy_report=CopyReport(video_ok=False, reasons=['размер кадра требует преобразования']))


class BitrateAdvisorTests(unittest.TestCase):
    def test_small_rip_gets_five_not_twelve(self):
        advice = recommend(movie())
        self.assertEqual(advice.bitrate, 5000000)
        self.assertEqual(advice.mode, 'encode')
        self.assertIn('возможна потеря', advice.explanation)
        budget = estimate([(movie(), [0])], 'BD25', collection=True, rates={movie().path: advice.bitrate})
        self.assertEqual(budget.video, 5674375000)

    def test_resolution_and_codec_change_recommendation(self):
        self.assertEqual(recommend(movie(width=1920, height=1080, bps=6000000)).bitrate, 8000000)
        self.assertEqual(recommend(movie(width=1920, height=1080, bps=6000000, codec='hevc')).bitrate, 12000000)
        self.assertEqual(recommend(movie(width=1280, height=720, bps=2000000)).bitrate, 6000000)
        capped = recommend(movie(bps=60000000))
        self.assertEqual(capped.bitrate, 28000000)
        self.assertIn('выше предела', capped.explanation)

    def test_video_measurement_never_uses_container_size_or_audio(self):
        unknown = replace(movie(), video_details={}, size=90000000000)
        self.assertEqual(source_rate(unknown)['bps'], 0)
        self.assertEqual(recommend(unknown).bitrate, 8000000)
        self.assertIn('Данных мало', recommend(unknown).explanation)
        tagged = replace(unknown, video_details={'tags': {'BPS': '1700000'}})
        self.assertEqual(source_rate(tagged)['bps'], 1700000)
        self.assertEqual(recommend(tagged).bitrate, 5000000)

    def test_copy_keeps_exact_bytes_even_with_a_lower_manual_rate(self):
        info = replace(movie(), copy_report=CopyReport(video_ok=True, audio_ok=True, video_bytes=8000000000, video_max_bps=15000000))
        self.assertEqual(recommend(info).mode, 'copy')
        budget = estimate([(info, [0])], 'BD25', collection=True, rates={info.path: 5000000})
        self.assertEqual(budget.video, 8000000000)
        with self.assertRaises(ValueError):
            require_fit(estimate([(info, [0])]*4, 'BD25', collection=True, rates={info.path: 5000000}))

    def test_mixed_per_film_rates_and_audio_budget(self):
        first, second = movie(), movie('large.mkv', 1920, 1080, 8000000)
        rates = {m.path: recommend(m).bitrate for m in (first, second)}
        result = estimate([(first, [0]), (second, [0])], 'BD50', collection=True, rates=rates)
        self.assertEqual(result.video, 9079*(5000000+10000000)//8)
        self.assertEqual(planned_rate(first), 5000000)
        self.assertEqual(planned_rate(first, overrides={first.path: 6500000}), 6500000)

    def test_sampling_reads_only_video_and_marks_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'sample.mkv'
            path.write_bytes(b'test')
            info = replace(movie(), path=str(path), video_details={})
            commands = []
            def feed(command, cancel, callback):
                commands.append(command)
                for _ in range(10):
                    callback('size=10000|duration_time=0.040000')
            with patch('smart_copy.stream_lines', side_effect=feed):
                measured = analyze_bitrate(info, 'ffprobe', threading.Event())
            self.assertEqual(measured['method'], 'samples')
            self.assertEqual(measured['bps'], 2000000)
            self.assertEqual(commands[0][commands[0].index('-select_streams')+1], 'v:0')
            self.assertIn('Короткие пробы', recommend(replace(info, video_analysis=measured)).explanation)

    def test_cancellation_and_unknown_sample_failure(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(RuntimeError, 'отменён'):
            analyze_bitrate(movie(), 'ffprobe', cancel)
        info = replace(movie(), path='not-a-real-file.mkv', video_details={})
        self.assertEqual(analyze_bitrate(info, 'ffprobe', threading.Event())['method'], 'unknown')

    def test_rate_validation_rejects_bad_projects(self):
        for rate in (True, 4_000_000, 28_000_001, '5000000', float('nan')):
            with self.assertRaises(ValueError):
                normalize_rates({movie().path: rate}, [movie().path])
        self.assertEqual(normalize_rates({movie().path: 5500000}, [movie().path]), {movie().path: 5500000})


class BitrateWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from main import Window
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.media = [movie(), movie('series.mkv', 1920, 1080, 6000000)]
        for info in self.media:
            item = QListWidgetItem(info.name)
            item.setData(Qt.UserRole, info.path)
            self.w.movies.addItem(item)
            self.w._scanned(info.path, info, '')
        self.w.movies.setCurrentRow(0)

    def tearDown(self):
        self.w.capacity_clock.stop()
        self.w.hide()
        self.w.deleteLater()

    def test_each_movie_automatic_override_and_reset(self):
        self.assertEqual(list(self.w._video_rates().values()), [5000000, 8000000])
        self.assertEqual(self.w.bitrate_table.item(0, 3).text(), '5 Мбит/с')
        self.w.custom_bitrate.setChecked(True)
        self.w.movie_bitrate.setValue(6.5)
        self.assertEqual(self.w._video_rates()[self.media[0].path], 6500000)
        self.w.movies.setCurrentRow(1)
        self.assertFalse(self.w.custom_bitrate.isChecked())
        self.assertEqual(self.w.movie_bitrate.value(), 8)
        self.w._use_recommendation()
        self.assertEqual(self.w._video_rates()[self.media[0].path], 5000000)
        self.assertEqual(self.w.encode_speed.currentIndex(), 1)

    def test_project_roundtrip_and_legacy_preservation(self):
        self.w.custom_bitrate.setChecked(True)
        self.w.movie_bitrate.setValue(6.5)
        data = self.w._project_data()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'project.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(self.w, '_queue_scan'), patch('main.QMessageBox.warning') as warning:
                self.w._load_project(path)
            warning.assert_not_called()
            self.assertEqual(self.w.bitrate_policy.currentData(), 'auto')
            self.assertEqual(self.w.bitrate_overrides[self.media[0].path], 6500000)
            del data['bitrate_policy'], data['bitrate_overrides']
            data['collection_bitrate'] = 12
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(self.w, '_queue_scan'), patch('main.QMessageBox.warning') as warning:
                self.w._load_project(path)
            warning.assert_not_called()
            self.assertEqual(self.w.bitrate_policy.currentData(), 'manual')
            self.assertEqual(self.w.collection_bitrate.value(), 12)

    def test_auto_budget_prevents_start_on_overflow_without_lowering_rates(self):
        self.w.media[self.media[0].path] = replace(self.media[0], duration=40000)
        rates = self.w._video_rates()
        with patch('main.QMessageBox.warning') as warning, patch('main.BuildThread') as worker:
            self.w.start_build()
        warning.assert_called_once()
        worker.assert_not_called()
        self.assertEqual(self.w._video_rates(), rates)
