from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core import MediaInfo
from quality_trial import TrialPlan
from quality_cache import fingerprint, make_record, restore_record, validate_record, load_records
from quality_metrics import recommend


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'film.mkv'
        self.source.write_bytes(b'film')
        self.config = {'output': str(self.root)}
        for key in ('ffmpeg', 'ffprobe'):
            path = self.root/(key+'.exe')
            path.write_bytes(key.encode())
            self.config[key] = str(path)
        self.info = MediaInfo(str(self.source), 100, 4, 'h264', 640, 360, '25/1', '', '', 0, 0, 0)
        self.plan = TrialPlan(0, 1, (5_000_000, 8_000_000), 0)
        self.cancel = threading.Event()
        self.identity = fingerprint(self.info, self.plan, self.config, self.cancel, lambda _: None, lambda _: None)
        metric = dict(frames=23, mean=.997, p5=.995, minimum=.99, worst_frame=3)
        variants = [dict(bitrate=r, path='C:/must-not-open.mkv', bytes=100, seconds=1,
                        film_video_bytes=1000, quality=dict(ssim=metric, vmaf=None, warnings=[])) for r in self.plan.rates]
        self.result = dict(frames=23, start=0, duration=23*1001/24000, encoder='CPU x264', preset='medium',
            cuda=False, peak=30_000_000, backend=dict(nvenc=False, cuda=False, vmaf=False), variants=variants,
            source=self.identity['source']['stamp'], reference='C:/must-not-open-reference.mkv',
            recommendation=recommend(variants))
        self.record = make_record(self.result, self.plan, self.identity)

    def restore(self, record=None, info=None, plan=None, config=None):
        return restore_record(record or self.record, info or self.info, plan or self.plan,
                              config or self.config, self.cancel, lambda _: None, lambda _: None)

    def test_round_trip_contains_scores_not_temporary_video_paths(self):
        encoded = json.dumps(self.record)
        self.assertNotIn('must-not-open', encoded)
        with patch('quality_cache.nvenc_available') as probe, patch('quality_cache.run_command') as encode:
            result = self.restore(json.loads(encoded))
        probe.assert_not_called()
        encode.assert_not_called()
        self.assertEqual(result['recommendation']['bitrate'], 5_000_000)
        self.assertTrue(result['archived'])
        self.assertNotIn('reference', result)
        self.assertTrue(all('path' not in v for v in result['variants']))

    def test_digest_catches_changes_even_if_size_and_mtime_preserved(self):
        stat = self.source.stat()
        self.source.write_bytes(b'FAKE')
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'Содержимое'):
            self.restore()

    def test_metadata_change_rejects_without_full_hash(self):
        self.source.write_bytes(b'changed')
        with patch('quality_cache.fingerprint') as calculate:
            with self.assertRaisesRegex(ValueError, 'изменился'):
                self.restore()
            calculate.assert_not_called()

    def test_settings_audio_and_policy_changes_invalidate(self):
        for changed in (replace(self.plan, fast=True), replace(self.plan, gpu_frames=False),
                        replace(self.plan, enhance=True), replace(self.plan, quiet_rate=1),
                        replace(self.plan, seconds=2), replace(self.plan, rates=(6_000_000,))):
            with self.assertRaisesRegex(ValueError, 'изменились'):
                self.restore(plan=changed)
        with self.assertRaisesRegex(ValueError, 'изменились'):
            self.restore(info=replace(self.info, selected_audio=()))
        with patch('quality_cache.ENGINE', 'next-policy'):
            with self.assertRaisesRegex(ValueError, 'изменились'):
                self.restore()

    def test_updated_binary_invalidates_but_identical_portable_copy_is_valid(self):
        copy = self.root/'other-ffmpeg.exe'
        copy.write_bytes(Path(self.config['ffmpeg']).read_bytes())
        self.assertEqual(self.restore(config=dict(self.config, ffmpeg=str(copy)))['recommendation']['bitrate'], 5_000_000)
        copy.write_bytes(b'update')
        with self.assertRaisesRegex(ValueError, 'инструменты'):
            self.restore(config=dict(self.config, ffmpeg=str(copy)))

    def test_gpu_backend_change_rejects(self):
        plan = replace(self.plan, encoder=2)
        with patch('gpu_monitor.find_nvidia_smi', return_value=None):
            identity = fingerprint(self.info, plan, self.config, self.cancel, lambda _: None, lambda _: None)
            result = dict(self.result, encoder='NVIDIA NVENC', preset='p5', backend=dict(nvenc=True, cuda=False, vmaf=False))
            record = make_record(result, plan, identity)
            with patch('quality_cache.nvenc_available', return_value=False):
                with self.assertRaisesRegex(ValueError, 'Доступность'):
                    self.restore(record=record, plan=plan)

    def test_cancellation_never_returns_reusable_results(self):
        self.cancel.set()
        with self.assertRaisesRegex(RuntimeError, 'отменена'):
            self.restore()

    def test_malformed_optional_reports_are_skipped_not_executed(self):
        for damage in ('missing', 'nan', 'frames', 'rate', 'warnings', 'selected'):
            record = deepcopy(self.record)
            if damage == 'missing':
                del record['identity']
            elif damage == 'nan':
                record['scenes'][0]['variants'][0]['quality']['ssim']['mean'] = float('nan')
            elif damage == 'frames':
                record['scenes'][0]['frames'] = 99
            elif damage == 'rate':
                record['scenes'][0]['variants'][0]['bitrate'] = 100
            elif damage == 'warnings':
                record['scenes'][0]['variants'][0]['quality']['warnings'] = [42]
            else:
                record['selected_rate'] = 50_000_000
            loaded, skipped = load_records({str(self.source): record}, [str(self.source)])
            self.assertEqual(loaded, {}, damage)
            self.assertEqual(skipped, 1, damage)
        self.assertEqual(load_records(None, []), ({}, 0))

    def test_multi_scene_round_trip_recomputes_common_recommendation(self):
        plan = replace(self.plan, automatic=True)
        identity = fingerprint(self.info, plan, self.config, self.cancel, lambda _: None, lambda _: None)
        result = dict(scenes=[self.result, dict(self.result, start=20)], recommendation=self.result['recommendation'])
        record = make_record(result, plan, identity)
        restored = self.restore(record=record, plan=plan)
        self.assertEqual(len(restored['scenes']), 2)
        self.assertTrue(all(s['archived'] for s in restored['scenes']))
        self.assertEqual(restored['recommendation']['bitrate'], 5_000_000)

    def test_gui_restores_and_applies_without_decoding_or_encoding(self):
        from PySide6.QtWidgets import QApplication
        from quality_dialog import QualityDialog
        app = QApplication.instance() or QApplication([])
        dialog = QualityDialog(self.info, self.config, 12_000_000, encoder=0, saved_record=self.record)
        try:
            with patch('quality_dialog.FrameWorker') as decoder, patch('quality_dialog.run_quality_search') as encode:
                end = time.monotonic()+10
                while time.monotonic() < end:
                    app.processEvents()
                    if dialog.result and not dialog._busy():
                        break
                    time.sleep(.01)
                self.assertTrue(dialog.result and dialog.result.get('archived'), dialog.status.text())
                self.assertTrue(dialog.apply_button.isEnabled())
                self.assertFalse(dialog.play.isEnabled())
                self.assertFalse(dialog.archive_note.isHidden())
                self.assertIsNone(dialog.temporary)
                dialog.table.selectRow(1)
                dialog._apply()
                self.assertEqual(dialog.selected_rate, 8_000_000)
                self.assertEqual(dialog.saved_record['selected_rate'], 8_000_000)
                decoder.assert_not_called()
                encode.assert_not_called()
        finally:
            dialog.reject()
            if dialog.worker:
                dialog.worker.wait(5000)
            dialog.deleteLater()
            app.processEvents()

    def test_project_round_trip_remove_and_new_project(self):
        from PySide6.QtWidgets import QApplication, QListWidgetItem
        from PySide6.QtCore import Qt
        from main import Window
        app = QApplication.instance() or QApplication([])
        with patch('main.load_settings', return_value={}):
            window = Window()
        try:
            item = QListWidgetItem('film')
            item.setData(Qt.UserRole, str(self.source))
            window.movies.addItem(item)
            window.quality_records[str(self.source)] = self.record
            data = window._project_data()
            project = self.root/'project.dfr.json'
            project.write_text(json.dumps(data), encoding='utf-8')
            window.new_project()
            self.assertEqual(window.quality_records, {})
            with patch.object(window, '_queue_scan'):
                window._load_project(str(project))
            self.assertEqual(window.quality_records[str(self.source)], self.record)
            window.new_project()
            # Legacy projects without the optional section remain valid.
            data.pop('quality_trials')
            project.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(window, '_queue_scan'):
                window._load_project(str(project))
            self.assertEqual(window.quality_records, {})
        finally:
            window.capacity_clock.stop()
            window.deleteLater()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
