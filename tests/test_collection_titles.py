import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtWidgets import QApplication, QListWidgetItem
from collection_titles import movie_title, normalize_names, validate_title
from main import Window
from test_disc_capacity import film


class CollectionNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.paths = [str(Path(name).resolve()) for name in ('movie.WEB-DL.mkv', 'episode.02.mkv')]
        for i, path in enumerate(self.paths):
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.UserRole, path)
            self.w.movies.addItem(item)
            self.w._scanned(path, replace(film(), path=path), '')
            self.w.chapters[path] = [0., 60.+i]
            self.w.audio_selections[path] = [i+1]
        self.w.movies.setCurrentRow(0)

    def tearDown(self):
        self.w.build_thread = None
        self.w.scan_thread = None
        self.w.capacity_clock.stop()
        self.w.hide()
        self.w.deleteLater()

    def rename(self, text):
        self.w.movie_title_edit.setText(text)
        self.w.movie_title_edit.textEdited.emit(text)

    def test_name_order_and_selection_roundtrip(self):
        self.rename('  Серия 1 — Встреча  ')
        self.w._move_movie(1)
        self.assertEqual(self.w._selected_path(), self.paths[0])
        self.assertIn('Серия 1 — Встреча', self.w.movies.item(1).text())
        data = self.w._project_data()
        self.assertEqual(data['files'], self.paths[::-1])
        self.assertEqual(data['movie_names'], {self.paths[0]: 'Серия 1 — Встреча'})
        self.assertEqual(data['audio'][self.paths[0]], [1])
        self.assertEqual(data['chapters'][self.paths[0]], [0., 60.])
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)/'project.json'
            project.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(self.w, '_queue_scan') as scan, patch('main.QMessageBox.warning') as warning:
                self.w._load_project(project)
            warning.assert_not_called()
            scan.assert_called_once_with(self.paths[::-1])
            self.assertEqual(self.w.movie_names, data['movie_names'])
            self.assertEqual(self.w.audio_selections, data['audio'])

    def test_drag_model_order_is_serialized_without_changing_source(self):
        self.rename('Пилот')
        self.assertTrue(self.w.movies.model().moveRows(QModelIndex(), 0, 1, QModelIndex(), 2))
        self.assertEqual(self.w._project_data()['files'], self.paths[::-1])
        self.assertEqual(self.w.media[self.paths[0]].path, self.paths[0])
        self.assertEqual(self.w.movie_names[self.paths[0]], 'Пилот')

    def test_clear_and_remove_name(self):
        self.rename('Пилот')
        self.rename('')
        self.assertFalse(self.w.movie_names)
        self.assertIn('movie.WEB-DL', self.w.movies.currentItem().text())
        self.rename('Финал')
        self.w.remove_media()
        self.assertNotIn(self.paths[0], self.w.movie_names)

    def test_busy_guard_and_boundaries(self):
        self.w._move_movie(-1)
        self.assertEqual(self.w._selected_path(), self.paths[0])
        self.w.build_thread = Mock()
        self.w.build_thread.isRunning.return_value = True
        self.w._refresh_buttons()
        self.assertFalse(self.w.movie_title_edit.isEnabled())
        self.assertFalse(self.w.movies.dragEnabled())
        self.w._move_movie(1)
        self.rename('Не применять')
        self.assertEqual(self.w._project_data()['files'], self.paths)
        self.assertFalse(self.w.movie_names)

    def test_invalid_project_preserves_current_project_and_legacy_loads(self):
        self.rename('Текущее имя')
        data = self.w._project_data()
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)/'project.json'
            data['movie_names'] = {self.paths[0]: 'Ошибка\nперенос'}
            project.write_text(json.dumps(data), encoding='utf-8')
            with patch('main.QMessageBox.warning') as warning:
                self.w._load_project(project)
            warning.assert_called_once()
            self.assertEqual(self.w.movie_names[self.paths[0]], 'Текущее имя')
            del data['movie_names']
            project.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(self.w, '_queue_scan'), patch('main.QMessageBox.warning') as warning:
                self.w._load_project(project)
            warning.assert_not_called()
            self.assertFalse(self.w.movie_names)

    def test_validation_and_fallback(self):
        for value in (None, 42, 'я'*81, 'a\x00b', 'a\nb', 'a\u202eb'):
            with self.assertRaises(ValueError):
                validate_title(value)
        self.assertEqual(validate_title(' Ночь & день — Part 2 '), 'Ночь & день — Part 2')
        self.assertEqual(movie_title(self.paths[1], {}), 'episode.02')
        self.assertEqual(normalize_names({self.paths[0]: '  ', 'unrelated.mkv': 'Lost'}, self.paths), {})
