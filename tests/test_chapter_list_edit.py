"""Chapter-list edits preserve timestamps, movie ownership and exported names."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem, QLineEdit
from core import parse_probe
from main import Window, BuildThread
from chapter_list import CHAPTER_NAME_ROLE, CHAPTER_OWNER_ROLE

DATA = {'format': {'duration': '120', 'size': '123'},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080}],
        'chapters': [{'start_time': '0', 'tags': {'title': 'Пролог'}},
                     {'start_time': '10.250', 'tags': {'title': 'Встреча'}},
                     {'start_time': '60.125', 'tags': {'title': 'Финал'}}]}


class ChapterListEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.path = str(Path('demo-chapters.mkv').resolve())
        self.add_movie(self.path)
        self.w.resize(1380, 850); self.w.show(); self.w.tabs.setCurrentIndex(3)
        self.app.processEvents()

    def add_movie(self, path):
        item = QListWidgetItem(Path(path).name); item.setData(Qt.UserRole, path)
        self.w.movies.addItem(item); self.w.movies.setCurrentItem(item)
        self.w._scanned(path, parse_probe(path, DATA), '')

    def tearDown(self):
        with patch('main.save_settings'):
            self.w.close()
        self.w.deleteLater(); self.app.processEvents()

    def name_editor(self, row):
        view = self.w.chapter_list
        view.setCurrentRow(row)
        point = view.visualItemRect(view.item(row)).center()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
        QTest.mouseDClick(view.viewport(), Qt.LeftButton, pos=point)
        self.app.processEvents()
        editors = [edit for edit in view.findChildren(QLineEdit, 'chapterNameEdit') if edit.isVisible()]
        self.assertEqual(len(editors), 1)
        return editors[0]

    def rename(self, row, text):
        edit = self.name_editor(row)
        edit.setText(text); QTest.keyClick(edit, Qt.Key_Return); self.app.processEvents()

    def test_double_click_renames_only_name_and_updates_menu(self):
        edit = self.name_editor(1)
        self.assertEqual(edit.text(), 'Встреча')
        edit.setText('Разговор героев'); QTest.keyClick(edit, Qt.Key_Return); self.app.processEvents()
        item = self.w.chapter_list.item(1)
        self.assertEqual(item.data(Qt.UserRole), 10.25)
        self.assertEqual(item.data(CHAPTER_NAME_ROLE), 'Разговор героев')
        self.assertIn('00:00:10.250', item.text())
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Разговор героев')
        self.assertEqual(self.w.chapter_names[self.path]['labels']['ru:chapter:1'], 'Разговор героев')

    def test_f2_opens_the_selected_chapter_name(self):
        view = self.w.chapter_list
        view.setCurrentRow(1); view.setFocus()
        QTest.keyClick(view, Qt.Key_F2); self.app.processEvents()
        editors = [edit for edit in view.findChildren(QLineEdit, 'chapterNameEdit') if edit.isVisible()]
        self.assertEqual(len(editors), 1)
        self.assertEqual(editors[0].text(), 'Встреча')
        QTest.keyClick(editors[0], Qt.Key_Escape)

    def test_escape_and_empty_names_preserve_existing_name(self):
        edit = self.name_editor(1); edit.setText('Отменённое имя')
        QTest.keyClick(edit, Qt.Key_Escape); self.app.processEvents()
        self.assertEqual(self.w.chapter_list.item(1).data(CHAPTER_NAME_ROLE), 'Встреча')
        self.rename(1, '   ')
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Встреча')
        self.assertEqual(self.w.chapters[self.path], [0, 10.25, 60.125])

    def test_first_chapter_can_be_renamed_while_start_stays_protected(self):
        self.rename(0, 'Начальные титры')
        self.assertEqual(self.w.chapter_list.item(0).data(Qt.UserRole), 0)
        self.assertIn('Начало фильма', self.w.chapter_list.item(0).text())
        self.assertFalse(self.w.edit_chapter_button.isEnabled())
        self.assertFalse(self.w.delete_chapter_button.isEnabled())
        self.assertEqual(self.w.menu_editor.design().text('chapter:0'), 'Начальные титры')

    def test_names_survive_two_movie_switch_project_roundtrip_and_export(self):
        self.rename(1, 'Первый фильм')
        other = str(Path('other-demo.mkv').resolve()); self.add_movie(other)
        self.assertEqual(self.w.chapter_list.item(1).data(CHAPTER_NAME_ROLE), 'Встреча')
        self.rename(1, 'Другой фильм')
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)/'chapters.dfr.json'
            project.write_text(json.dumps(self.w._project_data()), encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(project), '')), \
                 patch.object(self.w, '_queue_scan', side_effect=lambda paths: [self.add_movie(p) for p in paths]):
                self.w.open_project()
        worker = BuildThread([], {}, 'BD25', True, False, self.w.menu_editor.design(), chapter_names=self.w.chapter_names)
        for path, expected in ((self.path, 'Первый фильм'), (other, 'Другой фильм')):
            self.assertEqual(worker.menu_for(self.w.media[path], self.w.chapters[path]).text('chapter:1'), expected)
        self.w.movies.setCurrentRow(0)
        self.assertEqual(self.w.chapter_list.item(1).data(CHAPTER_NAME_ROLE), 'Первый фильм')

    def test_renamed_chapter_keeps_name_when_timeline_changes(self):
        self.rename(1, 'Погоня')
        self.w.apply_chapters(self.path, [0, 5, 10.25, 60.125])
        self.assertEqual(self.w.chapter_list.item(2).data(CHAPTER_NAME_ROLE), 'Погоня')
        self.w.apply_chapters(self.path, [0, 5, 60.125, 70], {10.25: 70})
        self.assertEqual(self.w.chapter_list.item(3).data(CHAPTER_NAME_ROLE), 'Погоня')
        self.assertEqual(self.w.chapter_list.item(3).data(Qt.UserRole), 70)

    def test_interval_number_and_actions_align_in_both_themes(self):
        for theme in ('graphite', 'blue'):
            self.w.set_theme(theme)
            for width, height in ((1040, 680), (1380, 850)):
                self.w.resize(width, height); self.app.processEvents()
                controls = (self.w.auto_interval, self.w.auto_chapter_button, self.w.delete_chapter_button)
                self.assertEqual(len({control.y() for control in controls}), 1)
                self.assertEqual(len({control.height() for control in controls}), 1)
                self.assertGreater(self.w.chapter_interval_control.slider.y(), self.w.auto_interval.geometry().bottom())

    def test_stale_owner_and_busy_state_cannot_rename_another_movie(self):
        item = self.w.chapter_list.item(1)
        index = self.w.chapter_list.indexFromItem(item)
        with patch.object(self.w, '_movies_editable', return_value=False):
            self.w.rename_chapter(index, 'Нельзя во время сборки')
        item.setData(CHAPTER_OWNER_ROLE, 'other.mkv')
        self.w.rename_chapter(index, 'Чужой фильм')
        self.assertEqual(self.w.menu_editor.design().text('chapter:1'), 'Встреча')
