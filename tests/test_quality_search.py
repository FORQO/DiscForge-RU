from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import MediaInfo
from quality_trial import TrialPlan, source_stamp, extend_trial
from quality_metrics import recommend
from quality_scenes import recommend_scenes
from quality_search import next_rate, run_quality_search, MAX_EXTRA_ROUNDS


def variant(rate, threshold):
    score = 98. if rate >= threshold else 90.
    return dict(bitrate=rate, path='unused', seconds=.1, bytes=1000, film_video_bytes=10000,
        quality=dict(vmaf=dict(frames=24, mean=score, p5=score-1, minimum=score-2, worst_frame=0),
                     ssim=dict(frames=24, mean=.997, p5=.995, minimum=.993, worst_frame=0)))


class AdaptivePolicyTests(unittest.TestCase):
    def test_raises_to_ceiling_without_repeats(self):
        rates = [5_000_000]
        advice = dict(status='below_target', bitrate=None)
        while (rate := next_rate(rates, advice, 24_700_000)) is not None:
            self.assertNotIn(rate, rates)
            self.assertLessEqual(rate, 24_700_000)
            rates.append(rate)
        self.assertEqual(rates[-1], 24_700_000)
        self.assertLessEqual(len(rates)-1, MAX_EXTRA_ROUNDS)

    def test_refines_only_an_untested_bracket(self):
        advice = dict(status='recommended', bitrate=17_000_000)
        self.assertEqual(next_rate([5_000_000, 12_000_000, 17_000_000], advice, 28_000_000), 14_500_000)
        self.assertIsNone(next_rate([16_500_000, 17_000_000], advice, 28_000_000))
        self.assertIsNone(next_rate([17_000_000], advice, 28_000_000))
        self.assertIsNone(next_rate([5_000_000], dict(status='unavailable'), 28_000_000))
        self.assertEqual(next_rate([5_100_000, 5_700_000],
            dict(status='recommended', bitrate=5_700_000), 28_000_000), 5_400_000)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'film.mkv'
        self.source.write_bytes(b'film')
        self.info = MediaInfo(str(self.source), 100, 4, 'h264', 640, 360, '25/1', '', '', 0, 0, 0)
        self.plan = TrialPlan(0, 1, (5_000_000, 8_000_000, 12_000_000), 0, retry_rates=True)
        self.cancel = threading.Event()

    def scene(self, threshold=15_000_000, start=0):
        variants = [variant(r, threshold) for r in self.plan.rates]
        return dict(source=source_stamp(self.source), reference=str(self.root/'reference.mkv'),
                    frames=24, duration=1, start=start, encoder='CPU x264', preset='medium',
                    cuda=False, peak=30_000_000, variants=variants, threshold=threshold,
                    backend=dict(nvenc=False, cuda=False, vmaf=True), recommendation=recommend(variants))

    def extend(self, info, plan, config, scene, rate, cancel, log, progress):
        values = sorted(scene['variants']+[variant(rate, scene['threshold'])], key=lambda v: v['bitrate'])
        progress(100)
        return dict(scene, variants=values, recommendation=recommend(values))

    def run_search(self, plan=None, progress=lambda _: None):
        return run_quality_search(self.info, plan or self.plan, {}, self.root, self.cancel, lambda _: None, progress)

    def test_raise_then_refine_keeps_all_results_and_reports(self):
        initial = self.scene()
        original = deepcopy(initial)
        progress = []
        with patch('quality_search.run_trial', return_value=initial) as first, \
             patch('quality_search.extend_trial', side_effect=self.extend) as extend:
            result = self.run_search(progress=progress.append)
        self.assertEqual(first.call_count, 1)
        self.assertGreaterEqual(result['recommendation']['bitrate'], 15_000_000)
        self.assertLessEqual(result['recommendation']['bitrate'], 15_500_000)
        new_rates = [call.args[4] for call in extend.call_args_list]
        self.assertEqual(len(new_rates), len(set(new_rates)))
        self.assertTrue(all(r not in self.plan.rates for r in new_rates))
        self.assertEqual(initial, original)
        self.assertEqual(len(result['variants']), 3+len(new_rates))
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)
        self.assertTrue((self.root/'search.json').is_file())

    def test_multi_scene_search_retests_same_new_rate_on_every_scene(self):
        scenes = [self.scene(15_000_000), self.scene(17_500_000, 20)]
        initial = dict(source=source_stamp(self.source), scenes=scenes, recommendation=recommend_scenes(scenes))
        with patch('quality_search.run_auto_trial', return_value=initial), \
             patch('quality_search.extend_trial', side_effect=self.extend) as extend:
            result = self.run_search(replace(self.plan, automatic=True))
        self.assertGreaterEqual(result['recommendation']['bitrate'], 17_500_000)
        self.assertLessEqual(result['recommendation']['bitrate'], 18_000_000)
        calls = extend.call_args_list
        self.assertEqual(len(calls)%2, 0)
        for a, b in zip(calls[::2], calls[1::2]):
            self.assertEqual(a.args[4], b.args[4])
            self.assertEqual((a.args[3]['start'], b.args[3]['start']), (0, 20))
        self.assertEqual([v['bitrate'] for v in result['scenes'][0]['variants']],
                         [v['bitrate'] for v in result['scenes'][1]['variants']])

    def test_no_retry_when_initial_passes_or_metrics_missing(self):
        for missing in (False, True):
            initial = self.scene(5_000_000)
            if missing:
                initial['variants'][0]['quality']['ssim'] = None
                initial['recommendation'] = recommend(initial['variants'])
            with patch('quality_search.run_trial', return_value=initial), patch('quality_search.extend_trial') as extend:
                result = self.run_search()
            extend.assert_not_called()
            self.assertFalse(result['search']['triggered'])

    def test_disabled_search_keeps_original_workflow(self):
        with patch('quality_search.run_trial', return_value=self.scene()) as run, patch('quality_search.extend_trial') as extend:
            result = self.run_search(replace(self.plan, retry_rates=False))
            self.assertNotIn('search', result)
            self.assertEqual(run.call_count, 1)
            extend.assert_not_called()

    def test_audio_ceiling_stops_with_honest_failure(self):
        with patch('quality_search.run_trial', return_value=self.scene(40_000_000)), \
             patch('quality_search.video_peak', return_value=24_720_000), \
             patch('quality_search.extend_trial', side_effect=self.extend):
            result = self.run_search()
        self.assertIsNone(result['recommendation']['bitrate'])
        self.assertEqual(max(v['bitrate'] for v in result['variants']), 24_700_000)
        self.assertIn('предел 24.7', result['search']['stop'])

    def test_incomplete_new_metric_stops_instead_of_raising_again(self):
        def broken(*args):
            result = self.extend(*args)
            result['variants'][-1]['quality']['ssim'] = None
            result['recommendation'] = recommend(result['variants'])
            return result
        with patch('quality_search.run_trial', return_value=self.scene()), \
             patch('quality_search.extend_trial', side_effect=broken) as extend:
            result = self.run_search()
        self.assertEqual(extend.call_count, 1)
        self.assertEqual(result['recommendation']['status'], 'unavailable')
        self.assertIsNone(result['recommendation']['bitrate'])

    def test_low_space_preserves_completed_trials(self):
        with patch('quality_search.run_trial', return_value=self.scene()), \
             patch('quality_search.shutil.disk_usage', return_value=SimpleNamespace(free=0)), \
             patch('quality_search.extend_trial') as extend:
            result = self.run_search()
        extend.assert_not_called()
        self.assertEqual(len(result['variants']), 3)
        self.assertIn('свободного места', result['search']['stop'])

    def test_cancel_during_round_does_not_publish_partial_batch(self):
        scenes = [self.scene(), self.scene(start=20)]
        original = deepcopy(scenes)
        initial = dict(source=source_stamp(self.source), scenes=scenes, recommendation=recommend_scenes(scenes))
        def cancel_after_first(*args):
            self.cancel.set()
            return self.extend(*args)
        with patch('quality_search.run_auto_trial', return_value=initial), \
             patch('quality_search.extend_trial', side_effect=cancel_after_first) as extend:
            with self.assertRaisesRegex(RuntimeError, 'отменена'):
                self.run_search(replace(self.plan, automatic=True))
        self.assertEqual(extend.call_count, 1)
        self.assertEqual(scenes, original)
        self.assertFalse((self.root/'search.json').exists())

    def test_source_changes_abort_before_additional_encode(self):
        initial = self.scene()
        self.source.write_bytes(b'changed')
        with patch('quality_search.run_trial', return_value=initial), patch('quality_search.extend_trial') as extend:
            with self.assertRaisesRegex(ValueError, 'изменился'):
                self.run_search()
            extend.assert_not_called()

    def test_retry_budget_is_enforced(self):
        with patch('quality_search.run_trial', return_value=self.scene()), \
             patch('quality_search.extend_trial', side_effect=self.extend) as extend, \
             patch('quality_search.MAX_EXTRA_ROUNDS', 2):
            result = self.run_search()
        self.assertEqual(extend.call_count, 2)
        self.assertIn('предел автоматического поиска', result['search']['stop'])

    def test_extension_uses_retained_frames_and_encoder_not_source(self):
        initial = self.scene()
        def encode(command, *args):
            self.assertEqual(command[command.index('-i')+1], initial['reference'])
            self.assertEqual(command[command.index('-c:v')+1], 'libx264')
            self.assertEqual(command[command.index('-preset')+1], 'medium')
            self.assertEqual(command[command.index('-frames:v')+1], '24')
            Path(command[-1]).write_bytes(b'encoded')
        with patch('quality_trial.run_command', side_effect=encode), \
             patch('quality_trial.inspect_clip', return_value=24), \
             patch('quality_metrics.score_variant', return_value=variant(17_000_000, 15_000_000)['quality']):
            result = extend_trial(self.info, self.plan, {'ffmpeg':'ffmpeg','ffprobe':'ffprobe'}, initial,
                                  17_000_000, self.cancel, lambda _: None, lambda _: None)
        self.assertEqual(len(result['variants']), 4)
        self.assertEqual(len(initial['variants']), 3)


if __name__ == '__main__':
    unittest.main()
