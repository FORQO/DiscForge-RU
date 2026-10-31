import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem
from main import Window, BuildThread, MODES
from disc_capacity import estimate
from test_disc_capacity import film
from video_treatment import enhancement_for, project_enhancements


class FilmEnhancementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.paths = [str(Path(name).resolve()) for name in ('old-film.mkv', 'good-source.mkv')]
        self.add_movies(self.paths)

    def add_movies(self, paths):
        for path in paths:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, path)
            self.w.movies.addItem(item)
            self.w._scanned(path, replace(film(), path=path), '')
        self.w.movies.setCurrentRow(0)

    def tearDown(self):
        self.w.build_thread = None
        self.w.scan_thread = None
        self.w.capacity_clock.stop()
        self.w.hide()
        self.w.deleteLater()

    def test_selection_trial_and_reorder_keep_independent_choices(self):
        self.w.enhance_video.setChecked(True)
        self.assertEqual(MODES[self.w.processing_mode.currentIndex()], 'auto')
        self.assertEqual(self.w.gpu_frames.isEnabled(), sys.platform != 'darwin')
        first, second = (self.w.media[p] for p in self.paths)
        snapshot = self.w._config()
        self.assertTrue(self.w._trial_config(first)['enhance'])
        self.assertFalse(self.w._trial_config(second)['enhance'])
        self.assertEqual(self.w._requested_mode(first), 'encode')
        self.assertEqual(self.w._requested_mode(second), 'auto')
        self.w.movies.setCurrentRow(1)
        self.assertFalse(self.w.enhance_video.isChecked())
        self.w.movies.setCurrentRow(0)
        self.assertTrue(self.w.enhance_video.isChecked())
        self.w._move_movie(1)
        self.assertTrue(self.w.enhance_video.isChecked())
        self.assertEqual(self.w._selected_path(), self.paths[0])
        self.w.remove_media()
        self.assertFalse(self.w.video_enhancements)
        self.assertEqual(snapshot['video_enhancements'], {self.paths[0]: True})
        self.add_movies([self.paths[0]])
        self.w.movies.setCurrentRow(1)
        self.assertFalse(self.w.enhance_video.isChecked())

    def test_mixed_capacity_and_worker_modes(self):
        self.w.enhance_video.setChecked(True)
        jobs = [(self.w.media[p], [0.]) for p in self.paths]
        budget = estimate(jobs, 'BD25', 'auto', collection=True, bitrate=6_000_000,
                          enhancements=self.w.video_enhancements)
        self.assertEqual([row[2] for row in budget.titles], ['encode', 'copy'])
        self.assertEqual(budget.video, 7200*6_000_000//8 + 8_000_000_000)
        for collection in (False, True):
            config = dict(self.w._config(), collection=collection)
            worker = BuildThread(jobs, config, 'BD25', False, False, processing_mode='auto')
            def prepare(media, mode, *args):
                return media, 'copy' if mode == 'auto' else mode, 'checked'
            with patch('main.prepare_processing', side_effect=prepare) as prepared, \
                 patch('main.create_bluray', return_value=Path('result')) as create, \
                 patch('disc_collection.create_collection', return_value=Path('result')) as common:
                finished = []
                worker.finished_all.connect(finished.append)
                worker.run()
            self.assertEqual(finished, [True])
            self.assertEqual([c.args[1] for c in prepared.call_args_list], ['encode', 'copy' if collection else 'auto'])
            if collection:
                self.assertEqual(common.call_args.kwargs['modes'], ['encode', 'copy'])
                self.assertEqual(common.call_args.args[1]['video_enhancements'], {self.paths[0]: True})
            else:
                self.assertEqual([c.kwargs['enhance'] for c in create.call_args_list], [True, False])
                self.assertEqual([c.kwargs['processing_mode'] for c in create.call_args_list], ['encode', 'copy'])

    def test_project_roundtrip_legacy_and_invalid_data(self):
        self.w.enhance_video.setChecked(True)
        self.w.quiet_mode.setCurrentIndex(self.w.quiet_mode.findData(.5))
        data = self.w._project_data()
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)/'project.json'
            def load(value):
                project.write_text(json.dumps(value), encoding='utf-8')
                with patch.object(self.w, '_queue_scan', side_effect=self.add_movies), patch('main.QMessageBox.warning') as warning:
                    self.w._load_project(project)
                warning.assert_not_called()
            load(data)
            self.assertTrue(self.w.enhance_video.isChecked())
            self.w.movies.setCurrentRow(1)
            self.assertFalse(self.w.enhance_video.isChecked())
            self.assertEqual(self.w.quiet_mode.currentData(), .5)
            invalid = dict(data, video_enhancements={self.paths[0]: 'true'})
            project.write_text(json.dumps(invalid), encoding='utf-8')
            with patch('main.QMessageBox.warning') as warning:
                self.w._load_project(project)
            warning.assert_called_once()
            self.assertEqual(self.w.video_enhancements, {self.paths[0]: True})
            legacy = dict(data, enhance=True, processing_mode='encode')
            legacy.pop('video_enhancements')
            load(legacy)
            self.assertEqual(self.w.video_enhancements, dict.fromkeys(self.paths, True))
            self.assertEqual(MODES[self.w.processing_mode.currentIndex()], 'auto')
            self.w.enhance_video.setChecked(False)
            self.assertEqual(self.w._requested_mode(self.w.media[self.paths[0]]), 'auto')
            legacy.pop('enhance')
            legacy.pop('quiet_rate')
            load(legacy)
            self.assertFalse(self.w.video_enhancements)
            self.assertEqual(self.w.quiet_mode.currentData(), 0)

    def test_busy_and_no_selection_cannot_change_flags(self):
        self.w.build_thread = Mock()
        self.w.build_thread.isRunning.return_value = True
        self.w._refresh_buttons()
        self.assertFalse(self.w.enhance_video.isEnabled())
        self.w.enhance_video.setChecked(True)
        self.assertFalse(self.w.video_enhancements)
        self.w.build_thread = None
        self.w.new_project()
        self.assertFalse(self.w.enhance_video.isEnabled())

    def test_map_precedence_and_validation(self):
        first, second = self.paths
        self.assertTrue(enhancement_for({'enhance': True}, first))
        self.assertFalse(enhancement_for({'enhance': True, 'video_enhancements': {}}, first))
        self.assertEqual(project_enhancements({'video_enhancements': {first: True, second: False, 'orphan.mkv': True}}, self.paths), {first: True})
        for bad in ([], None, {first: 1}, {first: 'false'}):
            with self.assertRaises(ValueError):
                project_enhancements({'video_enhancements': bad}, self.paths)
