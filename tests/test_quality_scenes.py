from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import MediaInfo
from quality_trial import TrialPlan
from quality_scenes import (complexity, scan_starts, choose_scenes, select_scenes,
                            recommend_scenes, run_auto_trial, SCAN_WIDTH, SCAN_HEIGHT)


def candidate(start, motion=10, detail=10, dark=10, length=5):
    return dict(start=start, duration=length, reason='детали', features=dict(motion=motion, detail=detail,
                dark_detail=dark, brightness=60, contrast=30))


def scene(values=(98, 98, 98), start=0, count=24):
    variants = []
    for rate, value in zip((5, 8, 12), values):
        variants.append(dict(bitrate=rate*1_000_000, bytes=1000, film_video_bytes=100000,
            seconds=.1, path='unused', quality={
            'vmaf': dict(frames=count, mean=value, p5=value-1, minimum=value-2, worst_frame=7),
            'ssim': dict(frames=count, mean=.997, p5=.995, minimum=.994, worst_frame=9)}))
    return dict(start=start, duration=1, frames=count, encoder='CPU x264', preset='medium',
                cuda=False, variants=variants, source=('unused', 4, 1), reference='unused',
                backend=dict(nvenc=False, cuda=False, vmaf=True))


class SelectionTests(unittest.TestCase):
    def test_scan_is_bounded_and_covers_whole_timeline(self):
        starts = scan_starts(7200, 10)
        self.assertEqual(len(starts), 24)
        self.assertEqual(starts[0], 0)
        self.assertEqual(starts[-1], 7190)
        self.assertEqual(scan_starts(2, 10), [0])
        for duration in (0, .5, float('inf')):
            with self.assertRaises(ValueError):
                scan_starts(duration, 10)

    def test_content_statistics_detect_motion_and_texture_not_flat_black(self):
        size = SCAN_WIDTH*SCAN_HEIGHT
        flat = complexity(bytes([0])*size*4)
        texture = bytes(30 if (x//4+y//4)%2 else 70
                        for y in range(SCAN_HEIGHT) for x in range(SCAN_WIDTH))
        still = complexity(texture*4)
        moving = complexity(texture+bytes(100-x for x in texture)+texture+bytes(100-x for x in texture))
        self.assertEqual(flat['detail'], 0)
        self.assertGreater(still['dark_detail'], 5)
        self.assertEqual(still['motion'], 0)
        self.assertGreater(moving['motion'], 20)
        # One isolated cut does not outweigh persistent motion.
        cut = complexity(bytes([20])*size*3+bytes([200])*size)
        self.assertEqual(cut['motion'], 0)
        with self.assertRaises(ValueError):
            complexity(b'incomplete')

    def test_selects_different_content_categories_without_overlap(self):
        candidates = [candidate(0, 100, 5, 1), candidate(1, 99, 80, 20),
                      candidate(10, 1, 100, 5), candidate(20, 2, 5, 100)]
        chosen = choose_scenes(candidates, 3, 30)
        self.assertEqual([c['start'] for c in chosen], [0, 10, 20])
        self.assertEqual([c['reason'] for c in chosen], ['движение', 'мелкие детали', 'детали в тенях'])

    def test_short_source_does_not_repeat_or_overlap_scenes(self):
        chosen = choose_scenes([candidate(i, length=4) for i in range(5)], 5, 8)
        self.assertEqual(len(chosen), 2)
        self.assertGreaterEqual(chosen[1]['start'], chosen[0]['start']+chosen[0]['duration'])

    def test_flat_candidates_do_not_produce_confident_selection(self):
        with self.assertRaisesRegex(ValueError, 'вручную'):
            choose_scenes([candidate(0, detail=0)], 3, 10)

    def test_cancelled_scan_does_not_start_decoder(self):
        cancel = threading.Event()
        cancel.set()
        info = SimpleNamespace(duration=100, path='movie')
        with patch('quality_scenes.capture') as capture:
            with self.assertRaisesRegex(RuntimeError, 'отменена'):
                select_scenes(info, TrialPlan(0, 5, (5_000_000,)), 'ffmpeg', cancel, lambda _: None, lambda _: None)
            capture.assert_not_called()


class AggregateTests(unittest.TestCase):
    def test_common_rate_is_lowest_passing_every_scene(self):
        scenes = [scene((98, 98, 98)), scene((95, 98, 98), 10), scene((95, 96, 98), 20)]
        result = recommend_scenes(scenes)
        self.assertEqual(result['bitrate'], 12_000_000)
        self.assertEqual(result['eligible'], [12_000_000])
        self.assertEqual(result['status'], 'recommended')

    def test_max_of_individual_recommendations_is_not_safe(self):
        # First passes only 5; second passes only 8. Max(5,8) fails the first.
        result = recommend_scenes([scene((99, 96, 96)), scene((96, 99, 96), 10)])
        self.assertEqual([r['bitrate'] for r in result['per_scene']], [5_000_000, 8_000_000])
        self.assertEqual(result['status'], 'below_target')
        self.assertIsNone(result['bitrate'])

    def test_bad_scene_cannot_be_hidden_by_averaging(self):
        scenes = [scene((99, 99, 99)) for _ in range(4)]+[scene((80, 80, 80))]
        self.assertIsNone(recommend_scenes(scenes)['bitrate'])

    def test_missing_vmaf_downgrades_every_scene_to_same_scale(self):
        scenes = [scene((80, 80, 80)), scene()]
        scenes[1]['variants'][0]['quality']['vmaf'] = None
        result = recommend_scenes(scenes)
        self.assertEqual(result['status'], 'provisional')
        self.assertEqual(result['bitrate'], 5_000_000)
        self.assertTrue(all(a['method'] == 'ssim' for a in result['per_scene']))

    def test_missing_metrics_different_rates_and_mixed_backends_block_advice(self):
        for damage in ('ssim', 'frame_count', 'rates', 'encoder', 'cuda'):
            scenes = [scene(), scene()]
            if damage == 'ssim':
                scenes[1]['variants'][1]['quality']['ssim'] = None
            elif damage == 'frame_count':
                scenes[1]['frames'] = 25
            elif damage == 'rates':
                scenes[1]['variants'].pop()
            elif damage == 'encoder':
                scenes[1]['encoder'] = 'NVIDIA NVENC'
            else:
                scenes[1]['cuda'] = True
            self.assertEqual(recommend_scenes(scenes)['status'], 'unavailable', damage)

    def test_scene_lengths_can_differ_but_all_frames_must_be_scored(self):
        self.assertEqual(recommend_scenes([scene(), scene(count=12)])['status'], 'recommended')


class BatchTests(unittest.TestCase):
    def run_batch(self, temp, cancel, **kwargs):
        source = Path(temp)/'source.mkv'
        source.write_bytes(b'film')
        info = MediaInfo(str(source), 60, 4, 'h264', 640, 360, '25/1', '', '', 0, 0, 0)
        return run_auto_trial(info, TrialPlan(0, 1, (5_000_000, 8_000_000, 12_000_000), 0, automatic=True),
            dict(ffmpeg='ffmpeg'), Path(temp)/'batch', cancel, lambda _: None, kwargs.get('progress', lambda _: None))

    def test_batch_reuses_backend_and_writes_report_only_after_completion(self):
        selection = dict(selected=[candidate(0, length=1), candidate(10, length=1)])
        progress = []
        with tempfile.TemporaryDirectory() as temp, patch('quality_scenes.select_scenes', return_value=selection), \
             patch('quality_scenes.run_trial', side_effect=[scene(), scene(start=10)]) as run:
            result = self.run_batch(temp, threading.Event(), progress=progress.append)
            self.assertEqual(len(result['scenes']), 2)
            self.assertIsNone(run.call_args_list[0].kwargs['backend'])
            self.assertEqual(run.call_args_list[1].kwargs['backend'], scene()['backend'])
            self.assertEqual(run.call_args_list[1].args[1].start, 10)
            self.assertTrue((Path(temp)/'batch/scenes.json').exists())
            self.assertEqual(progress[-1], 100)

    def test_failure_does_not_publish_partial_recommendation(self):
        selection = dict(selected=[candidate(0, length=1), candidate(10, length=1)])
        with tempfile.TemporaryDirectory() as temp, patch('quality_scenes.select_scenes', return_value=selection), \
             patch('quality_scenes.run_trial', side_effect=[scene(), RuntimeError('broken')]):
            with self.assertRaisesRegex(RuntimeError, 'broken'):
                self.run_batch(temp, threading.Event())
            self.assertFalse((Path(temp)/'batch/scenes.json').exists())

    def test_cancellation_between_scenes_stops_without_partial_advice(self):
        selection = dict(selected=[candidate(0, length=1), candidate(10, length=1)])
        cancel = threading.Event()
        def first(*args, **kwargs):
            cancel.set()
            return scene()
        with tempfile.TemporaryDirectory() as temp, patch('quality_scenes.select_scenes', return_value=selection), \
             patch('quality_scenes.run_trial', side_effect=first) as run:
            with self.assertRaisesRegex(RuntimeError, 'отменена'):
                self.run_batch(temp, cancel)
            self.assertEqual(run.call_count, 1)
            self.assertFalse((Path(temp)/'batch/scenes.json').exists())

    def test_source_change_between_scenes_stops_batch(self):
        selection = dict(selected=[candidate(0, length=1), candidate(10, length=1)])
        with tempfile.TemporaryDirectory() as temp:
            def mutate(*args, **kwargs):
                (Path(temp)/'source.mkv').write_bytes(b'changed')
                return scene()
            with patch('quality_scenes.select_scenes', return_value=selection), patch('quality_scenes.run_trial', side_effect=mutate) as run:
                with self.assertRaisesRegex(ValueError, 'изменился'):
                    self.run_batch(temp, threading.Event())
                self.assertEqual(run.call_count, 1)

    def test_disk_reserve_covers_all_scenes_before_encoding(self):
        selection = dict(selected=[candidate(0, length=30), candidate(40, length=30)])
        with tempfile.TemporaryDirectory() as temp, patch('quality_scenes.select_scenes', return_value=selection), \
             patch('quality_scenes.shutil.disk_usage', return_value=SimpleNamespace(free=4_000_000_000)), \
             patch('quality_scenes.run_trial') as run:
            with self.assertRaisesRegex(ValueError, 'всех сцен'):
                self.run_batch(temp, threading.Event())
            run.assert_not_called()


class SceneDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_scene_switch_keeps_global_recommendation_and_current_rate(self):
        from quality_dialog import QualityDialog
        info = MediaInfo('a', 60, 4, 'h264', 640, 360, '25/1', '', '', 0, 0, 0)
        dialog = QualityDialog(info, {'output': tempfile.gettempdir()}, 8_000_000)
        scenes = [scene((98, 98, 98)), scene((94, 95, 98), 10)]
        result = dict(scenes=scenes, recommendation=recommend_scenes(scenes))
        dialog.result_plan = dialog._plan()
        dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: False)
        try:
            self.assertTrue(dialog._plan().automatic)
            self.assertFalse(dialog.start.isEnabled())
            with patch.object(dialog, '_show_frame'):
                dialog._completed(result)
                self.assertEqual(dialog.table.currentRow(), 2)
                dialog.table.selectRow(0)
                dialog.scene_results.setCurrentIndex(1)
                self.assertEqual(dialog.table.currentRow(), 0)
                self.assertEqual(dialog.result['start'], 10)
                self.assertEqual(dialog.result['recommendation']['bitrate'], 12_000_000)
                dialog.scene_results.setCurrentIndex(0)
                dialog._weak_scene()
                self.assertEqual(dialog.scene_results.currentIndex(), 1)
                self.assertEqual(dialog.slider.value(), 7)
                dialog._select_recommended()
                self.assertEqual(dialog.table.currentRow(), 2)
                dialog.scene_count.setValue(4)
                self.assertFalse(dialog.apply_button.isEnabled())
                dialog.mode.setCurrentIndex(1)
                self.assertTrue(dialog.start.isEnabled())
                self.assertFalse(dialog._plan().automatic)
        finally:
            dialog.reject()
            dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
