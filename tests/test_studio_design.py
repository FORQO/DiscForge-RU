"""Studio interaction, responsive controls, number badges and real export parity."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from menu_editor import MenuEditor
from studio_ui import studio_style
from disc_menu import MenuDesign, render_page, install_menu, page_elements, button_content
from collection_menu import catalog_page
from bdmv_fixtures import minimal_mpls


class StudioDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = MenuEditor(); self.editor.setStyleSheet(studio_style('blue'))
        self.editor.resize(1200, 850); self.editor.show()
        self.editor.preview_selection('play'); self.app.processEvents()

    def tearDown(self):
        self.editor.close(); self.editor.deleteLater(); self.app.processEvents()

    def test_four_sections_fit_without_hidden_horizontal_controls(self):
        panel = self.editor.button_inspector
        self.assertEqual([panel.tabs.tabText(i) for i in range(4)], ['Размеры', 'Стиль', 'Сетка', 'Анимация'])
        for width, height in ((1040, 680), (1380, 850)):
            self.editor.resize(width, height)
            for index in range(4):
                panel.tabs.setCurrentIndex(index); self.app.processEvents()
                scroll = panel.tabs.widget(index)
                self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
            self.assertTrue(self.editor.canvas_toolbar.isVisible())
        panel.tabs.setCurrentIndex(2); panel.advanced_toggle.setChecked(True); self.app.processEvents()
        scroll = panel.tabs.widget(2)
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())

    def test_style_card_applies_renderer_style_without_geometry_change(self):
        panel = self.editor.button_inspector; panel.tabs.setCurrentIndex(1)
        self.app.processEvents()
        before = copy.deepcopy(self.editor.element_layout)
        QTest.mouseClick(panel.style_cards['glass'], Qt.LeftButton)
        self.assertEqual(self.editor.button_styles['play']['variant'], 'glass')
        self.assertEqual(before, self.editor.element_layout)
        self.assertTrue(panel.style_cards['glass'].isChecked())

    def test_resize_tool_is_explicit_and_keyboard_switches_back(self):
        preview = self.editor.preview
        element = next(e for e in preview.elements if e['id'] == 'play')
        self.assertFalse(preview.handles(element))
        QTest.keyClick(preview, Qt.Key_R)
        self.assertEqual(len(preview.handles(element)), 8)
        self.assertTrue(self.editor.resize_canvas.isChecked())
        QTest.keyClick(preview, Qt.Key_V)
        self.assertFalse(preview.handles(element))
        self.assertTrue(self.editor.select_canvas.isChecked())

    def test_zoom_pan_and_overlays_do_not_change_project(self):
        preview = self.editor.preview
        before = self.editor.settings()
        self.editor.set_canvas_zoom(1.0)
        self.assertEqual(preview.target_rect().width(), 1920)
        pos = preview.rect().center(); x = preview.target_rect().x()
        QTest.mousePress(preview, Qt.MiddleButton, pos=pos)
        QTest.mouseMove(preview, pos+QPoint(40, 0), 15)
        QTest.mouseRelease(preview, Qt.MiddleButton, pos=pos+QPoint(40, 0))
        self.assertEqual(preview.target_rect().x(), x+40)
        self.editor.grid_canvas.setChecked(True); self.editor.guides_canvas.setChecked(False)
        self.assertTrue(preview.show_grid); self.assertFalse(preview.show_guides)
        self.assertEqual(before, self.editor.settings())
        self.editor.set_preview_mode(True)
        self.assertIsNone(preview.zoom)
        self.editor.set_preview_mode(False)
        self.assertEqual(preview.zoom, 1.0)
        self.editor.set_canvas_zoom(0)
        self.assertIsNone(preview.zoom)
        self.assertLessEqual(preview.target_rect().width(), preview.width())

    def test_number_badge_preserves_editable_title_and_can_be_disabled(self):
        design, entries = catalog_page(MenuDesign('Film'), ['Эпизод 03 · Ночной город'])
        element = next(e for e in page_elements(design, [0], 0, entries) if e['button'])
        _, label, badge, ordinal = button_content(element)
        self.assertEqual(label, 'Ночной город'); self.assertEqual(ordinal, 3); self.assertIsNotNone(badge)
        self.assertEqual(element['shown'], 'Эпизод 03 · Ночной город')
        element['style']['numbered'] = False
        _, label, badge, _ = button_content(element)
        self.assertEqual(label, element['shown']); self.assertIsNone(badge)
        with self.assertRaises(ValueError):
            MenuDesign('Bad', button_styles={'play': {'numbered': 'yes'}})

    def test_numbered_catalog_export_is_pixel_identical_to_preview(self):
        from disc_collection import merge_menu
        design = MenuDesign('Collection')
        titles = ['Эпизод 01 · Ночной город', 'Эпизод 02 · Последний поезд']
        catalog, entries = catalog_page(design, titles)
        expected = render_page(catalog, [0], entries_override=entries)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'source'
            playlist = source/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True); playlist.write_bytes(minimal_mpls())
            install_menu(source, MenuDesign('Movie'), [0])
            merge_menu(root/'output', [source, source], design, titles)
            with zipfile.ZipFile(root/'output/BDMV/JAR/00000.jar') as jar:
                actual = QImage.fromData(jar.read('page0.png')).convertToFormat(expected.format())
                self.assertEqual(expected, actual)


class ThumbnailTests(unittest.TestCase):
    def test_thumbnail_command_is_bounded_and_result_delivered(self):
        from library_thumbnails import ThumbnailSignals, ThumbnailTask
        signals = ThumbnailSignals(); received = []
        signals.ready.connect(lambda path, payload: received.append((path, payload)))
        process = Mock(); process.communicate.return_value = (b'frame', None); process.returncode = 0; process.poll.return_value = 0
        with patch('library_thumbnails.subprocess.Popen', return_value=process) as spawn:
            ThumbnailTask('film with spaces.mkv', 5000, 'ffmpeg', signals, threading.Event()).run()
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index('-ss')+1], '30')
        self.assertEqual(command[command.index('-i')+1], 'film with spaces.mkv')
        self.assertIn('scale=160:90:force_original_aspect_ratio=increase,crop=160:90', command)
        self.assertEqual(received, [('film with spaces.mkv', b'frame')])

    def test_cancelled_thumbnail_does_not_launch_process(self):
        from library_thumbnails import ThumbnailSignals, ThumbnailTask
        cancel = threading.Event(); cancel.set()
        with patch('library_thumbnails.subprocess.Popen') as spawn:
            ThumbnailTask('film.mkv', 50, 'ffmpeg', ThumbnailSignals(), cancel).run()
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
