"""GUI regressions for per-film source audio selection and safe project files."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox
    from main import BuildThread, Window
except ImportError:
    QApplication = None

from core import parse_probe


def media(path, *, audio_count=2):
    streams = [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1920,
                'height': 1080, 'avg_frame_rate': '24000/1001'}]
    streams += [{'codec_type': 'audio', 'codec_name': 'aac', 'channels': 2,
                 'tags': {'language': 'eng'}},
                {'codec_type': 'audio', 'codec_name': 'eac3', 'channels': 6,
                 'tags': {'language': 'rus'}}][:audio_count]
    return parse_probe(path, {'format': {'duration': '90', 'size': '5000'},
                              'streams': streams})


@unittest.skipIf(QApplication is None, 'PySide6 not installed')
class GUIAudioSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = Window()

    def tearDown(self):
        with patch('main.save_settings'):
            self.window.close()

    def add_movie(self, path, info):
        self.window.media[path] = info
        self.window.chapters[path] = [0.0]
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        self.window.movies.addItem(item)

    def test_selection_is_independent_for_each_movie(self):
        a, b = '/tmp/first.mkv', '/tmp/second.mkv'
        self.add_movie(a, media(a))
        self.add_movie(b, media(b))
        self.window.movies.setCurrentRow(0)
        self.assertEqual(self.window.audio_track.count(), 2)
        self.assertIn('RUS', self.window.audio_track.itemText(1))
        self.assertIn('6 кан.', self.window.audio_track.itemText(1))
        self.window.audio_track.setCurrentIndex(1)
        self.assertEqual(self.window.audio_selection[a], 1)
        self.window.movies.setCurrentRow(1)
        self.assertEqual(self.window.audio_track.currentIndex(), 0)
        self.window.movies.setCurrentRow(0)
        self.assertEqual(self.window.audio_track.currentIndex(), 1)

    def test_build_thread_snapshots_selection_and_passes_audio_index(self):
        a, b = '/tmp/first.mkv', '/tmp/second.mkv'
        jobs = [(media(a), [0.0]), (media(b), [0.0])]
        selection = {a: 1, b: 0}
        worker = BuildThread(jobs, {'output': '/tmp/out', 'ffmpeg': 'ffmpeg',
                                    'tsmuxer': 'tsmuxer'}, 'BD25', False, False,
                             audio_selection=selection)
        selection[a] = 0
        with patch('main.create_bluray', return_value=Path('/tmp/result')) as create:
            worker.run()
        self.assertEqual(create.call_count, 2)
        self.assertEqual([c.kwargs['audio_index'] for c in create.call_args_list], [1, 0])

    def test_build_forwards_selected_track_to_queue(self):
        a = '/tmp/first.mkv'
        self.add_movie(a, media(a))
        self.window.movies.setCurrentRow(0)
        self.window.audio_track.setCurrentIndex(1)
        with patch.object(self.window, '_save_config'), \
             patch('main.QMessageBox.question', return_value=QMessageBox.Yes), \
             patch.object(BuildThread, 'start'):
            self.window.start_build()
        self.assertEqual(self.window.build_thread.audio_selection[a], 1)

    def test_silent_source_does_not_offer_audio(self):
        a = '/tmp/silent.mkv'
        self.add_movie(a, media(a, audio_count=0))
        self.window.movies.setCurrentRow(0)
        self.assertFalse(self.window.audio_track.isEnabled())
        self.assertIn('Без звука', self.window.audio_track.currentText())

    def test_project_roundtrip_preserves_selection(self):
        a = '/tmp/first.mkv'
        self.add_movie(a, media(a))
        self.window.movies.setCurrentRow(0)
        self.window.audio_track.setCurrentIndex(1)
        project = self.window._project_data()
        self.assertEqual(project['audio_selection'], {a: 1})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'project.dfr.json'
            path.write_text(json.dumps(project), encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path), '')), \
                 patch.object(self.window, '_queue_scan') as scan:
                self.window.open_project()
            scan.assert_called_once_with([a])
            self.assertEqual(self.window.audio_selection[a], 1)

    def test_invalid_saved_selection_preserves_current_project(self):
        a = '/tmp/first.mkv'
        self.window.menu_title.setText('Existing project')
        self.add_movie(a, media(a))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'broken.dfr.json'
            for invalid in ({a: True}, {a: -1}, {'unknown.mkv': 1}, []):
                with self.subTest(invalid=invalid):
                    data = {'format_version': 1, 'files': [a],
                            'chapters': {a: [0.0]}, 'audio_selection': invalid}
                    path.write_text(json.dumps(data), encoding='utf-8')
                    with patch('main.QFileDialog.getOpenFileName',
                               return_value=(str(path), '')), \
                         patch('main.QMessageBox.warning') as warning, \
                         patch.object(self.window, '_queue_scan') as scan:
                        self.window.open_project()
                    warning.assert_called_once()
                    scan.assert_not_called()
                    self.assertEqual(self.window.menu_title.text(), 'Existing project')

    def test_stale_choice_falls_back_to_first_after_probe(self):
        a = '/tmp/first.mkv'
        self.add_movie(a, media(a))
        self.window.audio_selection[a] = 99
        self.window.movies.setCurrentRow(0)
        self.window._scanned(a, media(a), '')
        self.assertEqual(self.window.audio_selection[a], 0)
        self.assertEqual(self.window.audio_track.currentIndex(), 0)

    def test_invalid_selection_prevents_starting_queue(self):
        a = '/tmp/first.mkv'
        self.add_movie(a, media(a))
        self.window.movies.setCurrentRow(0)
        self.window.audio_selection[a] = 9
        with patch.object(self.window, '_save_config'), \
             patch('main.QMessageBox.warning') as warning, \
             patch('main.QMessageBox.question') as question, \
             patch.object(BuildThread, 'start') as start:
            self.window.start_build()
        warning.assert_called_once()
        question.assert_not_called()
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
