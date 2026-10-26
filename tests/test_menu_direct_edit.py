"""Real pointer/key interactions, project round trips and exported runtime data."""
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
except ImportError:
    QApplication = None
from disc_menu import MenuDesign, pages, install_menu, validate_menu_navigation
from bdmv_fixtures import minimal_mpls


@unittest.skipIf(QApplication is None, 'Qt is not installed')
class DirectEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from menu_editor import MenuEditor
        self.editor = MenuEditor()
        self.editor.resize(1100, 950)
        self.editor.show()
        self.app.processEvents()

    def tearDown(self):
        self.editor.close()

    def click(self, x, y, double=False):
        preview = self.editor.preview
        rect = preview.target_rect()
        (QTest.mouseDClick if double else QTest.mouseClick)(preview, Qt.LeftButton, pos=QPoint(rect.x()+round(x*rect.width()/1920),
                                                        rect.y()+round(y*rect.height()/1080)))

    def test_click_title_edit_enter_and_escape(self):
        self.click(240, 255, double=True)
        self.assertTrue(self.editor.preview.inline.isVisible())
        self.editor.preview.inline.setText('Новый фильм / New movie')
        QTest.keyClick(self.editor.preview.inline, Qt.Key_Return)
        self.assertEqual(self.editor.design().title, 'Новый фильм / New movie')
        self.click(240, 255, double=True)
        self.editor.preview.inline.setText('Discard')
        QTest.keyClick(self.editor.preview.inline, Qt.Key_Escape)
        self.assertEqual(self.editor.design().title, 'Новый фильм / New movie')

    def test_click_button_edit_then_delete_and_restore(self):
        self.click(260, 440, double=True)
        self.assertEqual(self.editor.selected_button, 'play')
        self.editor.preview.inline.setText('Начать сеанс')
        QTest.keyClick(self.editor.preview.inline, Qt.Key_Return)
        self.assertEqual(pages([0], self.editor.design())[0][0][0], 'Начать сеанс')
        QTest.keyClick(self.editor.preview, Qt.Key_Delete)
        self.assertEqual(pages([0], self.editor.design())[0], [])
        # Remote navigation on an empty page must not divide by zero.
        self.editor.press('down')
        self.editor.press('enter')
        with self.assertRaisesRegex(ValueError, 'запуска'):
            validate_menu_navigation([0], self.editor.design())
        self.editor.restore_button()
        self.assertEqual(pages([0], self.editor.design())[0][0][0], 'Начать сеанс')

    def test_click_border_selects_without_editing_and_del_hides_only_that_button(self):
        self.editor.set_chapters([0, 4, 8])
        self.click(880, 550)
        self.assertEqual(self.editor.selected_button, 'chapters')
        self.assertEqual(self.editor.focus, 1)
        self.assertFalse(self.editor.preview.inline.isVisible())
        QTest.keyClick(self.editor.preview, Qt.Key_Delete)
        self.assertEqual(pages([0, 4, 8], self.editor.design())[0], [('Смотреть фильм', 'play:0')])

    def test_initial_text_click_then_del_deletes_button(self):
        self.click(260, 440)
        QTest.keyClick(self.editor.preview, Qt.Key_Delete)
        self.assertEqual(self.editor.design().hidden_buttons, ['play'])
        self.assertFalse(self.editor.preview.inline.isVisible())

    def test_single_select_double_edit_and_delete_every_static_text(self):
        for key, x, y in [('title', 240, 255), ('brand',210,145), ('subtitle',210,337), ('hint',210,988)]:
            self.click(x, y)
            self.assertEqual(self.editor.preview.selection, 'text:'+key)
            self.assertFalse(self.editor.preview.inline.isVisible())
            self.click(x, y, double=True)
            self.assertTrue(self.editor.preview.inline.isVisible())
            QTest.keyClick(self.editor.preview.inline, Qt.Key_Escape)
            QTest.keyClick(self.editor.preview, Qt.Key_Delete)
            self.assertIn(key, self.editor.design().hidden_text)
            self.assertNotIn('text:'+key, [e['id'] for e in self.editor.preview.elements])
            restored = MenuDesign.from_dict(self.editor.design().title, self.editor.settings())
            self.editor.apply_design(restored)
            self.assertIn(key, self.editor.design().hidden_text)
            self.editor.restore_button()
            self.assertNotIn(key, self.editor.design().hidden_text)
        self.editor.set_chapters([0,4])
        self.editor.press('down'); self.editor.press('enter')
        self.editor.select_button('text:chapter_heading')
        QTest.keyClick(self.editor.preview, Qt.Key_Delete)
        self.assertIn('chapter_heading', self.editor.design().hidden_text)

    def test_delete_during_text_edit_only_removes_characters(self):
        self.click(240,255,double=True)
        self.editor.preview.inline.setText('AB')
        self.editor.preview.inline.setCursorPosition(0)
        QTest.keyClick(self.editor.preview.inline, Qt.Key_Delete)
        QTest.keyClick(self.editor.preview.inline, Qt.Key_Return)
        self.assertEqual(self.editor.design().title, 'B')
        self.assertEqual(self.editor.design().hidden_text, [])

    def test_back_returns_previous_page_and_focus(self):
        self.editor.set_chapters(list(range(12)))
        self.editor.press('down')
        self.editor.press('enter')
        self.assertEqual(self.editor.page, 1)
        # The next-page button is to the right of the first chapter. Directional
        # navigation follows geometry, rather than advancing through list order.
        self.editor.press('right')
        self.assertEqual(self.editor.focus, 5)
        self.editor.press('enter')
        self.assertEqual(self.editor.page, 2)
        self.editor.press('back')
        self.assertEqual((self.editor.page, self.editor.focus), (1, 5))
        self.editor.press('back')
        self.assertEqual((self.editor.page, self.editor.focus), (0, 1))
        self.assertFalse(self.editor.back_button.isEnabled())

    def test_all_static_text_is_editable_even_with_letterboxing(self):
        for dimensions in ((1200, 950), (850, 1200)):
            self.editor.resize(*dimensions)
            self.app.processEvents()
            for key, x, y in [('brand', 210, 145), ('subtitle', 210, 337), ('hint', 210, 988)]:
                self.click(x, y, double=True)
                self.assertEqual(self.editor.preview.edit_key, key)
                self.editor.preview.inline.setText('Custom '+key)
                QTest.keyClick(self.editor.preview.inline, Qt.Key_Return)
                self.assertEqual(self.editor.design().text(key), 'Custom '+key)

    def test_styles_and_deleted_buttons_roundtrip_and_restore_after_reload(self):
        self.click(880, 440)
        self.editor.shape.setCurrentIndex(2)
        self.editor.border_width.setValue(5)
        self.editor.font_size.setValue(38)
        self.editor.opacity.setValue(65)
        self.editor.button_styles['play']['border'] = '#ff0033'
        self.editor.delete_button()
        data = self.editor.settings()
        restored = MenuDesign.from_dict('Movie', data)
        self.editor.apply_design(restored)
        self.editor.restore_button()
        design = self.editor.design()
        self.assertEqual(design.hidden_buttons, [])
        self.assertEqual(design.button_styles['play']['shape'], 'pill')
        self.assertEqual(design.button_styles['play']['border'], '#ff0033')

    def test_exported_focus_shape_color_and_deleted_action(self):
        # Main play can be hidden when chapters still provide access to the film.
        design = MenuDesign('Movie', hidden_buttons=['play'], button_styles={
            'chapters': {'shape': 'pill', 'highlight': '#ff0033', 'fill': '#123456'}})
        self.assertEqual(pages([0, 4], design)[0][0][1], 'page:1')
        validate_menu_navigation([0, 4], design)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            playlist = root/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            playlist.write_bytes(minimal_mpls())
            # Single chapter fixture, styled launch button.
            design = MenuDesign('Movie', button_styles={'play': {'shape': 'pill', 'highlight': '#ff0033'}})
            install_menu(root, design, [0])
            with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                props = jar.read('menu.properties')
                self.assertIn(b'page.0.0.radius=41', props)
                self.assertIn(b'page.0.0.highlight=ff0033', props)

    def test_invalid_style_and_hidden_key_rejected(self):
        for kwargs in ({'hidden_buttons': ['bad']}, {'button_styles': {'play': {'width': 100}}},
                       {'button_styles': {'play': {'fill': 'red'}}}):
            with self.assertRaises(ValueError):
                MenuDesign('Movie', **kwargs)
