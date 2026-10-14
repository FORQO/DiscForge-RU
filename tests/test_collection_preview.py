import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem
from PySide6.QtTest import QTest
from collection_menu import catalog_page
from disc_menu import MenuDesign, render_page
from main import Window
from test_disc_capacity import film


class CatalogPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.paths = []
        for index in range(2):
            path = str(Path(f'catalog-episode-{index}.mkv').resolve())
            self.paths.append(path)
            self.w.movie_names[path] = f'Серия {index+1} — История'
            item = QListWidgetItem('pending')
            item.setData(Qt.UserRole, path)
            self.w.movies.addItem(item)
            self.w._scanned(path, replace(film(), path=path, source_chapters=(0., 60.+index),
                                         source_chapter_titles=('Начало', f'Сцена серии {index+1}')), '')
        self.w.movies.setCurrentRow(0)
        self.editor = self.w.menu_editor

    def tearDown(self):
        self.w.capacity_clock.stop()
        self.w.hide()
        self.w.deleteLater()

    def test_catalog_appears_and_matches_authored_page_pixels(self):
        self.assertTrue(self.editor.is_catalog())
        buttons = [e for e in self.editor.preview.elements if e['button']]
        self.assertEqual([e['shown'] for e in buttons], ['Серия 1 — История', 'Серия 2 — История'])
        design, entries = catalog_page(self.editor.current_design(preview=True), [e['shown'] for e in buttons], targets=[1, 5])
        authored = render_page(design, [0.], focus=self.editor.focus, entries_override=entries)
        self.assertEqual(self.editor.preview.image, authored)

    def test_open_correct_movie_chapters_and_return_to_catalog(self):
        self.editor.preview_selection('catalog:movie:1')
        self.editor.press('enter')
        self.assertFalse(self.editor.is_catalog())
        self.assertEqual(self.w._selected_path(), self.paths[1])
        self.assertEqual(self.editor.chapters, [0., 61.])
        self.assertEqual(self.editor.display_design().title, 'Серия 2 — История')
        self.editor.press('down')
        self.editor.press('enter')
        self.assertEqual(self.editor.page, 1)
        shown = [e['shown'] for e in self.editor.preview.elements]
        self.assertIn('Серия 2 — История', shown)
        self.assertTrue(any('Сцена серии 2' in s for s in shown))
        self.assertFalse(any('Сцена серии 1' in s for s in shown))
        self.editor.press('down')
        self.editor.press('enter')
        self.assertIn('Серия 2 — История', self.editor.caption.text())
        self.assertIn('61', self.editor.caption.text())
        self.editor.press('back')
        self.assertEqual(self.editor.page, 0)
        self.editor.press('back')
        self.assertTrue(self.editor.is_catalog())

    def test_rename_reorder_remove_updates_automatic_buttons(self):
        self.editor.inline_text('movie:1', 'Финал')
        self.assertEqual(self.w.movie_names[self.paths[1]], 'Финал')
        self.assertEqual(self.w._selected_path(), self.paths[0])
        self.w._move_movie(1)
        shown = [e['shown'] for e in self.editor.preview.elements if e['button']]
        self.assertEqual(shown, ['Финал', 'Серия 1 — История'])
        self.editor.preview_selection('catalog:movie:0')
        self.editor.delete_button()
        self.assertFalse(self.editor.hidden_buttons)
        self.w.remove_media()
        self.assertEqual([e['shown'] for e in self.editor.preview.elements if e['button']], ['Финал'])

    def test_chapter_selector_keeps_per_movie_edits_and_names(self):
        self.editor.inline_text('chapter:1', 'Встреча героев')
        self.w.chapter_movie.setCurrentIndex(1)
        self.assertEqual(self.w._selected_path(), self.paths[1])
        self.assertIn('Серия 2 — История', self.w.chapter_status.text())
        self.assertIn('Серия 2 — История', self.w.chapter_list.item(1).toolTip())
        self.assertNotIn('Встреча героев', self.w.chapter_list.item(1).text())
        self.w.chapter_movie.setCurrentIndex(0)
        self.assertIn('Встреча героев', self.w.chapter_list.item(1).text())
        self.editor.open_movie(self.paths[0])
        self.editor.inline_text('title', 'Новое имя фильма')
        self.assertEqual(self.w.movie_names[self.paths[0]], 'Новое имя фильма')
        self.assertNotEqual(self.editor.title.text(), 'Новое имя фильма')

    def test_separate_disc_mode_preserves_original_editor(self):
        self.w.disc_mode.setCurrentIndex(1)
        self.assertFalse(self.editor.is_catalog())
        self.assertFalse(self.editor.collection_enabled)
        self.assertEqual(self.editor.preview.elements[1]['shown'], self.editor.title.text())

    def test_double_click_catalog_name_and_fullscreen_navigation(self):
        self.w.tabs.setCurrentWidget(self.editor)
        self.w.resize(1400, 1000)
        self.w.show()
        self.app.processEvents()
        element = next(e for e in self.editor.preview.elements if e['key'] == 'movie:1')
        point = self.editor.preview.display_rect(element['text_rect']).center()
        QTest.mouseDClick(self.editor.preview, Qt.LeftButton, pos=point)
        self.assertEqual(self.editor.preview.edit_key, 'movie:1')
        self.editor.preview.inline.setText('Возвращение')
        QTest.keyClick(self.editor.preview.inline, Qt.Key_Return)
        self.assertEqual(self.w.movie_names[self.paths[1]], 'Возвращение')
        self.assertFalse(any('movie:' in key for key in self.editor.labels))
        self.editor.show_fullscreen()
        self.app.processEvents()
        QTest.keyClick(self.editor.fullscreen, Qt.Key_Down)
        # Rename resets selection; explicitly select the second catalog item.
        self.editor.focus = 1
        QTest.keyClick(self.editor.fullscreen, Qt.Key_Return)
        self.assertEqual(self.w._selected_path(), self.paths[1])
        QTest.keyClick(self.editor.fullscreen, Qt.Key_Down)
        QTest.keyClick(self.editor.fullscreen, Qt.Key_Return)
        self.assertEqual(self.editor.page, 1)
        self.assertEqual(self.editor.display_design().title, 'Возвращение')
        QTest.keyClick(self.editor.fullscreen, Qt.Key_Escape)

    def test_empty_catalog_and_page_clamp_after_removal(self):
        self.editor.catalog_page = 10
        self.w._sync_collection()
        self.assertEqual(self.editor.catalog_page, 0)
        self.w.remove_media()
        self.w.remove_media()
        self.assertFalse([e for e in self.editor.preview.elements if e['button']])
        self.editor.press('enter')
        self.editor.press('down')
        self.assertTrue(self.editor.is_catalog())


class CatalogPagesTests(unittest.TestCase):
    def test_pagination_has_one_button_per_film_and_valid_targets(self):
        names = [f'Серия {i+1}' for i in range(12)]
        targets = [3+i*4 for i in range(12)]
        actual = []
        for page in range(3):
            preview, entries = catalog_page(MenuDesign('Сериал'), names, page)
            disc, authored = catalog_page(MenuDesign('Сериал'), names, page, targets)
            self.assertEqual(preview, disc)
            self.assertEqual([e[0] for e in entries], [e[0] for e in authored])
            for (name, action), (_, target) in zip(entries, authored):
                if action.startswith('movie:'):
                    index = int(action.split(':')[1])
                    self.assertEqual(target, f'page:{targets[index]}')
                    actual.append(name)
        self.assertEqual(actual, names)
