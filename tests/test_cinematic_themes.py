import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
import zipfile
from dataclasses import replace
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from disc_menu import MenuDesign, render_page, page_elements, install_menu
from menu_editor import MenuEditor
from bdmv_fixtures import minimal_mpls


class CinematicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_default_and_legacy_theme_preservation(self):
        self.assertEqual(MenuDesign('New').theme, 'amber')
        old=MenuDesign.from_dict('Old', {'theme':'graphite','hidden_text':['brand']})
        self.assertEqual(old.theme,'graphite')
        self.assertFalse(old.logo_visible)
        self.assertEqual(MenuDesign.from_dict('Old',{}).theme,'graphite')

    def test_logo_select_delete_restore_and_project_roundtrip(self):
        editor=MenuEditor();editor.resize(1200,850);editor.show();self.app.processEvents()
        try:
            self.assertEqual(editor.fullscreen_button.text(),'')
            self.assertFalse(editor.fullscreen_button.icon().isNull())
            r=editor.preview.target_rect()
            pos=QPoint(r.x()+round(1720*r.width()/1920),r.y()+round(120*r.height()/1080))
            QTest.mouseClick(editor.preview,Qt.LeftButton,pos=pos)
            self.assertEqual(editor.selected_button,'logo')
            QTest.mouseDClick(editor.preview,Qt.LeftButton,pos=pos)
            self.assertFalse(editor.preview.inline.isVisible())
            editor.change_geometry('logo',[1500,80,240,180])
            QTest.keyClick(editor.preview,Qt.Key_Delete)
            self.assertFalse(editor.design().logo_visible)
            self.assertNotIn('brand',editor.design().hidden_text)
            self.assertNotIn('logo',[e['id'] for e in editor.preview.elements])
            editor.apply_design(MenuDesign.from_dict(editor.design().title,editor.settings()))
            editor.restore_button()
            self.assertTrue(editor.design().logo_visible)
            self.assertEqual(editor.design().layout['logo'],[1500,80,240,180])
        finally:editor.close()

    def test_export_theme_without_logo_matches_preview(self):
        for theme in ('amber','sapphire'):
            with self.subTest(theme=theme),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'BDMV/PLAYLIST').mkdir(parents=True)
                (root/'BDMV/PLAYLIST/00000.mpls').write_bytes(minimal_mpls())
                design=MenuDesign('Мой Blu-ray',theme=theme,logo_visible=False)
                expected=render_page(design,[0])
                self.assertNotIn('logo',[e['id'] for e in page_elements(design,[0],0)])
                self.assertNotEqual(expected,render_page(replace(design,logo_visible=True),[0]))
                install_menu(root,design,[0])
                with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                    actual=QImage.fromData(jar.read('page0.png'))
                    self.assertEqual(actual,expected)
