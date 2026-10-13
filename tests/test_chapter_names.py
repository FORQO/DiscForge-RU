import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core import parse_probe
from source_chapters import import_chapter_metadata
from disc_menu import MenuDesign, pages

DATA = {'format': {'duration': '120', 'size': '123'},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080}],
        'chapters': [{'start_time': '0', 'tags': {'title': 'Пролог'}},
                     {'start_time': '10.250', 'tags': {'TITLE': 'Встреча'}},
                     {'start_time': '60.125', 'tags': {'title': 'Финал'}}]}


class NameImportTests(unittest.TestCase):
    def test_titles_follow_sorted_deduplicated_times(self):
        raw = [{'start_time': '60', 'tags': {'TITLE': 'Финал'}},
               {'start_time': '10.250'},
               {'start_time': '10.2501', 'tags': {'title': 'Встреча'}},
               {'start_time': '10.2502', 'tags': {'title': 'Другой дубль'}}]
        times, titles, notices = import_chapter_metadata(raw, 120)
        self.assertEqual(times, (0, 10.25, 60))
        self.assertEqual(titles, ('', 'Встреча', 'Финал'))
        self.assertFalse(notices)

    def test_bad_titles_do_not_discard_valid_timestamps(self):
        values = [None, 17, {}, '  Ночь\nв\x00городе  ', 'я' * 150]
        times, titles, notices = import_chapter_metadata([
            {'start_time': str(i), 'tags': {'title': value}} for i, value in enumerate(values)], 120)
        self.assertEqual(times, (0, 1, 2, 3, 4))
        self.assertEqual(titles, ('', '', '', 'Ночь в городе', 'я' * 120))
        self.assertFalse(notices)

    def test_probe_and_menu_serialization_keep_names_and_language_overrides(self):
        info = parse_probe('film.mkv', DATA)
        self.assertEqual(info.source_chapter_titles, ('Пролог', 'Встреча', 'Финал'))
        design = MenuDesign('Film', chapter_titles=list(info.source_chapter_titles),
                            labels={'ru:chapter:1': 'Разговор', 'en:chapter:1': 'Conversation'})
        restored = MenuDesign.from_dict('Film', json.loads(json.dumps(design.to_dict())))
        self.assertIn('Разговор', pages(list(info.source_chapters), restored)[1][1][0])
        self.assertEqual(restored.text('chapter:0'), 'Пролог')
        restored = replace(restored, language='en')
        self.assertEqual(restored.text('chapter:1'), 'Conversation')

    def test_unnamed_chapters_keep_numbered_fallback(self):
        for lang, expected in [('ru', 'Глава 2'), ('en', 'Chapter 2')]:
            design = MenuDesign('Film', language=lang, chapter_titles=['Начало', ''])
            self.assertIn(expected, pages([0, 10], design)[1][1][0])

    def test_invalid_project_names_rejected(self):
        for value in ('text', [None], ['x\n'], ['x' * 121], [''] * 1000):
            with self.assertRaises(ValueError):
                MenuDesign('Film', chapter_titles=value)


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication, QListWidgetItem, QPushButton
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from main import Window, BuildThread
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Qt is not installed')
class NameEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.path = str(Path('named.mkv').resolve())
        self.add_movie(self.path, DATA)

    def add_movie(self, path, data):
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        self.w.movies.addItem(item)
        self.w.movies.setCurrentItem(item)
        self.w._scanned(path, parse_probe(path, data), '')

    def tearDown(self):
        with patch('main.save_settings'):
            self.w.close()

    def test_double_click_edits_imported_name_and_reset_restores_source(self):
        editor = self.w.menu_editor
        self.w.tabs.setCurrentWidget(editor)
        self.w.resize(1600, 1000)
        self.w.show()
        editor.page = 1
        editor.refresh()
        self.app.processEvents()
        element = next(e for e in editor.preview.elements if e['key'] == 'chapter:1')
        point = editor.preview.display_rect(element['text_rect']).center()
        QTest.mouseDClick(editor.preview, Qt.LeftButton, pos=point)
        self.assertEqual(editor.preview.inline.text(), 'Встреча')
        editor.preview.inline.setText('Разговор героев')
        editor.preview.commit_edit()
        self.assertEqual(editor.design().text('chapter:1'), 'Разговор героев')
        reset = next(b for b in editor.findChildren(QPushButton) if b.text() == 'Сбросить подпись')
        reset.click()
        self.assertEqual(editor.design().text('chapter:1'), 'Встреча')

    def test_manual_names_survive_scan_save_and_reload(self):
        self.w.menu_editor.inline_text('chapter:1', 'Моя сцена')
        self.w._scanned(self.path, parse_probe(self.path, DATA), '')
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Моя сцена')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'film.dfr.json'
            path.write_text(json.dumps(self.w._project_data()), encoding='utf-8')
            def rescan(paths):
                for p in paths:
                    self.add_movie(p, DATA)
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path), '')), patch.object(self.w, '_queue_scan', side_effect=rescan):
                self.w.open_project()
        self.assertEqual(self.w.menu_editor.design().text('chapter:0'), 'Пролог')
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Моя сцена')

    def test_retime_insertion_and_deletion_keep_names_attached(self):
        editor = self.w.menu_editor
        editor.inline_text('chapter:1', 'Правка')
        self.w.apply_chapters(self.path, [0, 5, 10.25, 60.125])
        self.assertEqual(editor.chapter_titles, ['Пролог', '', 'Встреча', 'Финал'])
        self.assertEqual(editor.design().text('chapter:2'), 'Правка')
        self.w.apply_chapters(self.path, [0, 5, 60.125, 70], {10.25: 70})
        self.assertEqual(editor.chapter_titles, ['Пролог', '', 'Финал', 'Встреча'])
        self.assertEqual(editor.design().text('chapter:3'), 'Правка')
        self.w.apply_chapters(self.path, [0, 60.125])
        self.assertEqual(editor.chapter_titles, ['Пролог', 'Финал'])
        self.assertNotIn('ru:chapter:3', editor.labels)

    def test_queue_uses_names_and_edits_from_each_movie(self):
        self.w.menu_editor.inline_text('chapter:1', 'Первый фильм')
        other = str(Path('other.mkv').resolve())
        data = copy.deepcopy(DATA)
        data['chapters'][1]['tags'] = {'title': 'Другой фильм'}
        self.add_movie(other, data)
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Другой фильм')
        self.w._save_chapter_names()
        worker = BuildThread([], {}, 'BD25', True, False, self.w.menu_editor.design(), chapter_names=self.w.chapter_names)
        for path, expected in [(self.path, 'Первый фильм'), (other, 'Другой фильм')]:
            self.assertEqual(worker.menu_for(self.w.media[path], self.w.chapters[path]).text('chapter:1'), expected)
        self.w.movies.setCurrentRow(0)
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Первый фильм')

    def test_loading_iso_design_does_not_overwrite_movie_names(self):
        self.w.menu_editor.inline_text('chapter:1', 'Фильм')
        self.w.image_tools.menu_panel.loading_design.emit()
        self.w.menu_editor.apply_design(MenuDesign('ISO', chapter_titles=['ISO', 'Другая сцена']))
        self.w._save_chapter_names()
        self.w._select_media()
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Фильм')

    def test_legacy_project_keeps_manual_label_and_imports_missing_names(self):
        data = self.w._project_data()
        del data['chapter_names']
        data['menu'].pop('chapter_titles', None)
        data['menu']['labels'] = {'ru:chapter:1': 'Старая правка'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.dfr.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path), '')), patch.object(self.w, '_queue_scan', side_effect=lambda paths: self.add_movie(paths[0], DATA)):
                self.w.open_project()
        self.assertEqual(self.w.menu_editor.design().text('chapter:0'), 'Пролог')
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Старая правка')

    def test_project_saved_before_legacy_rescan_can_be_reopened(self):
        data = self.w._project_data()
        data['chapter_names'][self.path] = {'labels': {'ru:chapter:1': 'Правка до анализа'}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pending.dfr.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path), '')), patch.object(self.w, '_queue_scan', side_effect=lambda paths: self.add_movie(paths[0], DATA)), patch('main.QMessageBox.warning') as warning:
                self.w.open_project()
            warning.assert_not_called()
        self.assertEqual(self.w.menu_editor.design().text('chapter:0'), 'Пролог')
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Правка до анализа')
