import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import run_command
from quality_metrics import (parse_ssim, parse_vmaf, summarize, metric_command,
                             recommend, score_variant, has_vmaf)


def variant(rate, mean=98., p5=95., minimum=90., ssim=.996):
    return dict(bitrate=rate*1_000_000, quality={
        'vmaf': dict(frames=100, mean=mean, p5=p5, minimum=minimum, worst_frame=17),
        'ssim': dict(frames=100, mean=ssim, p5=ssim-.002, minimum=ssim-.003, worst_frame=23)})


class RecommendationTests(unittest.TestCase):
    def test_smallest_good_rate_near_best_is_selected(self):
        result = recommend([variant(5, 95., 92.), variant(8, 97.5, 94.5), variant(12, 98., 96.)])
        self.assertEqual(result['bitrate'], 8_000_000)
        self.assertEqual(result['eligible'], [8_000_000, 12_000_000])
        self.assertEqual(result['status'], 'recommended')

    def test_no_automatic_preference_for_highest_rate_and_order_independent(self):
        values = [variant(5, 98.7, 96.), variant(8, 98.6, 95.9), variant(12, 98.5, 95.8)]
        self.assertEqual(recommend(values[::-1])['bitrate'], 5_000_000)

    def test_mean_alone_cannot_hide_a_bad_frame(self):
        result = recommend([variant(5, 99., 96., 70.), variant(8, 99., 96., 90.)])
        self.assertEqual(result['bitrate'], 8_000_000)
        self.assertNotIn(5_000_000, result['eligible'])

    def test_p5_and_ssim_are_also_gates(self):
        a, b, c = variant(5, 99., 89.), variant(8, 99.), variant(12, 99.)
        b['quality']['ssim']['minimum'] = .8
        self.assertEqual(recommend([a, b, c])['bitrate'], 12_000_000)

    def test_none_pass_no_optimal_claim(self):
        result = recommend([variant(5, 89.), variant(8, 92.), variant(12, 94.)])
        self.assertIsNone(result['bitrate'])
        self.assertEqual(result['best_bitrate'], 12_000_000)
        self.assertEqual(result['status'], 'below_target')
        self.assertIn('Цель качества не достигнута', result['explanation'])

    def test_missing_vmaf_downgrades_all_to_ssim(self):
        values = [variant(5, ssim=.993), variant(8, ssim=.997), variant(12, ssim=.9975)]
        values[0]['quality']['vmaf'] = None
        result = recommend(values)
        self.assertEqual(result['status'], 'provisional')
        self.assertEqual(result['method'], 'ssim')
        self.assertEqual(result['bitrate'], 8_000_000)
        self.assertIn('достоверность', result['explanation'])

    def test_partial_ssim_or_different_counts_block_advice(self):
        for mutation in ('missing', 'count', 'nan'):
            values = [variant(5), variant(8)]
            if mutation == 'missing':
                values[0]['quality']['ssim'] = None
            elif mutation == 'count':
                values[0]['quality']['ssim']['frames'] = 99
            else:
                values[0]['quality']['ssim']['mean'] = float('nan')
            self.assertEqual(recommend(values)['status'], 'unavailable')
            self.assertIsNone(recommend(values)['bitrate'])
        self.assertEqual(recommend([])['status'], 'unavailable')


class MetricParsingTests(unittest.TestCase):
    def test_summary_preserves_bad_tail_and_frame_index(self):
        values = [99.]*100
        values[47] = 72.
        values[19:24] = [85.]*5
        result = summarize(values, 0, 100)
        self.assertEqual(result['minimum'], 72.)
        self.assertEqual(result['p5'], 85.)
        self.assertEqual(result['worst_frame'], 47)
        self.assertEqual(result['frames'], 100)

    def test_vmaf_rejects_incomplete_nonfinite_and_misaligned_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'vmaf.json'
            rows = [{'frameNum': i, 'metrics': {'vmaf': v}} for i, v in enumerate((99., 91., 97.))]
            path.write_text(json.dumps({'frames': rows}), encoding='utf-8')
            self.assertEqual(parse_vmaf(path, 3)['worst_frame'], 1)
            with self.assertRaises(ValueError):
                parse_vmaf(path, 4)
            rows[1]['frameNum'] = 0
            path.write_text(json.dumps({'frames': rows}), encoding='utf-8')
            with self.assertRaises(ValueError):
                parse_vmaf(path, 3)
            rows[1]['frameNum'] = 1
            rows[1]['metrics']['vmaf'] = float('nan')
            path.write_text(json.dumps({'frames': rows}), encoding='utf-8')
            with self.assertRaises(ValueError):
                parse_vmaf(path, 3)

    def test_ssim_parses_identical_frames_and_rejects_missing_or_bad_data(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'ssim.log'
            path.write_text('n:1 Y:1.000000 U:1.000000 V:1.000000 All:1.000000 (inf)\n'
                            'n:2 Y:0.990000 U:1.000000 V:1.000000 All:0.995000 (23.0)\n')
            result = parse_ssim(path, 2)
            self.assertAlmostEqual(result['mean'], .9975)
            self.assertEqual(result['worst_frame'], 1)
            for bad in ('n:2 Y:1 All:1\n', 'n:1 Y:1 All:nan\n', 'n:1 Y:1 All:1.01\n', ''):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    parse_ssim(path, 1)

    def test_metric_command_uses_exact_clock_and_safe_paths(self):
        for method in ('ssim', 'vmaf'):
            command = metric_command('ffmpeg', "исходник's [1].mkv", "проба,1.mkv", 47, method)
            graph = command[command.index('-filter_complex')+1]
            self.assertIn('settb=1/24000,setpts=N*1001', graph)
            self.assertIn('shortest=1:repeatlast=0', graph)
            self.assertNotIn('исходник', graph)
            self.assertNotIn('проба', graph)
            self.assertTrue(command[command.index('-i')+1].endswith('проба,1.mkv'))

    def test_detection_is_not_confused_by_similarly_named_filters(self):
        with patch('quality_trial.capture', return_value=b' .. libvmaf VV->V quality\n'):
            self.assertTrue(has_vmaf('ffmpeg', threading.Event()))
        with patch('quality_trial.capture', return_value=b' .. libvmaf_cuda VV->V quality\n'):
            self.assertFalse(has_vmaf('ffmpeg', threading.Event()))

    def test_metric_failure_does_not_fabricate_a_score(self):
        with tempfile.TemporaryDirectory() as temp, patch('quality_metrics.run_command', side_effect=RuntimeError('no model')):
            result = score_variant('ffmpeg', 'ref', 'sample', 24, temp, threading.Event(), lambda _: None, lambda _: None)
            self.assertIsNone(result['vmaf'])
            self.assertIsNone(result['ssim'])
            self.assertEqual(len(result['warnings']), 2)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_cancel_is_not_converted_into_missing_metrics(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as temp, patch('quality_metrics.run_command') as run:
            with self.assertRaisesRegex(RuntimeError, 'отменена'):
                score_variant('ffmpeg', 'ref', 'sample', 24, temp, cancel, lambda _: None, lambda _: None)
            run.assert_not_called()

    def test_child_report_directory_does_not_change_parent_cwd(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="DiskForge 'тест, ") as temp:
            run_command([sys.executable, '-c', "from pathlib import Path; Path('metric.txt').write_text('ok')"],
                        'test', 0, 1, 1, threading.Event(), lambda _: None, lambda _: None, cwd=temp)
            self.assertEqual((Path(temp)/'metric.txt').read_text(), 'ok')
            self.assertEqual(Path.cwd(), original)


class MetricDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_recommended_selection_worst_frame_and_stale_controls(self):
        from core import MediaInfo
        from quality_dialog import QualityDialog
        from types import SimpleNamespace
        info = MediaInfo('movie.mkv', 60, 100, 'h264', 1920, 1080, '24/1', '', '', 0, 0, 0)
        dialog = QualityDialog(info, {'output': tempfile.gettempdir()}, 8_000_000)
        dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: False)
        values = [variant(5, 95., 92.), variant(8, 97.5, 94.5), variant(12, 98., 96.)]
        for value in values:
            value.update(bytes=1_000_000, film_video_bytes=100_000_000, seconds=1.)
        result = dict(variants=values, recommendation=recommend(values), frames=100,
                      encoder='CPU x264', preset='medium', duration=4.17)
        dialog.result_plan = dialog._plan()
        try:
            with patch.object(dialog, '_show_frame'):
                dialog._completed(result)
                self.assertEqual(dialog.table.currentRow(), 1)
                self.assertTrue(dialog.pick_recommended.isEnabled())
                self.assertTrue(dialog.apply_button.isEnabled())
                self.assertIn('8 Мбит/с', dialog.advice_label.text())
                dialog.table.selectRow(0)
                dialog._select_recommended()
                self.assertEqual(dialog.table.currentRow(), 1)
                dialog._worst_frame()
                self.assertEqual(dialog.slider.value(), 17)
                dialog.duration.setValue(20)
                self.assertFalse(dialog.pick_recommended.isEnabled())
                self.assertFalse(dialog.apply_button.isEnabled())
                self.assertIsNone(dialog.selected_rate)  # recommendation alone never changes the project
        finally:
            dialog.reject()
            dialog.deleteLater()

    def test_no_passing_trial_does_not_enable_recommendation_button(self):
        from core import MediaInfo
        from quality_dialog import QualityDialog
        from types import SimpleNamespace
        dialog = QualityDialog(MediaInfo('a', 60, 1, 'h264', 1920, 1080, '24/1', '', '', 0, 0, 0),
                               {'output': tempfile.gettempdir()}, 8_000_000)
        dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: False)
        values = [variant(5, 85.), variant(8, 88.)]
        for value in values:
            value.update(bytes=1_000_000, film_video_bytes=100_000_000, seconds=1.)
        dialog.result_plan = dialog._plan()
        try:
            with patch.object(dialog, '_show_frame'):
                dialog._completed(dict(variants=values, recommendation=recommend(values), frames=100,
                                       encoder='CPU x264', preset='medium', duration=4.17))
                self.assertFalse(dialog.pick_recommended.isEnabled())
                self.assertTrue(dialog.apply_button.isEnabled())  # informed manual choice remains available
                self.assertIn('не достигнута', dialog.advice_label.text())
        finally:
            dialog.reject()
            dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
