"""Canvas gestures, chapter timing and authoring use the same persisted model."""
import os
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication, QListWidgetItem
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtTest import QTest
except ImportError:
    QApplication = None
from disc_menu import MenuDesign, pages, install_menu, validate_menu_navigation, page_elements
from menu_layout import snap_rect
from bdmv_fixtures import minimal_mpls


class LayoutModelTests(unittest.TestCase):
    def test_snap_near_edge_and_leave_distant_positions_alone(self):
        self.assertEqual(snap_rect([304, 404, 400, 80], [[300, 400, 200, 80]], 6)[0][:2], [300, 400])
        self.assertEqual(snap_rect([320, 425, 400, 80], [[300, 400, 200, 80]], 6)[0][:2], [320, 425])

    def test_malformed_layout_is_rejected(self):
        for kwargs in ({'layout': {'play': [1900, 400, 300, 80]}}, {'layout': {'play': [1, 2, -3, 80]}},
                       {'text_styles': {'title': {'text': 'red'}}}, {'custom_buttons': [{'id': 'oops'}]}):
            with self.assertRaises(ValueError):
                MenuDesign('Film', **kwargs)

    def test_custom_action_validated_before_authoring(self):
        design = MenuDesign('Film', custom_buttons=[dict(id='custom:123456789abc', page=0, action='play:5')])
        with self.assertRaisesRegex(ValueError, 'отсутствующую'):
            validate_menu_navigation([0], design)


@unittest.skipIf(QApplication is None, 'Qt is not installed')
class LayoutGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from menu_editor import MenuEditor
        self.editor = MenuEditor()
        self.editor.resize(1150, 1100)
        self.editor.show()
        self.app.processEvents()

    def tearDown(self):
        self.editor.close()

    def point(self, x, y):
        r = self.editor.preview.target_rect()
        return QPoint(r.x()+round(x*r.width()/1920), r.y()+round(y*r.height()/1080))

    def gesture(self, start, end, modifiers=Qt.NoModifier):
        preview = self.editor.preview
        QTest.mousePress(preview, Qt.LeftButton, modifiers, start)
        QTest.mouseMove(preview, end, 15)
        QTest.mouseRelease(preview, Qt.LeftButton, modifiers, end)
        self.app.processEvents()

    def test_drag_button_with_letterboxing_and_resize_handle(self):
        self.editor.magnet.setChecked(False)
        self.gesture(self.point(850, 445), self.point(1100, 645))
        rect = self.editor.design().layout['play']
        self.assertAlmostEqual(rect[0], 400, delta=4)
        self.assertAlmostEqual(rect[1], 600, delta=4)
        self.assertFalse(self.editor.preview.inline.isVisible())
        element = next(e for e in self.editor.preview.elements if e['id'] == 'play')
        handle = self.editor.preview.handles(element)['se'].center()
        self.gesture(handle, handle+QPoint(45, 30))
        bigger = self.editor.design().layout['play']
        self.assertGreater(bigger[2], rect[2])
        self.assertGreater(bigger[3], rect[3])
        self.assertLessEqual(bigger[0]+bigger[2], 1920)

    def test_move_and_resize_static_text_updates_font_size(self):
        self.editor.magnet.setChecked(False)
        self.gesture(self.point(350, 255), self.point(400, 600))
        self.assertIn('text:title', self.editor.design().layout)
        element = next(e for e in self.editor.preview.elements if e['id'] == 'text:title')
        handle = self.editor.preview.handles(element)['s'].center()
        self.gesture(handle, handle+QPoint(0, 25))
        self.assertGreater(self.editor.design().text_styles['title']['font_size'], 66)

    def test_static_text_color_font_bold_and_roundtrip(self):
        self.editor.preview_selection('text:title')
        # Pick a visible font in the actual combo; database-only private macOS
        # fonts and Windows-only Arial are not selectable on every platform.
        combo = self.editor.font_family
        index = next(i for i in range(combo.count())
                     if combo.itemText(i) != combo.currentText() and not combo.itemText(i).startswith('.'))
        family = combo.itemText(index)
        combo.setCurrentIndex(index)
        self.editor.font_size.setValue(72)
        self.editor.bold.setChecked(False)
        self.editor.italic.setChecked(True)
        with patch('menu_editor.QColorDialog.getColor', return_value=QColor('#ff1234')):
            self.editor.choose_color('text')
        restored = MenuDesign.from_dict(self.editor.title.text(), json.loads(json.dumps(self.editor.settings())))
        self.assertEqual(restored.text_styles['title']['text'], '#ff1234')
        self.assertEqual(restored.text_styles['title']['font_family'], family)
        self.assertTrue(restored.text_styles['title']['italic'])
        self.assertFalse(self.editor.shape.isEnabled())

    def test_add_button_edit_action_delete_and_restore(self):
        self.editor.set_chapters([0, 4, 8])
        self.editor.add_button()
        key = self.editor.selected_button
        self.editor.action_selector.setCurrentIndex(self.editor.action_selector.findData('play:2'))
        self.editor.inline_text(key, 'Финал')
        self.assertEqual(self.editor.design().custom_buttons[0]['action'], 'play:2')
        self.assertEqual(pages([0, 4, 8], self.editor.design())[0][-1], ('Финал', key))
        self.editor.delete_button()
        self.assertNotIn(key, [action for _, action in pages([0, 4, 8], self.editor.design())[0]])
        self.editor.restore_button()
        self.assertIn(key, [action for _, action in pages([0, 4, 8], self.editor.design())[0]])

    def test_alignment_centres_all_current_buttons(self):
        self.editor.add_button()
        self.editor.alignment.setCurrentIndex(1)
        self.editor.align_buttons()
        for e in page_elements(self.editor.design(), [0], 0):
            if e['button']:
                self.assertEqual(e['rect'][0]+e['rect'][2]/2, 960)

    def test_retime_preserves_chapter_labels_geometry_and_custom_jump(self):
        self.editor.set_chapters([0, 10, 20])
        self.editor.labels['ru:chapter:1'] = 'Сцена'
        self.editor.element_layout['chapter:1'] = [300, 500, 500, 90]
        self.editor.add_button()
        self.editor.action_selector.setCurrentIndex(self.editor.action_selector.findData('play:1'))
        self.editor.retime_chapters([0, 10, 20], [0, 5, 10, 20])
        design = self.editor.design()
        self.assertEqual(design.labels['ru:chapter:2'], 'Сцена')
        self.assertEqual(design.layout['chapter:2'], [300, 500, 500, 90])
        self.assertEqual(design.custom_buttons[0]['action'], 'play:2')
        self.editor.retime_chapters([0, 5, 10, 20], [0, 5, 20, 25], {10: 25})
        self.assertEqual(self.editor.design().custom_buttons[0]['action'], 'play:3')

    def test_export_has_custom_action_and_exact_moved_focus_rectangle(self):
        key = 'custom:123456789abc'
        design = MenuDesign('Film', layout={key: [880, 700, 550, 120]},
                            custom_buttons=[dict(id=key, page=0, action='play:0')])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            playlist = root/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            playlist.write_bytes(minimal_mpls())
            install_menu(root, design, [0])
            with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                props = jar.read('menu.properties')
                for line in (b'page.0.1=play:0', b'page.0.1.x=880', b'page.0.1.y=700',
                             b'page.0.1.w=550', b'page.0.1.h=120'):
                    self.assertIn(line, props)


@unittest.skipIf(QApplication is None, 'Qt is not installed')
class ChapterTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from main import Window
        from core import MediaInfo
        self.window = Window()
        self.path = 'test-film.mp4'
        self.window.media[self.path] = MediaInfo(self.path, 120, 10000, 'h264', 1920, 1080, '24', '', 'aac', 2, 1, 0)
        self.window.chapters[self.path] = [0.0]
        item = QListWidgetItem('Film')
        item.setData(Qt.UserRole, self.path)
        self.window.movies.addItem(item)
        self.window.movies.setCurrentRow(0)
        self.window._select_media()

    def tearDown(self):
        with patch('main.save_settings'):
            self.window.close()

    def test_add_and_edit_time_directly(self):
        self.window.chapter_time.setText('00:00:40')
        self.window.add_chapter_button.click()
        self.assertEqual(self.window.chapters[self.path], [0, 40])
        self.window.chapter_list.setCurrentRow(1)
        self.window.chapter_time.setText('00:01:10')
        self.window.edit_chapter_button.click()
        self.assertEqual(self.window.chapters[self.path], [0, 70])
        self.assertEqual(self.window.menu_editor.chapters, [0, 70])

    def test_invalid_duplicate_and_end_times_preserve_chapters(self):
        for value in ('00:00:00', '00:02:00', '00:65:00'):
            self.window.chapter_time.setText(value)
            with patch('main.QMessageBox.warning') as warning:
                self.window.add_chapter_button.click()
                warning.assert_called_once()
            self.assertEqual(self.window.chapters[self.path], [0])

    def test_first_chapter_is_protected_and_auto_interval_generates_marks(self):
        self.window.chapter_list.setCurrentRow(0)
        self.assertFalse(self.window.edit_chapter_button.isEnabled())
        self.assertFalse(self.window.delete_chapter_button.isEnabled())
        self.window.auto_interval.setValue(1)
        self.window.auto_chapter_button.click()
        self.assertEqual(self.window.chapters[self.path], [0, 60])

